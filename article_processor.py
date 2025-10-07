import os
import json
import time
import hashlib
import logging
from datetime import datetime, timezone
import requests

from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html

logger = logging.getLogger("article_processor")

# 🔧 Configurazione base
ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "3"))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))

# 🔗 URL pubblico del file cache su GitHub (raw)
GITHUB_RAW_CACHE_URL = (
    "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
)


# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------

def _now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_links(links):
    h = hashlib.sha256()
    for u in links:
        h.update(u.encode("utf-8"))
    return h.hexdigest()


# -----------------------------------------------------------------------------
# Cache handling
# -----------------------------------------------------------------------------

def _load_cache():
    """Carica la cache locale o, se mancante, prova a scaricarla da GitHub."""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
                logger.info(f"Cache locale caricata con {len(cache.get('items', {}))} articoli.")
                return cache
        except Exception:
            logger.exception("Errore durante il caricamento della cache locale, rigenero.")
    else:
        # 🔹 se non esiste cache locale, prova a leggere da GitHub raw
        try:
            logger.info("Cache locale non trovata, provo a scaricare da GitHub...")
            resp = requests.get(GITHUB_RAW_CACHE_URL, timeout=10)
            if resp.status_code == 200 and resp.text.strip():
                cache = json.loads(resp.text)
                _save_cache(cache)
                logger.info(f"Cache scaricata da GitHub con {len(cache.get('items', {}))} articoli.")
                return cache
            else:
                logger.warning(f"Nessuna cache valida trovata su GitHub (status {resp.status_code}).")
        except Exception as e:
            logger.warning(f"Errore nel recupero cache da GitHub: {e}")

    # fallback vuoto
    logger.info("Cache vuota inizializzata.")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}


def _save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)


def load_cache():
    return _load_cache()


# -----------------------------------------------------------------------------
# Article scanning and processing
# -----------------------------------------------------------------------------

def _scan_archive(session=None):
    s = session or requests.Session()
    html = fetch_html(ARCHIVE_URL, session=s)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    return links


def _process_one(url, existing_item=None, session=None):
    s = session or requests.Session()
    try:
        item = parse_article(url, session=s)
    except Exception:
        logger.exception(f"Errore parse_article su {url}")
        return None, False

    # Calcola hash del contenuto per verificare modifiche
    content_hash = _hash_text(item.get("description", "") + item.get("title", ""))
    if existing_item and existing_item.get("hash") == content_hash:
        return existing_item, False  # Nessuna modifica

    item["hash"] = content_hash
    item["updated_at"] = _now_iso()
    return item, True


def generate_items(force=False):
    """Aggiorna la cache elaborando batch di articoli e restituisce gli item correnti."""
    cache = _load_cache()
    session = requests.Session()

    links = _scan_archive(session)
    links_hash = _hash_links(links)
    if not force and cache.get("links_hash") == links_hash:
        logger.info("Nessun cambiamento negli archivi, uso la cache esistente.")
        items = list(cache["items"].values())
        return items, {"generated_at": _now_iso(), "total": len(items)}

    cursor = cache.get("cursor", 0)
    batch_links = links[cursor:cursor + MAX_BATCH]
    logger.info(f"Elaboro batch di {len(batch_links)} articoli (cursor={cursor})...")

    updated = 0
    for url in batch_links:
        existing_item = cache["items"].get(url)
        item, changed = _process_one(url, existing_item, session)
        if item:
            cache["items"][url] = item
            if changed:
                updated += 1
        time.sleep(REQUEST_DELAY)

    cache["cursor"] = cursor + len(batch_links)
    cache["last_scan"] = _now_iso()
    cache["links_hash"] = links_hash
    _save_cache(cache)

    total_items = len(cache["items"])
    logger.info(f"Cache aggiornata: {total_items} articoli totali, {updated} modificati.")
    return list(cache["items"].values()), {
        "generated_at": _now_iso(),
        "total": total_items,
        "updated": updated,
    }
