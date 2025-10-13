import json
import os
import logging
from datetime import datetime
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article

logger = logging.getLogger("article_processor")

CACHE_PATH = "cache/articles_cache.json"
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
MAX_BATCH = 3


def load_cache():
    """Carica la cache locale."""
    if not os.path.exists(CACHE_PATH):
        return {"items": []}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"items": data}
        if "items" not in data:
            data["items"] = []
        return data
    except Exception as e:
        logger.error("Errore caricando la cache: %s", e)
        return {"items": []}


def save_cache(data):
    """Salva la cache su disco, ordinata e ripulita."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])

    # 🔹 Filtra solo articoli veri
    filtered = []
    seen = set()
    for art in items:
        url = art.get("url", "")
        if not url or "/blog/" not in url:
            continue
        if any(x in url for x in ["?month=", "/tag/", "favicon", ".ico", ".jpg", ".png"]):
            continue
        # normalizza URL relativi
        if url.startswith("/"):
            url = f"https://www.artbooms.com{url}"
            art["url"] = url
        # evita duplicati
        if url in seen:
            continue
        seen.add(url)
        filtered.append(art)

    # 🔹 Deduplica mantenendo il più recente
    deduped = {}
    for art in filtered:
        url = art["url"]
        mod = art.get("modified") or ""
        if url not in deduped or mod > (deduped[url].get("modified") or ""):
            deduped[url] = art

    # 🔹 Ordina cronologicamente (più vecchi prima)
    ordered = sorted(
        deduped.values(),
        key=lambda x: x.get("published") or x.get("modified") or ""
    )

    data["items"] = ordered
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_article_dates(links):
    """Restituisce lista di link con data (ordinati per pubblicazione)."""
    results = []
    for link in links:
        try:
            art = parse_article(link)
            pub = art.get("published")
            if pub:
                results.append((link, pub))
            else:
                results.append((link, "9999-12-31T00:00:00Z"))
        except Exception as e:
            logger.warning("Errore leggendo data per %s: %s", link, e)
    results.sort(key=lambda x: x[1])
    return [r[0] for r in results]


def generate_items():
    """Scarica articoli e aggiorna la cache con controllo 'modified'."""
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        # Filtra solo i link veri del blog
        blog_links = [
            l for l in all_links
            if "/blog/" in l and "?month=" not in l and "/tag/" not in l and "favicon" not in l
        ]
        logger.info("[Parser] %s link articolo validi trovati.", len(blog_links))
    except Exception as e:
        logger.error("Errore scaricando archivio: %s", e)
        return

    cache = load_cache()
    cached = {a["url"]: a for a in cache.get("items", [])}
    new_articles = []

    ordered_links = fetch_article_dates(blog_links)

    for link in ordered_links:
        art = parse_article(link)
        if not art or not art.get("title"):
            continue

        cached_art = cached.get(link)
        if not cached_art:
            logger.info("[Processor] NUOVO articolo: %s", link)
            new_articles.append(art)
        else:
            old_mod = cached_art.get("modified")
            new_mod = art.get("modified")
            if new_mod and old_mod and new_mod != old_mod:
                logger.info("[Processor] Articolo AGGIORNATO: %s (modified)", link)
                cached[link] = art
                new_articles.append(art)

        if len(new_articles) >= MAX_BATCH:
            break

    if not new_articles:
        logger.info("Nessun nuovo o aggiornato articolo trovato.")
        return

    # Aggiorna cache
    for art in new_articles:
        cached[art["url"]] = art

    cache["items"] = list(cached.values())
    save_cache(cache)

    logger.info("[Processor] Aggiunti/aggiornati %s articoli (totale %s).",
                len(new_articles), len(cache["items"]))
