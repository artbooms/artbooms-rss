import os
import json
import time
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests

from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html

logger = logging.getLogger("article_processor")

# 🔧 Configurabili via env
ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "1"))   # quanti link processare per run
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))  # delay tra richieste per non sovraccaricare

# 🔗 URL pubblico del file cache su GitHub (non serve token)
GITHUB_RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"


def _now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _load_cache():
    """
    Carica la cache locale.
    Se non esiste, tenta il recupero automatico da GitHub raw.
    """
    # 1️⃣ Se esiste localmente → usa quella
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
                logger.info("Cache locale caricata con %d articoli", len(cache.get("items", {})))
                return cache
        except Exception:
            logger.exception("Errore caricamento cache locale, rigenero")

    # 2️⃣ Se non esiste → prova a scaricare da GitHub
    try:
        logger.info("Cache locale mancante: provo a scaricare da GitHub...")
        resp = requests.get(GITHUB_RAW_CACHE_URL, timeout=10)
        if resp.status_code == 200 and resp.text.strip():
            cache = json.loads(resp.text)
            _save_cache(cache)
            logger.info("Cache scaricata da GitHub (%d articoli)", len(cache.get("items", {})))
            return cache
        else:
            logger.warning(f"Nessuna cache valida trovata su GitHub (status={resp.status_code})")
    except Exception as e:
        logger.warning(f"Errore recupero cache da GitHub: {e}")

    # 3️⃣ Fallback → cache vuota
    logger.info("Inizializzo nuova cache vuota")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}


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


def _scan_archive(session=None):
    s = session or requests.Session()
    html = fetch_html(ARCHIVE_URL, session=s)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    return links


def _process_one(url, existing_item=None, session=None):
    """
    Scarica e parse l'articolo, ritorna (item_dict, changed_bool)
    """
    s = session or requests.Session()
    try:
        item = parse_article(url, session=s)
    except Exception:
        logger.exception("parse_article failed for %s", url)
        return None, False

    # compute a small content hash per rilevare modifiche
    content_hash = hashlib.sha256(
        (item.get("content_text", "") + (item.get("modified") or "")).encode("utf-8")
    ).hexdigest()

    if existing_item and existing_item.get("_hash") == content_hash:
        return existing_item, False

    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True


def _select_batch(links, cache):
    """
    Sceglie i prossimi link da processare partendo da cache['cursor'].
    Default: ritorna fino a MAX_BATCH link (sequenziali)
    """
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
    """
    Main: ritorna (items_list, meta)
    - force=True -> processa tutta la lista (slow)
    - force=False -> processa solo un batch (MAX_BATCH)
    """
    cache = _load_cache()
    session = requests.Session()
    links = _scan_archive(session=session)
    links_hash = _hash_links(links)

    # aggiorno cache se lista link cambiata (nuovi articoli)
    if cache.get("links_hash") != links_hash:
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()
        if "cursor" not in cache:
            cache["cursor"] = 0

    # Inversione automatica se archivio ordinato newest→ol
