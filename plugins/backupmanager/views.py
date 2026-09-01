import logging
from pathlib import Path

from django.http import HttpResponse
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from sysreptor.utils.configuration import configuration

from . import backup_engine, tasks
from .models import BackupRun
from .serializers import BackupRunSerializer

log = logging.getLogger(__name__)


class IsSuperuser(permissions.BasePermission):
    """Backup/restore can read and overwrite the entire system - superuser only."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class BackupRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = BackupRun.objects.all()
    serializer_class = BackupRunSerializer
    permission_classes = [IsSuperuser]

    @action(detail=False, methods=['post'])
    def trigger(self, request):
        # destinations: list like ["discord","gdrive"], or omitted/"all" to use every configured
        # destination (unchanged default behavior).
        raw = request.data.get('destinations')
        selected = None
        if raw not in (None, 'all', ['all']):
            selected = raw if isinstance(raw, list) else [raw]
        try:
            run = tasks.run_backup(trigger=BackupRun.TRIGGER_MANUAL, selected_destinations=selected)
        except RuntimeError as ex:
            return Response({'detail': str(ex)}, status=status.HTTP_409_CONFLICT)
        except ValueError as ex:
            return Response({'detail': str(ex)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_201_CREATED if run.status == BackupRun.STATUS_SUCCESS else status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response(BackupRunSerializer(run).data, status=code)

    @action(detail=False, methods=['post'], url_path='clear-local')
    def clear_local(self, request):
        files = backup_engine.list_local_backups()
        deleted = []
        for f in files:
            f.unlink(missing_ok=True)
            deleted.append(f.name)
        return Response({'deleted': deleted, 'count': len(deleted)})

    @action(detail=False, methods=['get'], url_path='download-local')
    def download_local(self, request):
        filename = request.query_params.get('filename')
        if not filename:
            return Response({'detail': 'filename required'}, status=status.HTTP_400_BAD_REQUEST)
        path = (backup_engine.BACKUPS_DIR / filename).resolve()
        if backup_engine.BACKUPS_DIR.resolve() not in path.parents or not path.is_file():
            return Response({'detail': 'invalid filename'}, status=status.HTTP_400_BAD_REQUEST)
        # Plain in-memory response, not FileResponse/StreamingHttpResponse: backups here are at
        # most a few MB, and Django's sync file-iterator streaming doesn't play reliably with
        # this app's ASGI/Uvicorn server (observed causing "StreamingHttpResponse must consume
        # synchronous iterators..." warnings and browser-side connection resets mid-download).
        data = path.read_bytes()
        resp = HttpResponse(data, content_type='application/octet-stream')
        resp['Content-Disposition'] = f'attachment; filename="{path.name}"'
        resp['Content-Length'] = str(len(data))
        return resp

    @action(detail=False, methods=['get'])
    def local_backups(self, request):
        files = backup_engine.list_local_backups()
        return Response([
            {'filename': f.name, 'size_bytes': f.stat().st_size, 'modified': f.stat().st_mtime}
            for f in files
        ])

    @action(detail=False, methods=['get'])
    def status_summary(self, request):
        return Response({
            'daily_enabled': configuration.BACKUP_DAILY_ENABLED,
            'discord_configured': bool(configuration.BACKUP_DISCORD_WEBHOOK_URL),
            'github_configured': bool(configuration.BACKUP_GITHUB_TOKEN and configuration.BACKUP_GITHUB_REPO),
            'gdrive_configured': bool(configuration.BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON),
            'backup_running': BackupRun.objects.filter(status=BackupRun.STATUS_RUNNING).exists(),
            'local_backup_count': len(backup_engine.list_local_backups()),
        })

    @action(detail=False, methods=['post'], url_path='restore-local')
    def restore_local(self, request):
        filename = request.data.get('filename')
        if not filename:
            return Response({'detail': 'filename required'}, status=status.HTTP_400_BAD_REQUEST)
        path = (backup_engine.BACKUPS_DIR / filename).resolve()
        if backup_engine.BACKUPS_DIR.resolve() not in path.parents or not path.is_file():
            return Response({'detail': 'invalid filename'}, status=status.HTTP_400_BAD_REQUEST)
        return self._do_restore(path, request.data)

    @action(detail=False, methods=['post'], url_path='restore-upload')
    def restore_upload(self, request):
        upload = request.FILES.get('file')
        if not upload:
            return Response({'detail': 'file required'}, status=status.HTTP_400_BAD_REQUEST)
        tmp_path = Path('/tmp') / f'restore-upload-{upload.name}'
        with open(tmp_path, 'wb') as f:
            for chunk in upload.chunks():
                f.write(chunk)
        try:
            return self._do_restore(tmp_path, request.data)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _do_restore(self, path, data):
        key_hex = data.get('key') or configuration.BACKUP_ENCRYPTION_KEY
        skip_database = str(data.get('skip_database', '')).lower() in ('1', 'true')
        skip_files = str(data.get('skip_files', '')).lower() in ('1', 'true')
        try:
            backup_engine.restore_backup(path, key_hex, skip_database=skip_database, skip_files=skip_files)
            if not skip_database:
                # The restored DB snapshot may contain the backup run's own tracking row still
                # marked "running" (see reap_all_running_after_restore docstring) - clear it so a
                # restored-from-old-backup instance doesn't get permanently stuck blocking new runs.
                tasks.reap_all_running_after_restore()
        except backup_engine.BackupError as ex:
            return Response({'detail': str(ex)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as ex:
            log.exception('Restore failed')
            return Response({'detail': f'Restore failed: {ex}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'detail': 'restore completed'})
