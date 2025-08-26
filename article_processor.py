import json
import os
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from article_parser import extract_article_links, parse_article

ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
DEFAULT_AUTHOR = os.environ.get("DEFAULT_AUTHOR", "ARTBOOMS")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "6"))

HEADERS = {
    "User-Agent": "artbooms-rss/1.0 (+https://www.artbooms.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def _now_utc():
    return datetime.utcnow().replace(tzinfo=timezone.utc)

def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return {"items": {}, "last_scan": None}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": {}, "last_scan": None}

def _save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def fetch(url, etag=None, last_modified=None):
    headers = dict(HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    return r

def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def _scan_archive(force=False):
    r = fetch(ARCHIVE_URL)
    r.raise_for_status()
    links = extract_article_links(r.text, BASE_URL)
    return links

def _process_one(url: str, cache_item: dict | None, force=False):
    etag = cache_item.get("etag") if cache_item else None
    lastmod = cache_item.get("last_modified") if cache_item else None

    try:
        r = fetch(url, etag=None if force else etag, last_modified=None if force else lastmod)
    except Exception:
        return cache_item, False

    if r.status_code == 304 and cache_item:
        return cache_item, False
    if r.status_code != 200:
        return cache_item, False

    item = parse_article(r.text, url, default_author=DEFAULT_AUTHOR)
    text_hash = _hash_text(item.get("content_text"))
    changed = text_hash != (cache_item.get("content_hash") if cache_item else None)

    headers = r.headers
    item.update({
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "content_hash": text_hash,
    })

    return item, changed

def generate_items(force=False):
    cache = _load_cache()
    links = _scan_archive(force=force)
    cache.setdefault("items", {})
    changed_any = False

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as ex:
        futures = {ex.submit(_process_one, url, cache["items"].get(url), force): url for url in links}
        for fut in as_completed(futures):
            url = futures[fut]
            new_item, changed = fut.result()
            if new_item:
                cache["items"][url] = new_item
                changed_any = changed_any or changed

    cache["last_scan"] = _now_utc().isoformat()
    _save_cache(cache)

    items_list = list(cache["items"].values())
    def to_dt(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime(1970,1,1, tzinfo=timezone.utc)
    items_list.sort(key=lambda x: to_dt(x.get("published") or x.get("modified") or "1970-01-01T00:00:00+00:00"), reverse=True)

    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS - Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms con aggiornamenti automatici"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": _now_utc(),
    }

    return items_list, meta

def load_cache():
    return _load_cache()
