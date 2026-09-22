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
        # NOTE: StringField defaults to required=True. Every field below is legitimately meant
        # to be left blank (that's how each destination gets "disabled" - see get_frontend_settings
        # and destinations.py, which all treat '' as "not configured", not an error). Without an
        # explicit required=False, SysReptor's shared Settings page form treats these as required
        # across the WHOLE configuration form, refusing to save ANY setting (not just ours) unless
        # every single one of these is filled in - e.g. trying to save only a Discord webhook URL
        # was blocked until GitHub/Google Drive fields were also filled in, even though they're
        # unrelated and meant to stay empty.
        StringField(
            id='BACKUP_ENCRYPTION_KEY',
            default='',
            required=False,
            help_text='Hex-encoded 256-bit AES key used to encrypt backups. Auto-generated on first run if empty. '
                       'KEEP THIS SAFE - it is required to restore any backup.'),
        BooleanField(
            id='BACKUP_DAILY_ENABLED',
            default=True,
            help_text='Whether the daily automatic backup is enabled.'),
        StringField(
            id='BACKUP_DISCORD_WEBHOOK_URL',
            default='',
            required=False,
            help_text='Discord webhook URL to post backup notifications/uploads to. Leave empty to disable. '
                       'Note: Discord webhooks reject files over 25MB (or higher with server boosts); '
                       'large backups will only send a notification, not the file itself.'),
        StringField(
            id='BACKUP_GITHUB_TOKEN',
            default='',
            required=False,
            help_text='GitHub personal access token with repo (contents:write) permission. Leave empty to disable.'),
        StringField(
            id='BACKUP_GITHUB_REPO',
            default='',
            required=False,
            help_text='GitHub repo in "owner/repo" format to push backups to. Leave empty to disable.'),
        StringField(
            id='BACKUP_GITHUB_BRANCH',
            default='main',
            required=False,
            help_text='Branch to commit backups to.'),
        StringField(
            id='BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON',
            default='',
            required=False,
            help_text='Full contents of a Google service account JSON key with Drive API access. Leave empty to disable.'),
        StringField(
            id='BACKUP_GDRIVE_FOLDER_ID',
            default='',
            required=False,
            help_text='Google Drive folder ID to upload backups into (the service account must have access to it).'),
    ])

    def ready(self) -> None:
        log.info('Loading BackupManager plugin...')
        from . import backup_engine, tasks  # noqa
        # Key generation is deferred to first actual use (run_backup) rather than done here -
        # querying/writing the DB during AppConfig.ready() triggers Django's
        # "Accessing the database during app initialization is discouraged" warning.

        # Re-apply any encryption keys recovered via the restore UI's "recovery key" field in a
        # past session - ready() runs fresh in every worker process (including new ones spawned
        # by a reload_server() SIGHUP), so this is what makes a previously-recovered key keep
        # working across restarts/reloads, not just for the one process that originally received
        # it. Pure filesystem read, no DB access, safe to do here.
        backup_engine.load_recovered_encryption_keys()

    def get_frontend_settings(self, request):
        return {
            'daily_enabled': configuration.BACKUP_DAILY_ENABLED,
            'discord_configured': bool(configuration.BACKUP_DISCORD_WEBHOOK_URL),
            'github_configured': bool(configuration.BACKUP_GITHUB_TOKEN and configuration.BACKUP_GITHUB_REPO),
            'gdrive_configured': bool(configuration.BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON),
        }
