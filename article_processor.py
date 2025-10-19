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

# Configurabili via env
ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "1"))   # quanti link processare per run (default 1 = uno per volta)
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))  # delay tra richieste per non sovraccaricare

def _now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            # 👇 Fix automatico se items è una lista (causa errore 'list' object has no attribute get')
            if isinstance(cache.get("items"), list):
                cache["items"] = {
                    it.get("url"): it
                    for it in cache["items"]
                    if isinstance(it, dict) and it.get("url")
                }
                logger.warning("⚠️ Cache convertita automaticamente da lista a dizionario.")
            return cache
        except Exception:
            logger.exception("Errore caricamento cache, rigenero")
    # struttura minima cache
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

    # compute a small content hash per articolo per rilevare modifiche
    content_hash = hashlib.sha256((item.get("content_text","") + (item.get("modified") or "")).encode("utf-8")).hexdigest()
    if existing_item:
        if existing_item.get("_hash") == content_hash:
            return existing_item, False
    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True

def _select_batch(links, cache):
    """
    Sceglie i prossimi link da processare partendo da cache['cursor'].
    Default: ritorna fino a MAX_BATCH link (sequenziali), aggiornamento del cursor gestito in generate_items.
    """
    if not links:
        return []
    cursor = cache.get("cursor", 0) or 0
    n = len(links)
    if cursor >= n:
        cursor = 0
    end = min(cursor + MAX_BATCH, n)
    batch = links[cursor:end]
    # se fine, e batch vuoto, prendi primi elementi
    if not batch and n > 0:
        batch = links[:min(MAX_BATCH, n)]
    return batch

def generate_items(force=False):
    """
    Main: ritorna (items_list, meta)
    - se force=True -> processa tutta la lista di link (slow) (usalo una tantum per popolare cache)
    - altrimenti processa il prossimo batch partendo dal cursor (default MAX_BATCH=1)
    """
    cache = _load_cache()

    session = requests.Session()
    links = _scan_archive(session=session)
    links_hash = _hash_links(links)

    # aggiorno cache se lista link cambiata (nuovi articoli)
    if cache.get("links_hash") != links_hash:
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()
        # se era la prima volta, assicurati cursor a 0
        if "cursor" not in cache:
            cache["cursor"] = 0

    # decide ordine: preferisco processare dal più vecchio al più nuovo.
    try:
        if links:
            first = cache.get("items", {}).get(links[0])
            last = cache.get("items", {}).get(links[-1])
            if first and last and first.get("published") and last.get("published"):
                from dateutil import parser as _p
                fdt = _p.parse(first["published"])
                ldt = _p.parse(last["published"])
                if fdt > ldt:
                    links = list(reversed(links))
            else:
                if len(links) > 50:
                    links = list(reversed(links))
    except Exception:
        pass

    # selezione batch
    if force:
        batch = links[:]  # processa tutto (attenzione: slow)
        cache["cursor"] = 0
    else:
        batch = _select_batch(links, cache)

    # process sequentialmente per non sovraccaricare
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

    # aggiorno cursor
    if links:
        cursor = cache.get("cursor", 0) or 0
        cursor = (cursor + len(batch)) % max(1, len(links))
        cache["cursor"] = cursor

    # salva cache
    _save_cache(cache)

    # prepara items_list ordinata (più nuova prima)
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

    items_list = list(cache.get("items", {}).values())
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
