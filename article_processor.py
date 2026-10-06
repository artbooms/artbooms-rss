import copy
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests
from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html
from cache_safety import (
    HASH_FIELDS, valid_article, preserve_optional, article_hash,
    merge_cache, cache_transaction, normalize, atomic_json, with_editorial_author,
)

logger = logging.getLogger("article_processor")
ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "cache/articles_cache.json")
MAX_BATCH = max(1, int(os.environ.get("MAX_BATCH", "3")))
REQUEST_DELAY = max(0.0, float(os.environ.get("REQUEST_DELAY", "0.8")))


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load_cache(allow_missing=False):
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as handle:
            return copy.deepcopy(normalize(json.load(handle), "cache locale"))
    except FileNotFoundError:
        if allow_missing:
            return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}
        raise
    # A malformed existing JSON is never treated as an empty archive.


def _save_cache(candidate):
    with cache_transaction(CACHE_PATH):
        current = _load_cache(allow_missing=True)
        merged, stats = merge_cache(current, candidate, allow_empty_current=True)
        if merged != current or not os.path.exists(CACHE_PATH):
            atomic_json(CACHE_PATH, merged)
        if stats["invalid"] or stats["stale"] or stats["conflict"]:
            logger.warning("Protezione cache: %s", stats)
        return merged


def _hash_links(links):
    digest = hashlib.sha256()
    for url in links:
        digest.update(url.encode("utf-8"))
    return digest.hexdigest()


def _scan_archive(session=None):
    html = fetch_html(ARCHIVE_URL, session=session)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    logger.info("Archivio scansionato: %d articoli", len(links))
    return links


def is_valid_article(item):
    return valid_article(item)


def _process_one(url, existing_item=None, session=None):
    existing_item = with_editorial_author(existing_item)
    previous = existing_item if valid_article(existing_item) else None
    try:
        parsed = with_editorial_author(parse_article(url, session=session))
    except Exception:
        logger.exception("Errore parsing: %s", url)
        return previous, False
    if not isinstance(parsed, dict):
        return previous, False
    # Preserve the author and optional fields BEFORE validation and hashing.
    item = preserve_optional(parsed, previous)
    if not valid_article(item):
        logger.warning("Parsing incompleto, scheda precedente conservata: %s", url)
        return previous, False
    if previous is not None:
        if previous.get("_hash") is not None and previous["_hash"] != article_hash(previous):
            logger.warning("Possibile correzione manuale, scheda conservata: %s", url)
            return previous, False
        if all((item.get(k) or "") == (previous.get(k) or "")
               for k in ("url",) + HASH_FIELDS):
            return previous, False
    item["_hash"] = article_hash(item)
    item["_fetched_at"] = _now_iso()
    return item, True


def generate_items(force=False):
    # Network work runs outside the write lock. Merge against the latest file
    # at save time so a concurrent valid correction cannot be blindly replaced.
    with cache_transaction(CACHE_PATH):
        cache = _load_cache(allow_missing=force)
    with requests.Session() as session:
        links = _scan_archive(session=session)
        if not links:
            logger.warning("Nessun link trovato: cache conservata")
            return [], {}
        links_hash = _hash_links(links)
        archive_changed = links_hash != cache.get("links_hash")
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()
        items = cache["items"]
        cursor = cache.get("cursor", 0)
        if type(cursor) is not int or cursor < 0:
            cursor = 0
        cursor %= len(links)
        missing = [u for u in links if not valid_article(items.get(u))]
        if missing:
            # Keep recent additions prompt without letting repeatedly bad pages
            # freeze every other addition and every existing-article refresh.
            if MAX_BATCH == 1 and archive_changed:
                batch = missing[-1:]
            else:
                batch = missing[-(MAX_BATCH - 1):] if MAX_BATCH > 1 else []
                visited = 0
                while len(batch) < MAX_BATCH and visited < len(links):
                    url = links[(cursor + visited) % len(links)]
                    visited += 1
                    if url not in batch:
                        batch.append(url)
                cache["cursor"] = (cursor + visited) % len(links)
        else:
            batch = links[cursor:min(cursor + MAX_BATCH, len(links))]
        changed_items = []
        for url in batch:
            item, changed = _process_one(url, items.get(url), session)
            if changed and item is not None:
                items[item["url"]] = item
                changed_items.append(item)
            time.sleep(REQUEST_DELAY)
        if not missing:
            cache["cursor"] = (cursor + len(batch)) % len(links)
        merged = _save_cache(cache)
        # Report only edits actually accepted by the conservative merge.
        changed_items = [i for i in changed_items
                         if merged["items"].get(i["url"]) == i]
    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS - Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": datetime.now(timezone.utc),
    }
    return changed_items, meta


def load_cache():
    return _load_cache()
