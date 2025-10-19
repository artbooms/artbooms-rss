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
                # 🔧 Fix: se items è lista → converti in dict
                if isinstance(data.get("items"), list):
                    logger.warning("⚠️ Correggo formato cache: items era lista, converto in dict.")
                    data["items"] = {it["url"]: it for it in data["items"] if isinstance(it, dict) and it.get("url")}
                return data
        except Exception:
            logger.exception("Errore caricamento cache, rigenero")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}

def _save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)
    logger.info("🧩 Cache salvata su disco: %s articoli totali.", len(cache.get("items", {})))

def _hash_links(links):
    import hashlib
    h = hashlib.sha256()
    for u in links:
        h.update(u.encode("utf-8"))
    return h.hexdigest()

def _scan_archive(session=None):
    s = session or requests.Session()
    html = fetch_html(ARCHIVE_URL, session=s)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    logger.info("🔍 Archivio scansionato: %d link trovati.", len(links))
    return links

def _process_one(url, existing_item=None, session=None):
    s = session or requests.Session()
    try:
        logger.info("➡️ Parsing articolo: %s", url)
        item = parse_article(url, session=s)
        logger.info("✅ Articolo processato: %s (titolo=%s)", url, item.get("title"))
    except Exception:
        logger.exception("❌ parse_article failed for %s", url)
        return None, False

    content_hash = hashlib.sha256((item.get("content_text", "") + (item.get("modified") or "")).encode("utf-8")).hexdigest()
    if existing_item and existing_item.get("_hash") == content_hash:
        logger.info("⏩ Nessun cambiamento per %s", url)
        return existing_item, False
    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True

def generate_items(force=False):
    cache = _load_cache()
    session = requests.Session()
    links = _scan_archive(session=session)
    links_hash = _hash_links(links)

    if cache.get("links_hash") != links_hash:
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()

    if not links:
        logger.warning("⚠️ Nessun link trovato nell’archivio.")
        return [], {}

    logger.info("🗓️ Primo link: %s", links[0])
    logger.info("🗓️ Ultimo link: %s", links[-1])

    if force:
        batch = links[:]
        cache["cursor"] = 0
        logger.info("⚙️ Modalità FORCED: processerò TUTTI gli articoli.")
    else:
        cursor = cache.get("cursor", 0)
        end = min(cursor + MAX_BATCH, len(links))
        batch = links[cursor:end]
        logger.info("⚙️ Elaboro batch %d → %d", cursor, end)

    for url in batch:
        existing = cache.get("items", {}).get(url)
        new_item, changed = _process_one(url, existing_item=existing, session=session)
        if new_item:
            cache.setdefault("items", {})[url] = new_item
            logger.info("💾 Salvato: %s (changed=%s)", url, changed)
        time.sleep(REQUEST_DELAY)

    cache["cursor"] = (cache.get("cursor", 0) + len(batch)) % max(1, len(links))
    _save_cache(cache)

    items_list = list(cache.get("items", {}).values())
    items_list.sort(key=lambda x: (x.get("published") or ""), reverse=False)

    logger.info("✅ Cache aggiornata, totale %s articoli", len(items_list))
    return items_list, {}
