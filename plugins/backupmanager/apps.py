import logging

from sysreptor.plugins import BooleanField, FieldDefinition, PluginConfig, StringField, configuration

log = logging.getLogger(__name__)


class BackupManagerConfig(PluginConfig):
    """
    Backup Manager: manual and daily automatic backups of the SysReptor database and files,
    with optional upload to Discord, GitHub, and Google Drive, plus restore support.

    NOTE ON LICENSING: SysReptor's built-in `manage.py backup`/`restorebackup` commands require
    a Professional license (checked in the CLI Command classes, not in the underlying data-layer
    functions). Since this is a Community Edition install with no license, this plugin does NOT
    call those gated commands or their internal functions. Instead it performs its own clean-room
    backup using `pg_dump`/`pg_restore` for the database and a tar of the app-data volume for
    files, encrypted independently with AES-256-GCM. This avoids depending on internal,
    license-gated code paths.
    """

    plugin_id = 'f413e156-1b86-4b57-a959-427413861cab'

    configuration_definition = FieldDefinition(fields=[
        StringField(
            id='BACKUP_ENCRYPTION_KEY',
            default='',
            help_text='Hex-encoded 256-bit AES key used to encrypt backups. Auto-generated on first run if empty. '
                       'KEEP THIS SAFE - it is required to restore any backup.'),
        BooleanField(
            id='BACKUP_DAILY_ENABLED',
            default=True,
            help_text='Whether the daily automatic backup is enabled.'),
        StringField(
            id='BACKUP_DISCORD_WEBHOOK_URL',
            default='',
            help_text='Discord webhook URL to post backup notifications/uploads to. Leave empty to disable. '
                       'Note: Discord webhooks reject files over 25MB (or higher with server boosts); '
                       'large backups will only send a notification, not the file itself.'),
        StringField(
            id='BACKUP_GITHUB_TOKEN',
            default='',
            help_text='GitHub personal access token with repo (contents:write) permission. Leave empty to disable.'),
        StringField(
            id='BACKUP_GITHUB_REPO',
            default='',
            help_text='GitHub repo in "owner/repo" format to push backups to. Leave empty to disable.'),
        StringField(
            id='BACKUP_GITHUB_BRANCH',
            default='main',
            help_text='Branch to commit backups to.'),
        StringField(
            id='BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON',
            default='',
            help_text='Full contents of a Google service account JSON key with Drive API access. Leave empty to disable.'),
        StringField(
            id='BACKUP_GDRIVE_FOLDER_ID',
            default='',
            help_text='Google Drive folder ID to upload backups into (the service account must have access to it).'),
    ])

    def ready(self) -> None:
        log.info('Loading BackupManager plugin...')
        from . import tasks  # noqa
        # Key generation is deferred to first actual use (run_backup) rather than done here -
        # querying/writing the DB during AppConfig.ready() triggers Django's
        # "Accessing the database during app initialization is discouraged" warning.

    def get_frontend_settings(self, request):
        return {
            'daily_enabled': configuration.BACKUP_DAILY_ENABLED,
            'discord_configured': bool(configuration.BACKUP_DISCORD_WEBHOOK_URL),
            'github_configured': bool(configuration.BACKUP_GITHUB_TOKEN and configuration.BACKUP_GITHUB_REPO),
            'gdrive_configured': bool(configuration.BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON),
        }
