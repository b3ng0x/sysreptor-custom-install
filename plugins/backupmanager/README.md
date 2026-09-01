# BackupManager plugin for SysReptor

Manual and daily automatic backups of the SysReptor database and uploaded files, with optional
upload to Discord, GitHub, and Google Drive, and a restore interface.

## Why this doesn't use `manage.py backup`

SysReptor's built-in `backup`/`restorebackup` management commands are gated behind a
**Professional license check** in the CLI command classes themselves (not in the underlying data
functions). This install is Community Edition with no license, so those commands fail with
`CommandError: Professional license required`.

Rather than depend on internal, license-gated, unstable-API functions to route around that check,
this plugin does its own backup: `pg_dump -Fc` for the database, a `tar` of the `/data` app-files
volume (excluding this plugin's own code/output directory), combined and encrypted independently
with AES-256-GCM. Restore is the reverse: decrypt, `pg_restore --clean --if-exists`, and re-extract
the files tar.

## Setup

Nothing to configure by hand to get local backups working - on first load the plugin
auto-generates a 256-bit AES encryption key (`BACKUP_ENCRYPTION_KEY`) and stores it via
SysReptor's configuration system (Settings page, or the `api_utils_dbconfigurationentry` table).
**Back this key up somewhere safe outside the system itself** - without it, existing backups
cannot be decrypted or restored.

To enable the plugin, add it to `ENABLED_PLUGINS` in `/opt/sysreptor/deploy/app.env`:

```
ENABLED_PLUGINS="cyberchef,renderfindings,scanimport,backupmanager"
```

and add the plugin directory to `PLUGIN_DIRS`:

```
PLUGIN_DIRS=/app/plugins/,/data/plugins
```

Then `docker compose restart app` (or `up -d`) and run migrations:

```
docker exec sysreptor-app python3 manage.py migrate
```

## Destinations (all optional, independently enabled)

Set these in `app.env` (or via the SysReptor Settings page once the plugin is loaded, which writes
them to the database) - the backup UI shows which are currently configured.

| Setting | Purpose |
|---|---|
| `BACKUP_DAILY_ENABLED` | `true`/`false`, defaults to `true` |
| `BACKUP_DISCORD_WEBHOOK_URL` | Discord webhook URL. Files over Discord's ~25MB attachment limit get a notification only, not the file. |
| `BACKUP_GITHUB_TOKEN` | GitHub personal access token with `repo` (contents write) scope |
| `BACKUP_GITHUB_REPO` | `owner/repo` to push backups into |
| `BACKUP_GITHUB_BRANCH` | defaults to `main` |
| `BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON` | Full JSON key of a Google service account with Drive API access |
| `BACKUP_GDRIVE_FOLDER_ID` | Drive folder ID the service account can write to |

GitHub "version override": each run writes both a timestamped file
(`backups/backup-<timestamp>.tar.enc`) and overwrites `backups/latest.tar.enc` - git history on the
`latest` file gives you every prior version via `git log`, while the path always points at the
newest backup.

## Using it

Open the "Backups" item in the main menu (added by this plugin). From there:
- **Run manual backup now** - triggers a backup synchronously and shows the result.
- **History** - every run, its destinations' success/failure, and any error.
- **Local backups** - the last 7 backups are kept on local disk (older ones pruned automatically);
  click Restore to restore straight from one of these.
- **Restore from uploaded file** - upload an `.tar.enc` backup (e.g. one pulled back down from
  GitHub/Drive) and restore from it, optionally with a different AES key than the server's current
  one (useful for restoring a backup taken before a key rotation).

All of this is also available directly via the REST API at `/api/plugins/<plugin_id>/api/runs/...`
if you want to script it (e.g. `curl` from an external cron as a belt-and-suspenders check that
the in-app daily job actually ran).

**Restore is destructive** - it overwrites the live database and files. Only superusers can trigger
it (enforced server-side), and the UI requires a confirmation click.

## How daily scheduling works

SysReptor has no separate worker/cron process - its own Docker healthcheck
(`/api/public/utils/healthcheck/`, polled every 30s per `docker-compose.yml`) opportunistically
runs any due periodic tasks after a successful check. This plugin registers
`backupmanager_daily_backup` via the same `@periodic_task(schedule=timedelta(days=1))` mechanism
SysReptor's own core code uses (e.g. `clear_sessions`) - no extra cron container needed, and it
fires automatically as long as the app container is up and its healthcheck is being polled.

## Known limitations

- Manual backup runs synchronously in the request - for very large installs this could be slow
  (gunicorn's worker timeout is 3600s per SysReptor's own `start.sh`, so it has headroom, but the
  browser tab needs to stay open until it finishes).
- Only one backup can run at a time (enforced - a second trigger while one is running returns
  HTTP 409).
