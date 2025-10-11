import json
import os
import logging
import time
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
        return data
    except Exception as e:
        logger.error("Errore caricando la cache: %s", e)
        return {"items": []}


def save_cache(data):
    """Salva la cache su disco."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def filter_and_sort_links(links):
    """
    Filtra solo i link validi del blog e li ordina dal più vecchio al più nuovo.
    """
    # Filtra solo gli articoli veri
    valid = [link for link in links if "/blog/" in link and "/tag/" not in link and "?" not in link]
    # Rimuovi duplicati mantenendo ordine
    seen = set()
    ordered = [l for l in valid if not (l in seen or seen.add(l))]
    # 🔁 Ordina cronologicamente (più vecchi prima)
    ordered.sort()
    # 🔁 Inverti: Squarespace li mostra dal più recente → più vecchio
    ordered = list(reversed(ordered))
    return ordered


def generate_items():
    """Scarica nuovi articoli dall’archivio e aggiorna la cache."""
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        sorted_links = filter_and_sort_links(all_links)
        logger.info("[Parser] %s link articolo validi trovati nell'archivio.", len(sorted_links))
    except Exception as e:
        logger.error("Errore scaricando l'archivio: %s", e)
        return

    cache = load_cache()
    cached_urls = {a["url"] for a in cache.get("items", [])}
    new_articles = []

    # Scorre dal più vecchio al più recente
    for link in sorted_links:
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
