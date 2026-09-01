# SysReptor + custom plugins

A ready-to-run installer for [SysReptor Community Edition](https://docs.sysreptor.com/) that
layers two custom plugins and a theme preset on top of the **official** SysReptor Docker image
and Docker Compose setup - nothing here forks or rebuilds SysReptor itself, so upgrading
SysReptor stays on the normal upstream path.

## What you get

- **`backupmanager`** - manual/daily encrypted backups of the whole app (database + files) with
  optional upload to Discord, GitHub, or Google Drive, and a restore UI. Built because SysReptor
  Community Edition's own `manage.py backup`/`restorebackup` commands are gated behind a
  Professional license - see the docstring in `plugins/backupmanager/apps.py` for details. Full
  usage docs in `plugins/backupmanager/README.md`.
- **`customcss`** - lets a superuser inject raw CSS app-wide (every page, not just a plugin
  settings page), with a live preview-before-committing workflow, on top of what SysReptor's
  own built-in `customizetheme` plugin already covers via JSON theme variables. Docs in
  `plugins/customcss/README.md`.
- **`presets/cyberpunk-neon.css`** - a complete neon-green-on-black theme covering the app bar,
  navigation drawer, buttons, switches, cards, tables, dialogs, the report markdown editor
  toolbar, and more, applied automatically by the installer.

## Prerequisites

- A Linux host (tested on Debian/Kali-family) with root/sudo access.
- Internet access to pull the SysReptor release tarball and Docker images.
- Nothing else - `install.sh` installs Docker and everything else it needs.

## Install

```bash
git clone https://github.com/b3ng0x/sysreptor-custom-install.git
cd sysreptor-custom-install
sudo ./install.sh
```

That's it. Fully non-interactive - no prompts, no manual config editing. At the end it prints
the login URL and an auto-generated admin username/password (also saved to
`/opt/sysreptor/deploy/admin-password.txt`, root-only permissions).

### What the installer actually does

1. Installs missing prerequisites (`sed curl openssl uuid-runtime coreutils`) and Docker Engine
   + Compose plugin if not already present.
2. Downloads the official SysReptor release tarball (same one
   [docs.sysreptor.com/install.sh](https://docs.sysreptor.com/install.sh) uses) into
   `/opt/sysreptor` (override with `SYSREPTOR_INSTALL_DIR`).
3. Generates a fresh `SECRET_KEY`, AES-256 data-at-rest `ENCRYPTION_KEYS`, and
   `POSTGRES_PASSWORD`/`REDIS_PASSWORD` - all via `openssl rand`, zero manual input, never reused
   across installs.
4. Sets `ALLOWED_HOSTS` from this host's own detected IP addresses (`hostname -I`) plus
   `localhost`/`127.0.0.1` - not hardcoded to any specific machine.
5. Creates the `sysreptor-db-data`/`sysreptor-app-data` Docker volumes (the official
   `docker-compose.yml` declares these `external: true`, so they must exist before `docker
   compose up` - the official installer creates them too, this script does the same).
6. Copies `plugins/backupmanager` and `plugins/customcss` into the app-data volume's
   `plugins/` directory and fixes ownership to uid 1000 (the container's runtime user, not
   root - forgetting this makes the plugins fail to load).
7. Sets `ENABLED_PLUGINS` (the two custom plugins plus SysReptor's own built-in
   `cyberchef`, `renderfindings`, `scanimport`, `customizetheme`) and `PLUGIN_DIRS` in `app.env`.
8. Brings the stack up with `docker compose up -d`, then waits for **5 consecutive** healthy
   healthcheck responses before continuing - not just one, since this app briefly recycles its
   worker processes after certain operations and a single lucky/unlucky check isn't reliable
   evidence either way.
9. Creates the admin superuser non-interactively via the official
   `manage.py createsuperuser --noinput` mechanism with a generated password.
10. Applies the Cyberpunk Neon CSS preset to the running instance.

Re-running the script is safe: it detects an existing install (`app.env` present) and won't
regenerate secrets or touch your database password, but it does refresh the plugin files from
this repo each time - that's the intended way to pick up plugin updates.

## Known limitations

- **Community Edition, not Professional.** Single active superuser, one active API token per
  user at a time, no license-gated features (including the native backup/restore commands -
  see `backupmanager`'s docstring). If you have a Professional license, set `SYSREPTOR_LICENSE`
  in your environment before running - the installer doesn't currently wire that through, PRs
  welcome.
- **No apt-upgrade path for the plugins.** They're plain files dropped into a Docker volume, not
  a packaged/versioned artifact. Track changes here in git, and re-run `install.sh` (or just the
  plugin-copy step) to deploy updates.
- **Static-file cache quirk.** After editing a plugin's `static/` files on a *running* instance,
  the app can serve a stale (or briefly truncated/"connection reset") response for up to about a
  minute afterward - a caching/worker-recycle behavior in SysReptor itself, not a plugin bug.
  Doesn't affect a fresh install (nothing's being edited live), just worth knowing if you
  customize further.
- This does **not** import the HTB exam designs/demo projects (that's a separate, one-time
  `manage.py importdemodata` step covered in
  [SysReptor's HTB reporting docs](https://docs.sysreptor.com/htb-reporting-with-sysreptor) -
  unrelated to what's packaged here, run it yourself afterward if you want that content too).

## Repo layout

```
install.sh                  the installer described above
plugins/backupmanager/      full plugin source
plugins/customcss/          full plugin source
presets/cyberpunk-neon.css  the theme CSS applied automatically on install
```
