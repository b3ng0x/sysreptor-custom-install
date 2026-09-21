"""
Clean-room backup/restore engine.

SysReptor Community Edition gates its own `manage.py backup`/`restorebackup` commands behind a
Professional license check. Rather than depending on internal, license-gated code paths, this
module does its own DB dump (pg_dump/pg_restore against the same Postgres instance the app uses)
and its own tar of the app-data volume, combined into one archive and encrypted with AES-256-GCM.
"""
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

log = logging.getLogger(__name__)

DATA_DIR = Path('/data')
PLUGIN_DIR = Path(__file__).resolve().parent
BACKUPS_DIR = PLUGIN_DIR / 'backups'
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# Excluded from the files tar: our own plugin code + output dir (redeployed by the installer,
# not user data; also avoids the tar recursively including previous backup archives).
EXCLUDE_RELATIVE = {'plugins/backupmanager'}


class BackupError(Exception):
    pass


def _pg_env():
    env = os.environ.copy()
    env['PGHOST'] = os.environ['DATABASE_HOST']
    env['PGPORT'] = os.environ.get('DATABASE_PORT', '5432')
    env['PGDATABASE'] = os.environ['DATABASE_NAME']
    env['PGUSER'] = os.environ['DATABASE_USER']
    env['PGPASSWORD'] = os.environ['DATABASE_PASSWORD']
    return env


# The app image's pg_dump/pg_restore client (from Debian's base packages) can be a newer major
# version than the actual Postgres server (SYSREPTOR_POSTGRES_VERSION, default 14 - see
# deploy/docker-compose.yml). Newer pg_dump clients (17+) unconditionally emit a
# "SET transaction_timeout = 0;" preamble that older servers reject outright. Using plain-SQL
# dumps (not custom format) lets us defensively filter lines like this before replay, instead of
# being locked into whatever pg_restore's binary-format replay does verbatim.
_INCOMPATIBLE_SET_PATTERNS = ('SET transaction_timeout',)


