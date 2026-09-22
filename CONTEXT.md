# Project context (for feeding to Claude on a new machine)

This file is not user-facing documentation (see `README.md` for that). It's a handoff brief:
paste or attach this file at the start of a new Claude Code session on a different machine and
Claude will have the full picture - what this project is, why it exists, what's already been
built and tested, and every non-obvious bug that was found and fixed along the way, so it doesn't
have to re-derive any of it from scratch.

## What this is

[SysReptor](https://docs.sysreptor.com/) is a pentest-report-writing platform, self-hosted via
the official Docker Compose setup. This repo (`sysreptor-custom-install`) is **not a fork** -
it's an installer plus two custom plugins that layer on top of the official, unmodified
SysReptor Community Edition image, so upgrading SysReptor itself stays on the normal upstream
path. It exists because Community Edition (free, no license) is missing two things the owner
wanted: a working backup/restore system (CE's own backup commands are Professional-license-gated)
and app-wide raw CSS theming (CE's built-in theming plugin only covers JSON theme *variables*,
not arbitrary CSS - e.g. it can't restyle the markdown editor toolbar).

Everything here was built, tested (including with Selenium against a real running instance,
headless Chromium), and iterated on a live single-server deployment before being packaged into
this installable form. The live deployment also has a **complete, from-scratch reinstall**
performed against this exact repo to validate it actually reproduces a working instance
end-to-end (not just "worked on the machine it was written on").

## Architecture, and why it looks the way it does

- **Official image, official `docker-compose.yml`/`app.env` layout.** `install.sh` downloads the
  same release tarball the official `docs.sysreptor.com/install.sh` uses, and only *adds* to
  `app.env` (`ENABLED_PLUGINS`, `PLUGIN_DIRS`) rather than replacing anything. Plugins are copied
  into the `sysreptor-app-data` Docker volume's `plugins/` directory, which the app already scans
  via `PLUGIN_DIRS=/app/plugins/,/data/plugins`.
- **Why a clean-room backup engine instead of `manage.py backup`/`restorebackup`.** Those two
  commands exist in SysReptor core but are gated behind a Professional license check *in the CLI
  command classes*, not in the underlying data layer - so `backupmanager` doesn't call them at
  all. Instead it does its own `pg_dump`/`pg_restore` against the same Postgres instance, plus a
  tar of the `/data` volume, combined into one archive and encrypted independently with
  AES-256-GCM (its own key, `BACKUP_ENCRYPTION_KEY`, unrelated to SysReptor's own field-encryption
  keys - see the encryption-key incident below, this distinction matters a lot).
- **Why the CSS plugin exists alongside SysReptor's built-in `customizetheme`.** That plugin only
  exposes a fixed set of Vuetify theme *variables* (colors, etc.) as JSON. It has no way to reach
  arbitrary component internals - e.g. the report markdown editor's toolbar icons render as
  invisible-on-invisible in some color combinations, which is a CSS-specificity problem no theme
  variable fixes. `customcss` injects raw CSS app-wide, including into isolated `srcdoc` preview
  iframes (which have zero stylesheet inheritance and need separate injection).

## The two plugins

### `backupmanager`

Manual/daily automatic backups (full database + entire `/data` volume - projects, templates,
designs, notes, uploaded assets, everything, not just "projects"), optional upload to Discord/
GitHub/Google Drive, local retention with download/clear, and a restore UI (from a local backup or
an uploaded file). Full usage docs: `plugins/backupmanager/README.md`.

Config (all via SysReptor's own DB-backed `configuration` system, editable from the plugin's
Settings-page fields - every field is `required=False` deliberately, see the comment in
`apps.py` about why):

- `BACKUP_ENCRYPTION_KEY` - auto-generated on first use, AES-256 key for the backup archive itself.
- `BACKUP_DAILY_ENABLED`, `BACKUP_DISCORD_WEBHOOK_URL`, `BACKUP_GITHUB_TOKEN`/`_REPO`/`_BRANCH`,
  `BACKUP_GDRIVE_SERVICE_ACCOUNT_JSON`/`_FOLDER_ID`.

### `customcss`

Lets a superuser paste raw CSS (or a JSON theme-preset-like structure) and preview it live before
committing, in both light and dark mode, app-wide. Ships one preset, `presets/cyberpunk-neon.css`,
applied automatically by `install.sh`. Full usage docs: `plugins/customcss/README.md`.

## Incident history - read this before touching backup/restore code again

These were all found through real use (not code review), each took real debugging to root-cause,
and the fixes are already live + in this repo. Knowing *why* each fix exists prevents
re-introducing the same bug from a different angle.

1. **Restore ordering (files vs. database).** Originally restored the database first. A
   file-extraction failure *after* a successful DB replace left the system in a broken hybrid
   state (new DB, old files/encryption context) that was hard to recover from. Fixed: files
   first, then the database (the true point of no return) - see the comment in
   `backup_engine.restore_backup`.
2. **`/data` volume root ownership.** A freshly created Docker volume mounts `root:root`. Only
   chowning the `plugins/` subdirectory (not the whole volume root) left the app (running as uid
   1000) unable to create new top-level directories later, e.g. `uploadedassets/` on first file
   upload or restore - failed with an opaque "Permission denied" nowhere near the real cause.
   Fixed in both the live deploy and `install.sh` (chowns the whole volume root).
3. **CE Settings-page multi-field validation bug (vendor bug, not ours).** SysReptor's shared
   Settings save form validates *all* fields with `required=True` together, even unrelated ones
   from other plugins - so leaving `StringField`s at their Django default (`required=True`)
   blocked saving *any* single setting until every field across the whole page was filled in.
   This is why every `StringField` in `backupmanager/apps.py` explicitly sets `required=False`.
4. **`pg_dump`/`pg_restore` version mismatches.** The app image's bundled `pg_dump` client can be
   a different major version than the actual Postgres server. A newer client dumping an older
   server emits a `SET transaction_timeout = 0;` preamble the older server rejects outright
   (hard abort) - fixed with defensive line-filtering on replay (see `_INCOMPATIBLE_SET_PATTERNS`
   in `backup_engine.py`). Also pinned `SYSREPTOR_POSTGRES_VERSION=17` in `install.sh` to match
   the app image's actual bundled client version - **re-verify this after any SysReptor version
   upgrade** via `docker exec sysreptor-app pg_dump --version`.
5. **FileResponse/StreamingHttpResponse broke under Uvicorn.** Backup downloads using Django's
   streaming response classes caused browser-side connection resets under this app's ASGI/Uvicorn
   server. Fixed by switching to a plain in-memory `HttpResponse` (backups here are at most a few
   MB, streaming was never actually needed).
6. **The big one - encryption keys are NOT part of the backup, by design, and that's a trap.**
   SysReptor encrypts some database columns (user passwords, notebook text, finding data,
   comments) with `ENCRYPTION_KEYS`, an environment variable in `app.env` **on the host**, not a
   database row. A `backupmanager` backup contains a `pg_dump` + a files tar - it does **not**
   contain `app.env`, deliberately (baking the data-at-rest key into the encrypted-at-rest backup
   would partly defeat the point of encrypting it). So: restoring a backup onto a **different or
   rebuilt** instance replaces the database successfully, but that instance's own
   `ENCRYPTION_KEYS` doesn't contain the key the data was sealed with. The failure then surfaces
   at the worst possible place - login - as an opaque `CryptoError` → HTTP 500, which looks
   exactly like "wrong password" even when the credentials are correct. Fixed with three things,
   all live and in this repo:
   - `create_backup()` now records the encryption key id (not the key itself - just its id, safe
     to store unencrypted) used at backup time in the archive's `meta.json`.
   - `restore_backup()` compares that id against the *target* instance's own `ENCRYPTION_KEYS`
     and returns a compatibility result; the restore API response includes an explicit warning
     when they don't match, instead of staying silent until the next failed login.
   - A **"Download recovery key"** button/endpoint (`GET .../runs/recovery-key/`) exports
     `ENCRYPTION_KEYS` + `DEFAULT_ENCRYPTION_KEY_ID` on demand - deliberately **never** bundled
     into a backup archive or auto-uploaded anywhere. This is the other half of a two-part
     secret; store it separately from your backups (e.g. a password manager), and re-download it
     whenever the key rotates. **If you only take away one thing from this file: a
     `backupmanager` backup restored onto a machine that doesn't already have the source
     instance's `ENCRYPTION_KEYS` will restore "successfully" but be unreadable. Save the
     recovery key file *now*, before you need it.**
7. **A second, compounding bug found in the same incident: stale post-restore config cache.**
   `restore_backup()` replaces the whole database via a raw `pg_restore`/`psql` replay - entirely
   outside Django's `configuration.update()`, which is the *only* thing that normally clears
   `sysreptor.utils.configuration`'s process-local, unbounded `functools.cache` of DB-backed
   config values (including `BACKUP_ENCRYPTION_KEY`). Left uncleared, the running process kept
   serving the **pre-restore** `BACKUP_ENCRYPTION_KEY` even though the row underneath had just
   changed - so the very next automatic backup after a restore was silently encrypted with a key
   that no longer matched what was actually configured, producing an undecryptable backup minutes
   after a successful restore. Reproduced directly (mutated the DB row underneath the cache,
   confirmed a stale read, confirmed clearing the cache fixes it) and fixed by having
   `restore_backup()` call `configuration.clear_cache()` + `reload_server()` (SIGHUP to gunicorn)
   right after the database replace - the exact same pattern SysReptor's own Settings-save
   endpoint already uses for this class of problem (`api_utils/views.py
   ConfigurationViewSet.patch`).
8. **The misleading HTTP 400 on restore.** A full database restore replaces the sessions table
   too, so the row backing *the request currently performing the restore* is gone by the time
   Django's `SessionMiddleware` tries to save it at the end of the request - an `UPDATE` against a
   vanished row, which Django refuses (`SessionInterrupted` → generic 400). This looked exactly
   like "the restore failed," when the restore had actually already completed successfully
   milliseconds earlier. Fixed by flushing the session via a hook (`pre_db_restore_hook`) fired
   only at the actual point of no return (immediately before the database replace, not
   pre-emptively at request start) - so a restore that fails validation *before* touching the
   database (bad key, corrupt archive) doesn't needlessly log the admin out, but a real full-DB
   restore now returns a clean 200 instead of a cosmetic 400. The admin genuinely is logged out
   either way once the database is actually replaced (the user table just changed too) - the fix
   only changes whether that shows up as a clean re-login prompt or a misleading error.
9. **CSRF token missing on mutating plugin actions.** `SessionAuthentication.enforce_csrf()`
   requires the `X-CSRFToken` header on unsafe methods for real cookie-based browser sessions -
   missing entirely from both plugins' frontend `fetch()` calls, so every real user hit "CSRF
   Failed: CSRF token missing" on trigger/restore/clear-local/save actions. This went unnoticed
   during development because testing used Bearer API tokens, which bypass CSRF entirely -
   exactly the kind of gap that doesn't show up until a real browser session hits it. Fixed by
   reading the `csrftoken` cookie and attaching it as `X-CSRFToken` on every non-GET `fetch()` in
   both plugins' `index.html`.
10. **Backup-config "all or nothing" save bug.** Same root cause as #3 (CE's shared Settings-page
    validation), rediscovered when trying to configure *only* the Discord webhook and getting
    blocked with "This field may not be blank" on the unrelated GitHub/Google Drive fields.
11. **Theme preset readability regression.** The Cyberpunk Neon preset made the Notes/Report
    markdown editor toolbar and preview text unreadable in light mode (and partially in dark
    mode) - a straightforward color-contrast bug in the preset CSS, fixed and re-verified with
    Selenium screenshots in both themes.
12. **Follow-up to #6: closing the loop so recovering a key doesn't require a manual `app.env`
    edit.** #6 gave the admin a clear warning and a recovery-key export, but actually *using* that
    export still meant SSHing into the host, hand-editing `app.env`, and recreating the container -
    exactly the kind of step a GUI restore flow shouldn't require. The blocker: `ENCRYPTION_KEYS`
    is a plain OS environment variable, fixed for the life of the container - the app has no
    filesystem access to `app.env` on the host at all, and even a graceful `reload_server()` just
    respawns workers with that same fixed environment (confirmed: editing `app.env` and sending
    SIGHUP does *not* pick up the change; only a full `docker compose up -d` re-reads the
    `env_file`). The fix uses the one piece of mutable, persistent storage the app *can* reach at
    runtime: a pasted recovery-key export now gets written to
    `/data/backupmanager_recovered_encryption_keys.json` (deliberately outside
    `plugins/backupmanager/`, which `install.sh` wipes and redeploys on every update) and merged
    into `settings.ENCRYPTION_KEYS` in three places - immediately, in-process, for the request that
    received it; on every subsequent app startup/reload via `BackupManagerConfig.ready()`; and
    propagated to every other worker in the container via the same `clear_cache()` +
    `reload_server()` pattern from #7. Verified end-to-end against a real cross-instance restore
    (see below) including survival across a genuine container restart, not just a graceful reload.
    **Known sharp edge, by design, not a bug:** the standalone "Apply recovery key only" button
    requires an authenticated superuser session - which is exactly what's broken if the restore
    affected *your own* account. It only helps if you can still reach the page some other way. The
    field on the restore form itself doesn't have this problem: paste the recovery key there
    *before* clicking Restore and it's applied in the same request, before the database (and the
    current session) gets replaced. This is a deliberate, narrow softening of #6's "two-part
    secret, kept apart" design - a key only ever enters the ring this way if a human explicitly
    pastes it in during recovery, never automatically from a backup/restore.
    - **Real-world validation of this whole feature**, done as one exercise: took a genuinely old
      backup (`format_version: 1`, predating item #6 entirely, so no key id was ever recorded in
      it), restored it onto a from-scratch second instance (separate containers/volumes/network -
      `sysreptor-test2-*`), confirmed the exact real failure (`CryptoError: No key with id=
      9b00d4ba-92a4-4721-8638-16d869efa811 in ENCRYPTION_KEYS` - via the real `/api/v1/auth/login/`
      endpoint, HTTP 500, not just an ORM-level check), then recovered the actual source key
      material for that id and confirmed: (a) manually editing `app.env` + `docker compose up -d`
      fixes it (the old, and still-available, manual path), and (b) pasting the same recovery key
      into the restore form's new field, in the *same* restore-upload request as the backup file
      itself, fixes it identically with zero manual host access - a clean `400 Invalid username or
      password` on a deliberately-wrong-password login attempt (proving decryption now succeeds,
      only credential-matching correctly fails), surviving both a `reload_server()` and a full
      container restart.

## Known limitations (unchanged from README, repeated here for completeness)

- Community Edition only: single superuser, one active API token per user, no
  `SYSREPTOR_LICENSE` wiring in `install.sh` (PRs welcome if that's ever needed).
- No apt-style upgrade path for the plugins - they're files in a Docker volume, versioned only by
  this git repo. Re-run `install.sh` to pick up plugin updates on an existing install.
- Static-file edits to a *running* instance can serve stale content for up to ~60s afterward - a
  SysReptor worker-recycle/caching quirk, not a plugin bug. Not an issue for a fresh install.
- Does not import HTB exam demo data - that's `manage.py importdemodata`, a separate one-time step
  covered in SysReptor's own HTB docs.

## How this was tested

- Every plugin change was exercised through the real HTTP API (superuser Bearer token created
  ad-hoc in a Django shell, always in one shell invocation since shell state doesn't persist
  between separate tool calls) before being declared fixed.
- UI changes were verified with Selenium against headless Chromium
  (`/usr/bin/chromium` + `/usr/bin/chromedriver` on the dev box), authenticating either through
  the real login form (`name="username"`/`name="password"`, `[data-testid="login-submit"]`) or by
  injecting a session directly (`import_module(settings.SESSION_ENGINE).SessionStore` - **must**
  use the app's configured session engine, not `django.contrib.sessions.backends.db` directly;
  SysReptor's session backend hashes the key differently and a mismatched store silently produces
  an unauthenticated session).
- Plugin static pages are served at `/static/plugins/<plugin_id>/index.html` directly (this is
  also how to load one in a browser/Selenium test without fighting the outer Nuxt SPA shell's
  routing).
- The full install → use → **delete everything** → reinstall from this repo → verify cycle was
  run at least once to catch "works because of leftover state on the dev machine" bugs.

## Repo layout

```
install.sh                  the non-interactive installer (see README.md for what it does)
CONTEXT.md                  this file
README.md                   user-facing install/usage docs
plugins/backupmanager/      full plugin source + its own README.md
plugins/customcss/          full plugin source + its own README.md
presets/cyberpunk-neon.css  the theme CSS applied automatically on install
```

## If you're picking this up on a new machine right now

1. Read `README.md` for the actual install command.
2. If you're restoring a backup taken on a *different* instance (migration, disaster recovery),
   read incident #6 above first, and get the source instance's recovery-key export (or its
   `app.env` `ENCRYPTION_KEYS` value) before you need it, not after.
3. Nothing here requires touching SysReptor's own source - if you find yourself wanting to patch
   something under `/app/api/src/sysreptor/`, that's a sign the fix belongs in a plugin instead
   (or is a genuine upstream bug worth reporting, not working around here).
