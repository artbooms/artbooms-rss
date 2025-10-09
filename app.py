import os
import json
import time
import requests
from datetime import datetime, timezone
from article_parser import parse_article

CACHE_FILE = "articles_cache.json"
REMOTE_BASE = "https://www.artbooms.com"
ARCHIVE_URL = f"{REMOTE_BASE}/blog?offset={{offset}}"


def load_cache():
    """Carica la cache locale se esiste."""
    if not os.path.exists(CACHE_FILE):
        return {"items": {}, "cursor": 0, "last_scan": None}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": {}, "cursor": 0, "last_scan": None}


def save_cache(cache):
    """Salva la cache locale."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_articles(offset=0, limit=3):
    """Scarica un blocco di articoli da Squarespace (3 alla volta)."""
    url = ARCHIVE_URL.format(offset=offset)
    resp = requests.get(url, timeout=20)
    if resp.status_code != 200:
        return []
    html = resp.text
    return parse_article(html)


def generate_items(force=False):
    """Scarica articoli, aggiorna la cache e restituisce il numero di nuovi articoli."""
    cache = load_cache()
    items = cache.get("items", {})
    cursor = cache.get("cursor", 0)
    new_count = 0

    # Ogni ciclo scarica 3 articoli nuovi
    articles = fetch_articles(offset=cursor)
    if not articles:
        print("[Processor] Nessun nuovo articolo trovato.")
        cache["last_scan"] = datetime.now(timezone.utc).isoformat()
        save_cache(cache)
        return 0

    for art in articles:
        art_id = art.get("id") or art.get("url")
        if not art_id:
            continue
        old = items.get(art_id)
        if not old or old.get("modified") != art.get("modified"):
            items[art_id] = art
            new_count += 1

    if new_count:
        cache["items"] = items
        cache["cursor"] = cursor + len(articles)
        cache["last_scan"] = datetime.now(timezone.utc).isoformat()
        save_cache(cache)

        # ✅ Salva anche la cache completa su disco per GitHub Actions
        os.makedirs("cache", exist_ok=True)
        with open("cache/articles_cache.json", "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        print(f"[Processor] ✅ {new_count} nuovi articoli aggiunti. Totale: {len(items)}")
    else:
        print("[Processor] Nessuna modifica rilevata negli articoli esistenti.")

    return new_count