def _dump_database(out_path: Path):
    result = subprocess.run(
        ['pg_dump', '--clean', '--if-exists', '--no-owner', '--no-privileges', '-f', str(out_path)],
        env=_pg_env(), capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise BackupError(f'pg_dump failed: {result.stderr.strip()}')


def _restore_database(dump_path: Path):
    filtered_path = dump_path.with_suffix('.filtered.sql')
    with open(dump_path, encoding='utf-8', errors='replace') as src, open(filtered_path, 'w', encoding='utf-8') as dst:
        for line in src:
            if any(line.startswith(pat) for pat in _INCOMPATIBLE_SET_PATTERNS):
                continue
            dst.write(line)

    result = subprocess.run(
        ['psql', '--set', 'ON_ERROR_STOP=1', '-f', str(filtered_path)],
        env=_pg_env(), capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise BackupError(f'psql restore failed: {result.stderr.strip()}')
    return result.stderr


def _exclude_filter(tarinfo: tarfile.TarInfo):
    # Applies to every member added recursively (tarfile.add's filter runs per-file, not just on
    # the top-level path passed in) - matters because EXCLUDE_RELATIVE entries like
    # 'plugins/backupmanager' are nested under a top-level dir ('plugins'), not top-level
    # themselves. A shallow top-level-only check here previously let this whole subtree (this
    # plugin's own code AND its accumulating backups/ dir) get included in every backup, causing
    # each backup to contain all prior backups and roughly double in size every run.
    if any(tarinfo.name == ex or tarinfo.name.startswith(ex + '/') for ex in EXCLUDE_RELATIVE):
        return None
    return tarinfo


def _tar_files(out_path: Path):
    with tarfile.open(out_path, 'w') as tf:
        for item in DATA_DIR.iterdir():
            rel = item.relative_to(DATA_DIR)
            tf.add(item, arcname=str(rel), filter=_exclude_filter)


def _untar_files(tar_path: Path, keep_existing_plugin_dirs=True):
    with tarfile.open(tar_path, 'r') as tf:
        members = tf.getmembers()
        if keep_existing_plugin_dirs:
            members = [m for m in members if not any(m.name == ex or m.name.startswith(ex + '/') for ex in EXCLUDE_RELATIVE)]
        tf.extractall(path=DATA_DIR, members=members, filter='data')


def encrypt_file(in_path: Path, out_path: Path, key: bytes):
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    data = in_path.read_bytes()
    ct = aesgcm.encrypt(nonce, data, None)
    out_path.write_bytes(nonce + ct)


def decrypt_file(in_path: Path, out_path: Path, key: bytes):
    aesgcm = AESGCM(key)
    blob = in_path.read_bytes()
    nonce, ct = blob[:12], blob[12:]
    try:
        data = aesgcm.decrypt(nonce, ct, None)
    except Exception as ex:
        raise BackupError(f'Decryption failed (wrong key or corrupted file): {ex}') from ex
    out_path.write_bytes(data)


def create_backup(key_hex: str) -> Path:
    """
    Create an encrypted backup archive containing a pg_dump of the database and a tar of the
    app-data files. Returns the path to the final encrypted file (kept in BACKUPS_DIR).
    """
    key = bytes.fromhex(key_hex)
    # Second-resolution timestamp alone can collide (e.g. rapid manual re-triggers, or tests),
    # silently overwriting an older backup with no error - a short random suffix guarantees a
    # unique filename regardless of timing.
    ts = f'{time.strftime("%Y%m%d-%H%M%S")}-{secrets.token_hex(3)}'
    with tempfile.TemporaryDirectory(dir='/tmp') as tmpdir:
        tmpdir = Path(tmpdir)
        db_dump = tmpdir / 'db.dump'
        files_tar = tmpdir / 'files.tar'
        outer_tar = tmpdir / 'backup.tar'

        _dump_database(db_dump)
        _tar_files(files_tar)

        with tarfile.open(outer_tar, 'w') as tf:
            tf.add(db_dump, arcname='db.dump')
            tf.add(files_tar, arcname='files.tar')
            # encryption_key_id records which SysReptor-level field-encryption key (from
            # ENCRYPTION_KEYS in app.env, NOT this backup's own BACKUP_ENCRYPTION_KEY) the
            # database's encrypted columns (passwords, notes, findings, comments...) were sealed
            # with at backup time. It is just a key *id* (safe to store unencrypted - it names a
            # key, it isn't one), recorded so restore_backup() can warn immediately if the target
            # instance's own ENCRYPTION_KEYS doesn't contain it, instead of that surfacing later
            # as opaque CryptoError 500s on login.
            meta = {
                'created': ts,
                'format_version': 2,
                'encryption_key_id': settings.DEFAULT_ENCRYPTION_KEY_ID,
            }
            meta_bytes = json.dumps(meta).encode()
            info = tarfile.TarInfo(name='meta.json')
            info.size = len(meta_bytes)
            tf.addfile(info, io.BytesIO(meta_bytes))

        final_path = BACKUPS_DIR / f'backup-{ts}.tar.enc'
        encrypt_file(outer_tar, final_path, key)

    return final_path


def restore_backup(enc_path: Path, key_hex: str, skip_database=False, skip_files=False, pre_db_restore_hook=None):
    """
    Returns a dict describing the restore, including an encryption-key compatibility check
    (see meta['encryption_key_id'] in create_backup) so the caller can surface a clear warning
    immediately instead of the failure only showing up later as CryptoError 500s on login.

    pre_db_restore_hook, if given, is called immediately before the database replace - the true
    point of no return - and only if the database is actually about to be replaced (never on a
    files-only restore, and never if decryption/extraction/a bad key failed first). This lets a
    caller do something disruptive-but-necessary, like invalidating the calling HTTP session,
    exactly when it's actually warranted rather than pre-emptively for a restore attempt that
    might still fail validation before touching anything.
    """
    key = bytes.fromhex(key_hex)
    with tempfile.TemporaryDirectory(dir='/tmp') as tmpdir:
        tmpdir = Path(tmpdir)
        outer_tar = tmpdir / 'backup.tar'
        decrypt_file(enc_path, outer_tar, key)

        with tarfile.open(outer_tar, 'r') as tf:
            tf.extractall(path=tmpdir, filter='data')

        meta_path = tmpdir / 'meta.json'
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}

        # Files first, then the database - the database replay is the true point of no return
        # (it can't be trivially rolled back mid-request), while file extraction failing (e.g. a
        # permissions issue) is comparatively safe to fail on. Restoring DB before files meant a
        # file-extraction failure left the system with an already-swapped-in old database (with
        # its own old encryption keys, session state, etc.) but the previous files/config still
        # in place - a broken, inconsistent half-restore that's hard to recover from.
        if not skip_files:
            _untar_files(tmpdir / 'files.tar')

        result = {'backup_encryption_key_id': meta.get('encryption_key_id'), 'key_available': None}

        if not skip_database:
            if pre_db_restore_hook:
                pre_db_restore_hook()
            _restore_database(tmpdir / 'db.dump')

            # The DB replace above happens via a raw pg_restore/psql replay, entirely outside
            # Django's configuration.update() (the only thing that normally clears
            # sysreptor.utils.configuration's process-local, unbounded functools.cache of DB
            # config values). Left uncleared, this process keeps serving whatever
            # BACKUP_ENCRYPTION_KEY (and any other DB-backed setting) was cached before the
            # restore, even though the row underneath just changed to the restored data's value -
            # this is exactly what made the very next automatic backup get encrypted with a key
            # that no longer matched what was actually configured, silently producing an
            # undecryptable backup. Same fix SysReptor's own Settings-save endpoint applies after
            # every config change (see api_utils/views.py ConfigurationViewSet.patch) - clear this
            # process's cache immediately, then reload_server() (SIGHUP to gunicorn) so every
            # worker process in the container picks up the freshly-restored config too, not just
            # the one that happened to handle this request.
            from sysreptor.utils.configuration import configuration, reload_server
            configuration.clear_cache()
            reload_server()

            # settings.ENCRYPTION_KEYS is loaded once from the app.env environment variable at
            # process start and is NOT part of the database, so it's untouched by the restore
            # above - it still reflects this instance's own keys, which is exactly what we want
            # to compare the backup's origin key id against.
            result['key_available'] = (
                result['backup_encryption_key_id'] is None
                or result['backup_encryption_key_id'] in settings.ENCRYPTION_KEYS
            )

        return result


def list_local_backups():
    return sorted(BACKUPS_DIR.glob('backup-*.tar.enc'), reverse=True)


def prune_local_backups(keep=7):
    """Keep local disk from filling up; destinations hold the long-term copies."""
    files = list_local_backups()
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            log.warning(f'Failed to prune old local backup {f}')
