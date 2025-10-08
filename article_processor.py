import os
import json
import time
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests
from xml.etree import ElementTree as ET
import article_parser

INDEX_FEED_URL = "https://www.artbooms.com/blog?format=rss"
HTTP_TIMEOUT = 15
USER_AGENT = "artbooms-rss/1.0 (+https://www.artbooms.com)"

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _headers():
    return {"User-Agent": USER_AGENT, "Accept": "*/*"}

def read_cache(local_path: str):
    if not os.path.exists(local_path):
        return {"version": 1, "last_updated": None, "articles": []}
    with open(local_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _atomic_write(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def save_cache(local_path: str, cache: Dict[str, Any]):
    cache["last_updated"] = _now_iso()
    _atomic_write(local_path, cache)

def ensure_cache(local_path: str, github_raw_url: Optional[str] = None):
    """Scarica la cache da GitHub se non esiste localmente."""
    if os.path.exists(local_path):
        return
    if github_raw_url:
        try:
            r = requests.get(github_raw_url, headers=_headers(), timeout=HTTP_TIMEOUT)
            if r.status_code == 200 and r.content:
                cache = json.loads(r.content.decode("utf-8"))
                _atomic_write(local_path, cache)
                return
        except Exception:
            pass
    _atomic_write(local_path, {"version": 1, "last_updated": None, "articles": []})

def _fetch_index_items() -> List[Dict[str, str]]:
    r = requests.get(INDEX_FEED_URL, headers=_headers(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        items.append({"title": title, "link": link, "guid": guid, "pub_date": pub_date})
    return items

def _fetch_html(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text

def _article_id(guid: str) -> str:
    return hashlib.sha1(guid.encode("utf-8")).hexdigest()

def update_cache_batch(batch_size: int, local_path: str):
    """Aggiorna batch di articoli: aggiunge nuovi e aggiorna modificati."""
    cache = read_cache(local_path)
    articles = cache.setdefault("articles", [])
    by_url = {a["url"]: a for a in articles}
    index_items = _fetch_index_items()

    to_process = []
    for it in index_items:
        url = it["link"]
        if url not in by_url:
            to_process.append(it)
        else:
            existing = by_url[url]
            if existing.get("published") != it["pub_date"]:
                to_process.append(it)

    to_process = to_process[:batch_size]
    updated = 0

    for it in to_process:
        html = _fetch_html(it["link"])
        parsed = article_parser.parse_article(html, it["link"])
        aid = _article_id(it["guid"])
        article = {
            "id": aid,
            "url": it["link"],
            "guid": it["guid"],
            "title": parsed.get("title") or it["title"],
            "author": parsed.get("author"),
            "description": parsed.get("description"),
            "published": it["pub_date"],
            "modified": parsed.get("modified") or it["pub_date"],
            "fetched_at": _now_iso(),
        }
        by_url[it["link"]] = article
        updated += 1
        time.sleep(0.5)

    if updated > 0:
        cache["articles"] = sorted(by_url.values(), key=lambda a: a["published"], reverse=True)
        save_cache(local_path, cache)

    return {"updated": updated, "total": len(cache["articles"]), "last_updated": cache.get("last_updated")}
