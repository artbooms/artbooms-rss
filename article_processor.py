import os
import json
import time
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests
from xml.etree import ElementTree as ET

# Se preferisci continuare con la tua pagina "archivio", imposta INDEX_FEED_URL di conseguenza.
# L'RSS di Squarespace è leggero e stabile per l'indice.
INDEX_FEED_URL = os.environ.get("INDEX_FEED_URL", "https://www.artbooms.com/blog?format=rss")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "15"))
USER_AGENT = os.environ.get("HTTP_USER_AGENT", "artbooms-rss/1.0 (+https://www.artbooms.com)")

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _headers() -> Dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "*/*"}

# ===== Cache I/O =====

def read_cache(local_path: str) -> Dict[str, Any]:
    if not os.path.exists(local_path):
        return {"version": 1, "last_updated": None, "articles": []}
    with open(local_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def save_cache(local_path: str, cache: Dict[str, Any]) -> None:
    cache["last_updated"] = _now_iso()
    _atomic_write(local_path, cache)

def ensure_cache(local_path: str, github_raw_url: Optional[str] = None) -> None:
    """Se la cache locale non esiste, prova a scaricarla dal raw GitHub; altrimenti inizializza vuota."""
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

# ===== Fetch indice + parsing articoli =====

def _fetch_index_items() -> List[Dict[str, str]]:
    """Ritorna [{title, link, guid, pub_date}] dall'RSS di Squarespace."""
    r = requests.get(INDEX_FEED_URL, headers=_headers(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items: List[Dict[str, str]] = []
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

# Usa il tuo parser già corretto
import article_parser  # parse_article(html, url) -> dict con titolo, autore, descrizione, ecc.

def _article_id(guid_or_url: str) -> str:
    return hashlib.sha1(guid_or_url.encode("utf-8")).hexdigest()

def _merge_article(cache: Dict[str, Any], article: Dict[str, Any]) -> None:
    articles = cache.setdefault("articles", [])
    idx = next((i for i, a in enumerate(articles) if a.get("id") == article.get("id")), None)
    if idx is None:
        articles.append(article)
    else:
        articles[idx] = article
    # ordina per data se disponibile
    def key(a): return a.get("published_iso") or a.get("published") or ""
    articles.sort(key=key, reverse=True)

def _normalize_article(parsed: Dict[str, Any], link: str, guid: str, pub_date: str) -> Dict[str, Any]:
    aid = _article_id(guid or link)
    base = {
        "id": aid,
        "url": link,
        "guid": guid or link,
        "fetched_at": _now_iso(),
        "published": pub_date,
        "published_iso": parsed.get("published_iso"),
    }
    base.update(parsed or {})
    return base

def update_cache_batch(batch_size: int, local_path: str) -> Dict[str, Any]:
    """
    Elabora fino a `batch_size` articoli:
      - legge l'indice RSS
      - identifica i nuovi link (e riprocessa i più recenti per update minori)
      - salva su disco se cambia qualcosa
    """
    cache = read_cache(local_path)
    existing_urls = {a.get("url") for a in cache.get("articles", [])}

    index_items = _fetch_index_items()

    # Selezione worklist: prima i nuovi, poi (se serve) i più recenti già presenti
    to_process: List[Dict[str, str]] = [it for it in index_items if it["link"] not in existing_urls]
    if len(to_process) < batch_size:
        for it in index_items:
            if it["link"] in existing_urls and it not in to_process:
                to_process.append(it)
    to_process = to_process[:batch_size]

    updated = 0
    for it in to_process:
        html = _fetch_html(it["link"])
        parsed = article_parser.parse_article(html, it["link"])
        article = _normalize_article(parsed, it["link"], it["guid"], it["pub_date"])
        _merge_article(cache, article)
        updated += 1
        time.sleep(0.5)  # gentilezza verso Squarespace

    if updated > 0:
        save_cache(local_path, cache)

    return {"updated": updated, "total": len(cache.get("articles", [])), "last_updated": cache.get("last_updated")}
