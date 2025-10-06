import os
import json
import base64
import logging
from datetime import datetime, timezone
import requests
import re

logger = logging.getLogger("cache_sync")

# Legge MAX_BATCH e, se contiene anche un token, separa le due parti.
# Formati accettati: "3 TOKEN", "3|TOKEN", "3~~TOKEN", "3;TOKEN", "3,TOKEN"
_raw = os.environ.get("MAX_BATCH", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

if _raw:
    m = re.match(r"^\s*(\d+)\s*(?:\||~~|;|,|\s)\s*(.+)?$", _raw)
    if m:
        os.environ["MAX_BATCH"] = m.group(1)
        if not GITHUB_TOKEN:
            token_candidate = (m.group(2) or "").strip()
            if token_candidate:
                GITHUB_TOKEN = token_candidate

# === PARAMETRI PERSONALIZZATI PER ARTBOOMS ===
REPO_OWNER = "artbooms"
REPO_NAME = "artbooms-rss"
BRANCH = "main"
REMOTE_PATH = "cache/articles_cache.json"
GITHUB_API = "https://api.github.com"

def _headers():
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "artbooms-rss-cache-sync/1.0",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = "token " + GITHUB_TOKEN
    return h

def _get_remote_file():
    if not GITHUB_TOKEN:
        logger.warning("Token GitHub mancante: skip GET")
        return None
    url = f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/contents/{REMOTE_PATH}?ref={BRANCH}"
    r = requests.get(url, headers=_headers(), timeout=20)
    if r.status_code == 200:
        return r.json()
    if r.status_code == 404:
        logger.info("Cache remota non presente (404)")
        return None
    logger.warning("GET cache remota: HTTP %s - %s", r.status_code, r.text[:120])
    return None

def _put_remote_file(content_bytes, sha=None):
    if not GITHUB_TOKEN:
        logger.warning("Token GitHub mancante: skip PUT")
        return
    url = f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/contents/{REMOTE_PATH}"
    b64 = base64.b64encode(content_bytes).decode("utf-8")
    msg = "Update cache {path} @ {ts}".format(
        path=REMOTE_PATH,
        ts=datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    )
    payload = {"message": msg, "content": b64, "branch": BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, json=payload, headers=_headers(), timeout=30)
    if r.status_code in (200, 201):
        logger.info("Cache caricata su GitHub (%s)", REMOTE_PATH)
        return
    logger.warning("PUT cache remota: HTTP %s - %s", r.status_code, r.text[:120])

def pull_cache_if_missing(local_path="articles_cache.json"):
    """Se manca la cache locale, prova a scaricarla da GitHub (se c'è)."""
    if os.path.exists(local_path):
        return
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
    """Se la cache locale è cambiata rispetto alla remota, fai upload su GitHub."""
    if not os.path.exists(local_path):
        logger.info("Cache locale assente: skip push")
        return
    try:
        with open(local_path, "rb") as f:
            local_bytes = f.read()
    except Exception as e:
        logger.warning("Impossibile leggere cache locale: %s", e)
        return
    remote = _get_remote_file()
    remote_bytes, remote_sha = None, None
    if remote and "content" in remote:
        try:
            remote_bytes = base64.b64decode(remote["content"])
            remote_sha = remote.get("sha")
        except Exception:
            pass
    if (remote_bytes is not None) and (remote_bytes == local_bytes):
        logger.info("Cache identica alla remota: nessun upload")
        return
    _put_remote_file(local_bytes, sha=remote_sha)
