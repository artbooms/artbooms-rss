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

ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "1"))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))

def _now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Errore caricamento cache, rigenero")
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

    # 🧹 Filtro di sicurezza: accetta solo link che sembrano articoli veri
    clean_links = []
    for u in links:
        if not u:
            continue
        parsed = urlparse(u)
        if not parsed.path:
            continue
        # accetta solo percorsi che contengono /blog/ o /archivio/
        if any(x in parsed.path for x in ["/blog/", "/archivio/", "/articolo/"]):
            clean_links.append(u)
    logger.info(f"[Filter] {len(clean_links)}/{len(links)} link accettati come articoli validi.")
    return clean_links

def _process_one(url, existing_item=None, session=None):
    s = session or requests.Session()
    try:
        item = parse_article(url, session=s)
    except Exception:
        logger.exception("parse_article failed for %s", url)
        return None, False

    if not isinstance(item, dict):
        logger.warning(f"[Skip] Oggetto non valido per {url}: {type(item)}")
        return None, False

    content_hash = hashlib.sha256((item.get("content_text","") + (item.get("modified") or "")).encode("utf-8")).hexdigest()
    if existing_item and existing_item.get("_hash") == content_hash:
        return existing_item, False
    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True

def _select_batch(links, cache):
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
    cache = _load_cache()
    session = requests.Session()
    links = _scan_archive(session=session)
    links_hash = _hash_links(links)

    if cache.get("links_hash") != links_hash:
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()
        if "cursor" not in cache:
            cache["cursor"] = 0

    # ordinamento archivio
    try:
        if links:
            first = cache.get("items", {}).get(links[0])
            last = cache.get("items", {}).get(links[-1])
            if first and last and first.get("published") and last.get("published"):
                from dateutil import parser as _p
                if _p.parse(first["published"]) > _p.parse(last["published"]):
                    links = list(reversed(links))
            elif len(links) > 50:
                links = list(reversed(links))
    except Exception:
        pass

    batch = links[:] if force else _select_batch(links, cache)

    for url in batch:
        existing = cache.get("items", {}).get(url)
        new_item, changed = _process_one(url, existing_item=existing, session=session)
        if new_item:
            cache.setdefault("items", {})[url] = new_item
            logger.info(f"Processed {url} (changed={changed})")
        time.sleep(REQUEST_DELAY)

    if links:
        cursor = cache.get("cursor", 0) or 0
        cursor = (cursor + len(batch)) % max(1, len(links))
        cache["cursor"] = cursor

    _save_cache(cache)

    # 🔥 Persistenza GitHub
    try:
        os.makedirs("cache", exist_ok=True)
        with open("cache/articles_cache.json", "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Impossibile salvare cache persistente: %s", e)

    # 🧱 Lista ordinata per feed
    def _to_dt(s):
        try:
            from dateutil import parser as _p
            if not s:
                return datetime(1970,1,1, tzinfo=timezone.utc)
            dt = _p.parse(s)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime(1970,1,1, tzinfo=timezone.utc)

    items_list = [it for it in cache.get("items", {}).values() if isinstance(it, dict)]
    items_list.sort(key=lambda x: max(_to_dt(x.get("modified")), _to_dt(x.get("published"))), reverse=True)

    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS - Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms con aggiornamenti automatici"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": datetime.utcnow().replace(tzinfo=timezone.utc)
    }

    return items_list, meta

def load_cache():
    return _load_cache()
