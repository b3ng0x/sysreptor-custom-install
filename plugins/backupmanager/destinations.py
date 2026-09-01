"""
Upload adapters for backup destinations. Each adapter takes a local file path and returns
(ok: bool, detail: str). Adapters must never raise - a failure on one destination must not
abort the others or the overall backup run.
"""
import base64
import json
import logging
from pathlib import Path

import httpx
import requests

log = logging.getLogger(__name__)

DISCORD_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # Discord's default attachment limit


def upload_discord(file_path: Path, filename: str, webhook_url: str) -> tuple[bool, str]:
    if not webhook_url:
        return False, 'not configured'
    try:
        size = file_path.stat().st_size
        if size <= DISCORD_MAX_UPLOAD_BYTES:
            with open(file_path, 'rb') as f:
                resp = httpx.post(
                    webhook_url,
                    data={'content': f'SysReptor backup: `{filename}` ({size} bytes)'},
                    files={'file': (filename, f, 'application/octet-stream')},
                    timeout=120)
        else:
            resp = httpx.post(
                webhook_url,
                data={'content': f'SysReptor backup `{filename}` ({size} bytes) exceeds Discord\'s '
                                  f'{DISCORD_MAX_UPLOAD_BYTES} byte upload limit - notification only, '
                                  f'file was NOT uploaded here. Check GitHub/Drive/local storage instead.'},
                timeout=30)
        if resp.status_code in (200, 204):
            return True, 'uploaded' if size <= DISCORD_MAX_UPLOAD_BYTES else 'notified only (too large)'
        return False, f'HTTP {resp.status_code}: {resp.text[:300]}'
    except Exception as ex:
        log.exception('Discord upload failed')
        return False, str(ex)


def upload_github(file_path: Path, filename: str, token: str, repo: str, branch: str) -> tuple[bool, str]:
    if not (token and repo):
        return False, 'not configured'
    try:
        content_b64 = base64.b64encode(file_path.read_bytes()).decode()
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }
        results = []
        # Timestamped copy (full history kept in the repo) + a fixed "latest" pointer that gets
        # overwritten each run - satisfies "version override capability": git log on latest.tar.enc
        # shows every prior version, while the path itself always points at the newest backup.
        for path in (f'backups/{filename}', 'backups/latest.tar.enc'):
            get_resp = requests.get(
                f'https://api.github.com/repos/{repo}/contents/{path}',
                headers=headers, params={'ref': branch}, timeout=30)
            sha = get_resp.json().get('sha') if get_resp.status_code == 200 else None

            put_body = {
                'message': f'Automated backup: {filename}',
                'content': content_b64,
                'branch': branch,
            }
            if sha:
                put_body['sha'] = sha

            put_resp = requests.put(
                f'https://api.github.com/repos/{repo}/contents/{path}',
                headers=headers, data=json.dumps(put_body), timeout=120)
            if put_resp.status_code not in (200, 201):
                results.append(f'{path}: HTTP {put_resp.status_code} {put_resp.text[:200]}')
            else:
                results.append(f'{path}: ok')

        ok = all('ok' in r for r in results)
        return ok, '; '.join(results)
    except Exception as ex:
        log.exception('GitHub upload failed')
        return False, str(ex)


def upload_gdrive(file_path: Path, filename: str, service_account_json: str, folder_id: str) -> tuple[bool, str]:
    if not service_account_json:
        return False, 'not configured'
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account as gsa

        info = json.loads(service_account_json)
        creds = gsa.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/drive.file'])
        creds.refresh(Request())

        metadata = {'name': filename}
        if folder_id:
            metadata['parents'] = [folder_id]

        boundary = 'sysreptor-backup-boundary'
        file_bytes = file_path.read_bytes()
        body = (
            f'--{boundary}\r\n'
            f'Content-Type: application/json; charset=UTF-8\r\n\r\n'
            f'{json.dumps(metadata)}\r\n'
            f'--{boundary}\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + file_bytes + f'\r\n--{boundary}--'.encode()

        resp = requests.post(
            'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart',
            headers={
                'Authorization': f'Bearer {creds.token}',
                'Content-Type': f'multipart/related; boundary={boundary}',
            },
            data=body, timeout=300)
        if resp.status_code in (200, 201):
            return True, f'uploaded as file id {resp.json().get("id")}'
        return False, f'HTTP {resp.status_code}: {resp.text[:300]}'
    except Exception as ex:
        log.exception('Google Drive upload failed')
        return False, str(ex)
