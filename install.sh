#!/bin/bash
# Non-interactive installer: official SysReptor Community Edition + custom plugins/preset
# from this repo, layered on top. Based on the documented steps at
# https://docs.sysreptor.com/setup/installation and the (interactive) official
# install.sh at https://docs.sysreptor.com/install.sh - this script replicates its
# essential steps directly rather than piping into it, since that script has several
# blocking `read -p` prompts with no full non-interactive escape hatch.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SYSREPTOR_INSTALL_DIR:-/opt/sysreptor}"
ADMIN_USERNAME="${SYSREPTOR_ADMIN_USERNAME:-admin}"
ENABLED_PLUGINS_LIST="cyberchef,renderfindings,scanimport,customizetheme,backupmanager,customcss"

log() { echo "==> $*"; }

# 1. Prerequisites -----------------------------------------------------------
log "Checking prerequisites..."
MISSING=()
for cmd in sed curl openssl uuidgen tar; do
  command -v "$cmd" >/dev/null 2>&1 || MISSING+=("$cmd")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
  log "Installing missing packages: ${MISSING[*]}"
  apt-get update -qq
  apt-get install -y -qq sed curl openssl uuid-runtime coreutils
fi

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine..."
  curl -fsSL https://get.docker.com | bash
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose v2 plugin not found even after Docker install. Aborting." >&2
  exit 1
fi

# 2. Fetch official SysReptor deploy files -----------------------------------
if [ -d "$INSTALL_DIR/deploy" ]; then
  log "Existing $INSTALL_DIR/deploy found - reusing it (won't re-download)."
else
  log "Downloading official SysReptor Docker Compose files..."
  mkdir -p "$INSTALL_DIR"
  curl -s -L --output /tmp/sysreptor-setup.tar.gz \
    https://github.com/syslifters/sysreptor/releases/latest/download/setup.tar.gz
  tar -tzf /tmp/sysreptor-setup.tar.gz >/dev/null 2>&1 || {
    echo "ERROR: downloaded setup.tar.gz is not a valid archive." >&2
    exit 1
  }
  tar xzf /tmp/sysreptor-setup.tar.gz -C /tmp
  rsync -a /tmp/sysreptor/ "$INSTALL_DIR/" 2>/dev/null || cp -r /tmp/sysreptor/. "$INSTALL_DIR/"
  rm -rf /tmp/sysreptor /tmp/sysreptor-setup.tar.gz
fi

DEPLOY_DIR="$INSTALL_DIR/deploy"
cd "$DEPLOY_DIR"

# 3. Generate app.env with fresh secrets (zero manual input) -----------------
if [ -f app.env ]; then
  log "app.env already exists - leaving secrets as-is (won't overwrite an existing install)."
else
  log "Generating app.env with fresh secrets..."
  cp app.env.example app.env

  SECRET_KEY="$(openssl rand -base64 64 | tr -d '\n=')"
  sed -i -e "s#.*SECRET_KEY=.*#SECRET_KEY=\"${SECRET_KEY}\"#" app.env

  KEY_ID="$(uuidgen)"
  AES_KEY="$(openssl rand -base64 32)"
  sed -i -e "s#.*ENCRYPTION_KEYS=.*#ENCRYPTION_KEYS=[{\"id\": \"${KEY_ID}\", \"key\": \"${AES_KEY}\", \"cipher\": \"AES-GCM\", \"revoked\": false}]#" app.env
  sed -i -e "s#.*DEFAULT_ENCRYPTION_KEY_ID=.*#DEFAULT_ENCRYPTION_KEY_ID=\"${KEY_ID}\"#" app.env

  # ALLOWED_HOSTS: this host's own reachable addresses, not any specific prior box's.
  HOST_IPS="$(hostname -I 2>/dev/null | tr ' ' ',' | sed 's/,$//' || true)"
  ALLOWED_HOSTS_VALUE="localhost,127.0.0.1${HOST_IPS:+,$HOST_IPS}"
  if grep -q '^ALLOWED_HOSTS=' app.env; then
    sed -i -e "s#.*ALLOWED_HOSTS=.*#ALLOWED_HOSTS=\"${ALLOWED_HOSTS_VALUE}\"#" app.env
  else
    echo "ALLOWED_HOSTS=\"${ALLOWED_HOSTS_VALUE}\"" >> app.env
  fi

  echo "ENABLED_PLUGINS=\"${ENABLED_PLUGINS_LIST}\"" >> app.env
  echo "PLUGIN_DIRS=/app/plugins/,/data/plugins" >> app.env
  echo "DEBUG=off" >> app.env
fi

if [ ! -f .env ]; then
  [ -f .env.example ] && cp .env.example .env || touch .env
fi
log "Generating database and Redis passwords..."
for env_var in POSTGRES_PASSWORD REDIS_PASSWORD; do
  if grep -q "^${env_var}=" .env; then
    continue  # don't rotate an existing install's DB password
  fi
  password="$(openssl rand -hex 32)"
  if grep -qE "^[[:space:]]*(#[[:space:]]*)?${env_var}=" .env; then
    sed -i "s|^[[:space:]]*#\?[[:space:]]*${env_var}=.*|${env_var}=${password}|" .env
  else
    echo "${env_var}=${password}" >> .env
  fi
