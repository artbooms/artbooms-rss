import os
import json
import base64
import logging
from datetime import datetime, timezone
import requests

logger = logging.getLogger("cache_sync")

# ✅ Legge token da MAX_BATCH se contiene anche "|"
_raw = os.environ.get("MAX_BATCH", "")
if "|" in _raw:
    parts = _raw.split("|", 1)
    os.environ["MAX_BATCH"] = parts[0]
    GITHUB_TOKEN = parts[1]
else:
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

REPO_OWNER = "artbooms"
REPO_NAME = "artbooms-rss"
BRANCH = "main"
REMOTE_PATH = "cache/articles_cache.json"
GITHUB_API = "https://api.github.com"

def _headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "artbooms-rss-cache-sync/1.0",
    }

def _get_remote_file():
    if not GITHUB_TOKEN:
        logger.warning("Nessun token GitHub configurato")
        return None
    url = f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/contents/{REMOTE_PATH}?ref={BRANCH}"
    r = requests.get(url, headers=_headers(), timeout=20)
    if r.status_code == 200:
        return r.json()
    else:
        logger.warning("Cache remota non trovata (HTTP %s)", r.status_code)
        return None

def _put_remote_file(content_bytes, sha=None):
    if not GITHUB_TOKEN:
        logger.warning("Token GitHub mancante, salto upload cache")
        return
    url = f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/contents/{REMOTE_PATH}"
    b64 = base64.b64encode(content_bytes).decode("utf-8")
    msg = f"Aggiornamento cache {REMOTE_PATH} @ {datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()}"
    payload = {"message": msg, "content": b64, "branch": BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, json=payload, headers=_headers(), timeout=30)
    if r.status_code in (200, 201):
        logger.info("Cache caricata su GitHub (%s)", REMOTE_PATH)
    else:
        logger.warning("Errore upload cache: %s %s", r.status_code, r.text[:100])

def pull_cache_if_missing(local_path="articles_cache.json"):
    """Scarica la cache da GitHub se manca localmente."""
    remote = _get_remote_file()
    if remote and "content" in remote:
        content = base64.b64decode(remote["content"])
        folder = os.path.dirname(local_path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(content)
        logger.info("Cache ripristinata da GitHub")

def push_cache_if_changed(local_path="articles_cache.json"):
    """Carica su GitHub la cache locale se modificata."""
    if not GITHUB_TOKEN:
        logger.warning("Token GitHub mancante, salto upload cache")
        return
    if not os.path.exists(local_path):
        logger.warning("Cache locale assente, nessun upload")
        return
    with open(local_path, "rb") as f:
        local_bytes = f.read()
    remote = _get_remote_file()
    remote_bytes = None
    remote_sha = None
    if remote and "content" in remote:
        remote_bytes = base64.b64decode(remote["content"])
        remote_sha = remote.get("sha")
    if remote_bytes == local_bytes:
        logger.info("Cache identica: nessun upload necessario")
        return
    _put_remote_file(local_bytes, sha=remote_sha)
