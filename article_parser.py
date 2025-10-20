import os
import json
import time
import hashlib
import logging
from datetime import datetime, timezone
import requests

from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html

logger = logging.getLogger("article_processor")

ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "3"))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))

def _now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data.get("items"), list):
                    data["items"] = {
                        it["url"]: it for it in data["items"]
                        if isinstance(it, dict) and it.get("url")
                    }
                if "cursor" not in data:
                    data["cursor"] = 0
                return data
        except Exception:
            logger.exception("Errore caricando la cache; rigenero struttura vuota.")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}

def _save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)
    logger.info("💾 Cache aggiornata: %s articoli totali.", len(cache.get("items", {})))

def _hash_links(links):
    h = hashlib.sha256()
    for u in links:
        h.update(u.encode("utf-8"))
    return h.hexdigest()

def _scan_archive(session=None):
    s = session or requests.Session()
    html = fetch_html(ARCHIVE_URL, session=s)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    logger.info("Archivio scansionato: %d articoli trovati.", len(links))
    return links

def _process_one(url, existing_item=None, session=None):
    s = session or requests.Session()
    try:
        item = parse_article(url, session=s)
    except Exception:
        logger.exception("Errore parsing articolo: %s", url)
        return None, False

    content_hash = hashlib.sha256(
        (item.get("content_text", "") + (item.get("modified") or "")).encode("utf-8")
    ).hexdigest()

    if existing_item and existing_item.get("_hash") == content_hash:
        return existing_item, False

    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True

def generate_items(force=False):
    """
    Genera feed aggiornato, poi aggiorna cache e cursor (feed → cache → feed).
    """
    cache = _load_cache()
    session = requests.Session()
    links = _scan_archive(session=session)

    if not links:
        logger.warning("Nessun link trovato nell’archivio.")
        return [], {}

    # 🔁 Ordine cronologico: vecchi → nuovi
    links.sort()
    links_hash = _hash_links(links)

    # 🔹 Riparte dal punto corretto
    cursor = cache.get("cursor", 0)
    if cursor >= len(links):
        cursor = 0

    start = cursor
    end = min(start + MAX_BATCH, len(links))
    batch = links[start:end]
    logger.info("Elaboro batch %d → %d (totale link %d)", start, end, len(links))

    fresh_items = []
    changed_count = 0

    for url in batch:
        existing = cache.get("items", {}).get(url)
        new_item, changed = _process_one(url, existing_item=existing, session=session)
        if new_item:
            fresh_items.append(new_item)
        if changed:
            changed_count += 1
        time.sleep(REQUEST_DELAY)

    # Se non ha trovato nuovi articoli, riusa tutti
    if not fresh_items:
        fresh_items = list(cache.get("items", {}).values())

    fresh_items.sort(key=lambda x: (x.get("published") or ""), reverse=False)
    logger.info("✅ Feed generato: %s articoli totali.", len(fresh_items))

    # ✅ Aggiorna la cache solo dopo aver generato il feed
    for item in fresh_items:
        cache.setdefault("items", {})[item["url"]] = item

    cache["cursor"] = end if end < len(links) else 0
    cache["last_scan"] = _now_iso()
    cache["links_hash"] = links_hash
    _save_cache(cache)

    logger.info("🆕 Batch completato, articoli aggiornati: %s", changed_count)

    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS - Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": datetime.utcnow().replace(tzinfo=timezone.utc)
    }
    return fresh_items, meta

def load_cache():
    return _load_cache()
