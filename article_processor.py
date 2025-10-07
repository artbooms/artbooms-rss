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

# 🔗 URL pubblico della cache su GitHub (no token richiesto)
GITHUB_RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"


# -----------------------------------------------------------------------------
# Funzioni di utilità
# -----------------------------------------------------------------------------
def _now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)


def _hash_links(links):
    h = hashlib.sha256()
    for u in links:
        h.update(u.encode("utf-8"))
    return h.hexdigest()


# -----------------------------------------------------------------------------
# Gestione cache (locale + GitHub)
# -----------------------------------------------------------------------------
def _load_cache():
    """Carica la cache locale o la scarica da GitHub se mancante."""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
                logger.info("Cache locale caricata con %d articoli", len(cache.get("items", {})))
                return cache
        except Exception:
            logger.exception("Errore caricamento cache locale, rigenero")

    # Se la cache non esiste, tenta da GitHub
    try:
        logger.info("Cache locale mancante: provo a scaricare da GitHub...")
        resp = requests.get(GITHUB_RAW_CACHE_URL, timeout=10)
        if resp.status_code == 200 and resp.text.strip():
            cache = json.loads(resp.text)
            _save_cache(cache)
            logger.info("Cache scaricata da GitHub con %d articoli", len(cache.get("items", {})))
            return cache
        else:
            logger.warning(f"Nessuna cache valida trovata su GitHub (status={resp.status_code})")
    except Exception as e:
        logger.warning(f"Errore recupero cache da GitHub: {e}")

    # Cache vuota come fallback
    logger.info("Inizializzo nuova cache vuota")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}


# -----------------------------------------------------------------------------
# Parsing e aggiornamento articoli
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
        logger.exception("parse_article failed for %s", url)
        return None, False

    content_hash = hashlib.sha256(
        (item.get("content_text", "") + (item.get("modified") or "")).encode("utf-8")
    ).hexdigest()

    if existing_item and existing_item.get("_hash") == content_hash:
        return existing_item, False

    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True


def _select_batch(links, cache):
    """Seleziona i prossimi link da processare (batch)."""
    if not links:
        return []
    cursor = cache.get("cursor", 0) or 0
    n = len(links)
    if cursor >= n:
        cursor = 0
    end = min(cursor + MAX_BATCH, n)
    batch = links[cursor:end]
    if not batch and n > 0:
        batch = links[:min(MAX_BATCH, n)]
    return batch


def generate_items(force=False):
    """Aggiorna la cache e restituisce gli articoli."""
    cache = _load_cache()
    session = requests.Session()
    links = _scan_archive(session=session)
    links_hash = _hash_links(links)

    if cache.get("links_hash") != links_hash:
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()
        if "cursor" not in cache:
            cache["cursor"] = 0

    # inversione ordine se archivio newest->oldest
    try:
        if links and len(links) > 50:
            links = list(reversed(links))
    except Exception:
        pass

    batch = links[:] if force else _select_batch(links, cache)

    for url in batch:
        existing = cache.get("items", {}).get(url)
        try:
            new_item, changed = _process_one(url, existing_item=existing, session=session)
        except Exception:
            logger.exception("Errore _process_one su %s", url)
            new_item, changed = None, False
        if new_item:
            cache.setdefault("items", {})[url] = new_item
            logger.info("Processed %s (changed=%s)", url, changed)
        time.sleep(REQUEST_DELAY)

    if links:
        cursor = cache.get("cursor", 0) or 0
        cursor = (cursor + len(batch)) % max(1, len(links))
        cache["cursor"] = cursor

    _save_cache(cache)

    # ordina articoli (più nuovi prima)
    def _to_dt(s):
        try:
            from dateutil import parser as _p
            if not s:
                return datetime(1970, 1, 1, tzinfo=timezone.utc)
            dt = _p.parse(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

    items_list = list(cache.get("items", {}).values())
    items_list.sort(
        key=lambda x: max(_to_dt(x.get("modified")), _to_dt(x.get("published"))),
        reverse=True
    )

    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS - Archivio completo"),
        "description": os.environ.get(
            "FEED_DESCRIPTION",
            "Tutti gli articoli di Artbooms con aggiornamenti automatici"
        ),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": datetime.utcnow().replace(tzinfo=timezone.utc)
    }

    return items_list, meta


def load_cache():
    """Accesso pubblico alla cache."""
    return _load_cache()


