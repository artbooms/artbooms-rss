import json
import os
import logging
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article

logger = logging.getLogger("article_processor")

CACHE_PATH = "cache/articles_cache.json"
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
MAX_BATCH = 3


def load_cache():
    """Carica la cache locale in modo sicuro."""
    if not os.path.exists(CACHE_PATH):
        return {"items": []}

    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"items": data}
        elif not isinstance(data, dict):
            data = {"items": []}
        if "items" not in data:
            data["items"] = []
        # 🔒 ripulisce eventuali voci non-articolo
        before = len(data["items"])
        data["items"] = [i for i in data["items"] if isinstance(i, dict) and "/blog/" in (i.get("url") or "")]
        after = len(data["items"])
        if after != before:
            logging.warning("Sanitized cache: rimossi %s elementi non-articolo", before - after)
        return data
    except Exception as e:
        logger.error("Errore caricando la cache: %s", e)
        return {"items": []}


def save_cache(data):
    """Salva la cache su disco."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def generate_items():
    """Scarica nuovi articoli dall’archivio e aggiorna la cache."""
    try:
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        # Mantieni l'ordine dell'archivio (non sort), link unici già garantiti dal parser
        valid_links = all_links
        logger.info("[Parser] %s link articolo validi trovati nell'archivio.", len(valid_links))
    except Exception as e:
        logger.error("Errore scaricando l'archivio: %s", e)
        return

    cache = load_cache()
    cached_urls = {a["url"] for a in cache.get("items", [])}
    new_articles = []

    # Scorri in ordine (presunto dal più vecchio al più nuovo in archivio)
    for link in valid_links:
        if link not in cached_urls:
            art = parse_article(link)
            if art and art.get("title"):
                new_articles.append(art)
                logger.info("[Processor] Processed %s", link)
            if len(new_articles) >= MAX_BATCH:
                break

    if not new_articles:
        logger.info("Nessun nuovo articolo trovato.")
        return

    cache["items"].extend(new_articles)
    save_cache(cache)
    logger.info("[Processor] Aggiunti %s nuovi articoli alla cache (totale: %s).",
                len(new_articles), len(cache["items"]))