done
grep -q '^SYSREPTOR_POSTGRES_VERSION=' .env || echo "SYSREPTOR_POSTGRES_VERSION=18" >> .env
grep -q '^BIND_PORT=' .env || echo 'BIND_PORT=0.0.0.0:8000:8000' >> .env

rm -f docker-compose.override.yml 2>/dev/null || true

# 4. External Docker volumes --------------------------------------------------
log "Creating Docker volumes (idempotent)..."
docker volume inspect sysreptor-db-data >/dev/null 2>&1 || docker volume create sysreptor-db-data >/dev/null
docker volume inspect sysreptor-app-data >/dev/null 2>&1 || docker volume create sysreptor-app-data >/dev/null

# 5. Copy custom plugins into the app-data volume -----------------------------
log "Installing custom plugins into the sysreptor-app-data volume..."
PLUGIN_VOLUME_PATH="$(docker volume inspect sysreptor-app-data --format '{{ .Mountpoint }}')"
mkdir -p "$PLUGIN_VOLUME_PATH/plugins"
for plugin in backupmanager customcss; do
  rm -rf "${PLUGIN_VOLUME_PATH:?}/plugins/${plugin}"
  cp -r "$REPO_DIR/plugins/$plugin" "$PLUGIN_VOLUME_PATH/plugins/$plugin"
done
chown -R 1000:1000 "$PLUGIN_VOLUME_PATH/plugins"

# 6. Bring the stack up -------------------------------------------------------
log "Starting containers (this pulls images on first run, can take a few minutes)..."
set +e
( set -a; source .env; set +a; docker compose up -d )
COMPOSE_RC=$?
set -e
if [ $COMPOSE_RC -ne 0 ]; then
  echo "ERROR: docker compose up failed." >&2
  exit 1
fi

log "Waiting for the database/migrations to be ready..."
for i in $(seq 1 90); do
  if echo '' | docker compose exec --no-TTY app python3 manage.py migrate --check >/dev/null 2>&1; then
    break
  fi
  sleep 2
  if [ "$i" -eq 90 ]; then
    echo "ERROR: app never became ready (migrate --check kept failing after 3 minutes)." >&2
    exit 1
  fi
done

log "Waiting for the HTTP healthcheck to report healthy (5 consecutive OK checks, avoids reporting success on a stale/mid-recycle response)..."
OK_STREAK=0
for i in $(seq 1 90); do
  BODY="$(curl -s --max-time 5 http://127.0.0.1:8000/api/public/utils/healthcheck/ || true)"
  if echo "$BODY" | grep -q '"database":true' && echo "$BODY" | grep -q '"migrations":true'; then
    OK_STREAK=$((OK_STREAK + 1))
  else
    OK_STREAK=0
  fi
  [ "$OK_STREAK" -ge 5 ] && break
  sleep 2
  if [ "$i" -eq 90 ]; then
    echo "ERROR: healthcheck never stabilized after 3 minutes." >&2
    exit 1
  fi
done

# 7. Admin user ----------------------------------------------------------------
log "Creating admin user..."
ADMIN_PASSWORD="$(openssl rand -base64 20 | tr -d '\n=')"
echo '' | docker compose exec --no-TTY \
  -e DJANGO_SUPERUSER_USERNAME="$ADMIN_USERNAME" \
  -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASSWORD" \
  app python3 manage.py createsuperuser --noinput
echo "$ADMIN_PASSWORD" > "$DEPLOY_DIR/admin-password.txt"
chmod 600 "$DEPLOY_DIR/admin-password.txt"

# 8. Apply the Cyberpunk Neon preset --------------------------------------------
log "Applying the Cyberpunk Neon CSS preset..."
docker compose cp "$REPO_DIR/presets/cyberpunk-neon.css" app:/tmp/preset.css
docker compose exec --no-TTY app python3 manage.py shell -c "
from sysreptor.utils.configuration import configuration
with open('/tmp/preset.css') as f:
    css = f.read()
configuration.update({'CUSTOM_CSS': css, 'CUSTOM_CSS_ENABLED': True})
print('Preset applied:', len(css), 'bytes')
"

# 9. Summary ---------------------------------------------------------------------
echo ""
echo "================================================================"
echo " SysReptor + custom plugins installed."
echo "================================================================"
echo " URL:      http://127.0.0.1:8000/  (also reachable on this host's other IPs: ${HOST_IPS:-none detected})"
echo " Username: $ADMIN_USERNAME"
echo " Password: $ADMIN_PASSWORD"
echo "           (also saved to $DEPLOY_DIR/admin-password.txt, root-only)"
echo ""
echo " Plugins enabled: $ENABLED_PLUGINS_LIST"
echo " Theme preset:    Cyberpunk Neon (applied, toggle off via the Custom CSS plugin page)"
echo ""
echo " Notes:"
echo " - Community Edition: single-superuser, 1 active API token per user, no license-gated"
echo "   backup/restore commands (this is exactly why backupmanager exists as a workaround)."
echo " - Anything under deploy/plugins is hand-installed, not apt-managed - there is no"
echo "   automatic upgrade path for backupmanager/customcss. Re-run this script's step 5"
echo "   manually (or just re-run the whole script) to pick up plugin updates from this repo."
echo " - If you edit a plugin's static files live, browsers/the app server can serve a stale"
echo "   response for up to ~60s afterward (a caching/worker-recycle quirk) - not an issue for"
echo "   a fresh install, just don't panic if you see it after a later edit."
echo "================================================================"
