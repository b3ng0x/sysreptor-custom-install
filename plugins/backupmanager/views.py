import base64
import json
import logging
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from sysreptor.utils.configuration import configuration, reload_server

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
        return self._do_restore(path, request)

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
            return self._do_restore(tmp_path, request)
        finally:
            tmp_path.unlink(missing_ok=True)

    @action(detail=False, methods=['get'], url_path='recovery-key')
    def recovery_key(self, request):
        # This is deliberately NEVER included in create_backup()'s archive or uploaded to any
        # destination (Discord/GitHub/GDrive) - see backup_engine.create_backup's meta.json
        # comment. Putting the key that decrypts data-at-rest inside the encrypted-at-rest backup
        # would partly defeat the point of encrypting it. Restoring this backup's database onto a
        # different (or rebuilt) instance produces a database that instance cannot read unless
        # its own ENCRYPTION_KEYS already contains the key(s) below - so this file is the other,
        # separate half of that secret and must be stored somewhere independent of the backups
        # themselves (e.g. a password manager), not next to them.
        keys = [
            {
                'id': k.id,
                'key': base64.b64encode(k.key).decode(),
                'cipher': k.cipher.value if hasattr(k.cipher, 'value') else str(k.cipher),
                'revoked': k.revoked,
            }
            for k in settings.ENCRYPTION_KEYS.values()
        ]
        payload = {
            'ENCRYPTION_KEYS': keys,
            'DEFAULT_ENCRYPTION_KEY_ID': settings.DEFAULT_ENCRYPTION_KEY_ID,
            'note': (
                'This file does NOT decrypt backup archives from this plugin (those use a '
                'separate BACKUP_ENCRYPTION_KEY, shown on the Backups page). It contains the '
                'key(s) SysReptor itself uses to read encrypted database columns (user '
                'passwords, notebook text, finding data, comments). A database backup restored '
                'onto an instance whose own ENCRYPTION_KEYS does not include the id(s) below '
                'will restore successfully but be unreadable - logins will fail with '
                'CryptoError. Store this file separately from your backups, and re-download a '
                'fresh copy whenever ENCRYPTION_KEYS changes (rotation, fresh install).'
            ),
        }
        data = json.dumps(payload, indent=2).encode()
        resp = HttpResponse(data, content_type='application/json')
        resp['Content-Disposition'] = 'attachment; filename="sysreptor-recovery-key.json"'
        resp['Content-Length'] = str(len(data))
        return resp

    @action(detail=False, methods=['post'], url_path='apply-recovery-key')
    def apply_recovery_key(self, request):
        """
        Lets an admin paste a recovery-key export (this plugin's own "Download recovery key"
        format) directly into the restore UI and have it take effect immediately - no manual
        app.env edit, no container recreation. See backup_engine.apply_recovery_keys for how and
        why this works (and the trade-off it accepts) and CONTEXT.md for the incident that
        motivated it. Usable standalone (e.g. to fix a restore done in an earlier session without
        re-uploading the backup file again), and also wired into _do_restore below via the
        `recovery_key` field so it can be supplied in the same request as the restore itself.
        """
        try:
            payload = request.data.get('recovery_key') if 'recovery_key' in request.data else request.data
            if isinstance(payload, str):
                payload = json.loads(payload)
            applied_ids = backup_engine.apply_recovery_keys(payload)
        except backup_engine.BackupError as ex:
            return Response({'detail': str(ex)}, status=status.HTTP_400_BAD_REQUEST)
        except (json.JSONDecodeError, TypeError) as ex:
            return Response({'detail': f'Recovery key: invalid JSON ({ex})'}, status=status.HTTP_400_BAD_REQUEST)

        # Propagate to every worker process in this container, not just the one handling this
        # request - same reasoning as in restore_backup's own post-database-restore cache clear.
        configuration.clear_cache()
        reload_server()

        return Response({'detail': 'recovery key applied', 'applied_key_ids': applied_ids})

    def _do_restore(self, path, request):
        data = request.data
        key_hex = data.get('key') or configuration.BACKUP_ENCRYPTION_KEY
        skip_database = str(data.get('skip_database', '')).lower() in ('1', 'true')
        skip_files = str(data.get('skip_files', '')).lower() in ('1', 'true')
        recovery_key_applied = None
        raw_recovery_key = data.get('recovery_key')
        if raw_recovery_key:
            try:
                payload = json.loads(raw_recovery_key) if isinstance(raw_recovery_key, str) else raw_recovery_key
                recovery_key_applied = backup_engine.apply_recovery_keys(payload)
                # restore_backup() below only reload_server()s when it actually replaces the
                # database (not on a files-only restore) - do it unconditionally here so a
                # recovery key supplied alongside a files-only restore still reaches every worker
                # process, not just this one.
                configuration.clear_cache()
                reload_server()
            except backup_engine.BackupError as ex:
                return Response({'detail': str(ex)}, status=status.HTTP_400_BAD_REQUEST)
            except (json.JSONDecodeError, TypeError) as ex:
                return Response({'detail': f'Recovery key: invalid JSON ({ex})'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            # A full database restore replaces the sessions table too, so the row backing *this*
            # request's session is gone by the time Django's SessionMiddleware tries to save it
            # at the end of the request - that save is an UPDATE against a now-vanished row,
            # which Django refuses (SessionInterrupted -> opaque 400), masking the fact the
            # restore itself already succeeded. request.session.flush() tells Django this
            # session is intentionally gone, so the middleware issues a fresh INSERT into the
            # restored DB instead of a doomed UPDATE, letting the response below return normally.
            # The admin is genuinely logged out either way once the database is actually replaced
            # (the user table just changed too) - this only changes whether that shows up as a
            # clean re-login prompt or a misleading 400 that looks like the restore itself
            # failed. Passed as a hook (not called upfront) so a restore that fails validation
            # (bad key, corrupt archive) before ever reaching the database doesn't needlessly
            # log the admin out for nothing.
            result = backup_engine.restore_backup(
                path, key_hex, skip_database=skip_database, skip_files=skip_files,
                pre_db_restore_hook=request.session.flush)
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

        response_data = {'detail': 'restore completed'}
        if recovery_key_applied is not None:
            response_data['recovery_key_applied'] = recovery_key_applied
        if not skip_database:
            response_data['encryption_key_check'] = result
            if result.get('key_available') is False:
                response_data['warning'] = (
                    f"This backup's data was encrypted with key id "
                    f"'{result.get('backup_encryption_key_id')}', which is not present in this "
                    "instance's ENCRYPTION_KEYS. The restore completed, but logins and any "
                    "encrypted field (passwords, notes, findings, comments) will fail with "
                    "CryptoError until that key is recovered. Paste the source instance's "
                    "downloaded recovery-key file into the \"Recovery key\" field and restore "
                    "again (or use \"Apply recovery key\" on its own, without re-uploading the "
                    "backup) - no manual app.env edit needed."
                )
        return Response(response_data)
