import logging
import secrets
from datetime import timedelta

from sysreptor.tasks.models import TaskStatus, periodic_task
from sysreptor.utils.configuration import configuration

from . import backup_engine, destinations
from .models import BackupRun

log = logging.getLogger(__name__)

# A "running" BackupRun older than this is assumed to be from a crashed/killed process
# (e.g. the app container was restarted mid-backup) rather than a genuinely in-progress run,
# and is marked failed so new backups aren't blocked forever. Same pattern SysReptor's own
# core scheduler uses for periodic tasks (see PeriodicTaskQuerySet.get_pending_tasks).
MAX_RUNTIME = timedelta(minutes=30)


def ensure_backup_key():
    """Auto-generate the AES-256 backup encryption key on first run. No manual input required."""
    if not configuration.BACKUP_ENCRYPTION_KEY:
        key_hex = secrets.token_hex(32)
        configuration.update({'BACKUP_ENCRYPTION_KEY': key_hex})
        log.info('BackupManager: generated a new backup encryption key')


def _reap_stale_running_runs():
    from django.utils import timezone
    cutoff = timezone.now() - MAX_RUNTIME
    stale = BackupRun.objects.filter(status=BackupRun.STATUS_RUNNING, started__lt=cutoff)
    for run in stale:
        log.warning(f'BackupManager: reaping stale running BackupRun {run.id} (started {run.started}, assumed crashed)')
        run.status = BackupRun.STATUS_FAILED
        run.error_message = 'Backup process did not complete (assumed crashed - app restarted mid-run, or exceeded max runtime)'
        run.completed = timezone.now()
        run.save()


def reap_all_running_after_restore():
    """
    create_backup() snapshots the DB while its own tracking row is still status=running (the row
    is created before pg_dump runs, and only marked success/failed afterwards), so restoring an
    older backup resurrects that row as permanently "running" even though nothing is actually in
    progress - the restore path itself doesn't go through run_backup(). Any running row found
    right after a restore is therefore stale by definition, regardless of MAX_RUNTIME.
    """
    from django.utils import timezone
    running = BackupRun.objects.filter(status=BackupRun.STATUS_RUNNING)
    for run in running:
        log.info(f'BackupManager: clearing stale running BackupRun {run.id} left over from a restored snapshot')
        run.status = BackupRun.STATUS_FAILED
        run.error_message = 'Reset after restore (this run was mid-flight in the restored backup snapshot, not actually in progress)'
        run.completed = timezone.now()
        run.save()


ALL_DESTINATIONS = ('discord', 'github', 'gdrive')


def run_backup(trigger: str, selected_destinations=None) -> BackupRun:
    """
    selected_destinations: iterable of destination keys ('discord', 'github', 'gdrive') to
    actually upload to for this run, or None to mean "all" (used by the daily automatic task
    and any caller that doesn't care to filter - preserves prior behavior).
    """
    _reap_stale_running_runs()
    if BackupRun.objects.filter(status=BackupRun.STATUS_RUNNING).exists():
        raise RuntimeError('A backup is already running')

    selected = set(selected_destinations) if selected_destinations is not None else set(ALL_DESTINATIONS)
    unknown = selected - set(ALL_DESTINATIONS)
    if unknown:
        raise ValueError(f'Unknown destination(s): {", ".join(sorted(unknown))}')

    run = BackupRun.objects.create(trigger=trigger, status=BackupRun.STATUS_RUNNING)
    try:
        ensure_backup_key()
        key_hex = configuration.BACKUP_ENCRYPTION_KEY

        backup_path = backup_engine.create_backup(key_hex)
        run.size_bytes = backup_path.stat().st_size

        results = {}

        if 'discord' in selected:
            ok, detail = destinations.upload_discord(
                backup_path, backup_path.name, configuration.BACKUP_DISCORD_WEBHOOK_URL)
            results['discord'] = {'ok': ok, 'detail': detail}
        else:
            results['discord'] = {'ok': None, 'detail': 'skipped (not selected for this run)'}

        if 'github' in selected:
            ok, detail = destinations.upload_github(
                backup_path, backup_path.name,
                configuration.BACKUP_GITHUB_TOKEN, configuration.BACKUP_GITHUB_REPO, configuration.BACKUP_GITHUB_BRANCH)
            results['github'] = {'ok': ok, 'detail': detail}
        else:
            results['github'] = {'ok': None, 'detail': 'skipped (not selected for this run)'}

        if 'gdrive' in selected:
            ok, detail = destinations.upload_gdrive(
                backup_path, backup_path.name,
                configuration.BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON, configuration.BACKUP_GDRIVE_FOLDER_ID)
            results['gdrive'] = {'ok': ok, 'detail': detail}
        else:
            results['gdrive'] = {'ok': None, 'detail': 'skipped (not selected for this run)'}

        run.destination_results = results
        run.status = BackupRun.STATUS_SUCCESS
        backup_engine.prune_local_backups()
    except Exception as ex:
        log.exception('Backup run failed')
        run.status = BackupRun.STATUS_FAILED
        run.error_message = str(ex)
    finally:
        from django.utils import timezone
        run.completed = timezone.now()
        run.save()

    return run


@periodic_task(id='backupmanager_daily_backup', schedule=timedelta(days=1))
def daily_backup_task(task_info):
    if not configuration.BACKUP_DAILY_ENABLED:
        return TaskStatus.SUCCESS
    run = run_backup(trigger=BackupRun.TRIGGER_DAILY)
    return TaskStatus.SUCCESS if run.status == BackupRun.STATUS_SUCCESS else TaskStatus.FAILED
