import json
import os
import logging
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
    """Salva la cache su disco ordinata per data di pubblicazione."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])
    items.sort(key=lambda x: x.get("published") or "")
    data["items"] = items
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def generate_items():
    """Scarica nuovi articoli dall’archivio e aggiorna la cache (3 per ciclo)."""
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        # 🔹 Solo articoli veri (esclude tag e link mese)
        blog_links = [
            l for l in all_links
            if "/blog/" in l and "?month=" not in l and "/tag/" not in l and "?" not in l
        ]
        logger.info("[Parser] %s link articolo validi trovati.", len(blog_links))
    except Exception as e:
        logger.error("Errore scaricando archivio: %s", e)
        return

    cache = load_cache()
    cached = {a["url"]: a for a in cache.get("items", [])}
    new_articles = []

    # 🔹 Ordina dal più vecchio al più recente (per garantire continuità)
    blog_links.sort()

    for link in blog_links:
        if link in cached:
            continue

        art = parse_article(link)
        if not art or not art.get("title"):
            continue

        new_articles.append(art)
        logger.info("[Processor] NUOVO articolo: %s", link)

        if len(new_articles) >= MAX_BATCH:
            break

    if not new_articles:
        logger.info("Nessun nuovo articolo trovato.")
        return

    cache["items"].extend(new_articles)
    save_cache(cache)

    logger.info("[Processor] Aggiunti %s nuovi articoli (totale: %s).",
                len(new_articles), len(cache["items"]))
