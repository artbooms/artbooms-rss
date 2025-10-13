import json
import os
import logging
import re
import time
from urllib.parse import urljoin
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article

logger = logging.getLogger("article_processor")

CACHE_PATH = "cache/articles_cache.json"
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
BASE_URL = "https://www.artbooms.com"
MAX_BATCH = 3


def load_cache():
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
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])
    items.sort(key=lambda x: x.get("published") or "")
    data["items"] = items
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("[Cache] Salvata cache con %s articoli.", len(items))


def fetch_article_dates(links):
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


def extract_all_articles():
    """Estrae i link mese e poi i link articoli da ogni mese."""
    html = fetch_html(ARCHIVE_URL)

    # ✅ 1. Trova i link mese direttamente nell'HTML
    month_links = re.findall(r'href=["\']([^"\']*month=[^"\']*)["\']', html, re.IGNORECASE)
    month_links = [urljoin(BASE_URL, l) for l in month_links if "/blog" in l]
    month_links = list(dict.fromkeys(month_links))  # rimuovi duplicati mantenendo ordine

    logger.info("[Parser] %s link mese trovati (regex diretta).", len(month_links))

    if not month_links:
        logger.warning("[Parser] Nessun link mese trovato, possibile variazione nel markup.")
        return []

    all_articles = set()

    # ✅ 2. Scorri ogni link mese
    for mlink in month_links:
        try:
            logger.info("[Parser] Leggo articoli da: %s", mlink)
            mhtml = fetch_html(mlink)
            articles = re.findall(r'href=["\'](/blog/[^"\']+)["\']', mhtml)
            full_articles = [urljoin(BASE_URL, a) for a in articles if "/tag/" not in a]
            all_articles.update(full_articles)
            logger.info("[Parser] %s articoli trovati nel mese %s", len(full_articles), mlink)
            time.sleep(0.5)
        except Exception as e:
            logger.warning("Errore leggendo %s: %s", mlink, e)

    logger.info("[Parser] Totale articoli estratti da tutti i mesi: %s", len(all_articles))
    return list(all_articles)


def generate_items():
    """Scarica articoli e aggiorna la cache con controllo 'modified'."""
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        all_links = extract_all_articles()
        logger.info("[Parser] %s link articolo validi trovati in totale.", len(all_links))
    except Exception as e:
        logger.error("Errore scaricando archivio: %s", e)
        return

    if not all_links:
        logger.warning("Nessun articolo trovato, interruzione processo.")
        return

    cache = load_cache()
    cached = {a["url"]: a for a in cache.get("items", [])}
    new_articles = []

    ordered_links = fetch_article_dates(all_links)

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

    for art in new_articles:
        cached[art["url"]] = art

    cache["items"] = list(cached.values())
    save_cache(cache)

    logger.info("[Processor] Aggiunti/aggiornati %s articoli (totale %s).",
                len(new_articles), len(cache["items"]))
