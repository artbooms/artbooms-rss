import json
import os
import logging
from datetime import datetime
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article

ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
CACHE_PATH = "article_cache/data.json"
MAX_BATCH = 3

logger = logging.getLogger("article_processor")


def load_cache():
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
    except Exception:
        return {"items": []}


def save_cache(data):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])
    # Ordina dal più nuovo al più vecchio
    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    data["items"] = items
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def filter_and_sort_links(links):
    """Filtra i link del blog e li ordina in modo corretto (nuovi prima)."""
    valid = []
    for link in links:
        l = link.lower().strip()
        if "/blog/" not in l:
            continue
        if any(bad in l for bad in ["?", "/tag/", "feed", "favicon", ".jpg", ".png", ".gif", ".jpeg"]):
            continue
        # Escludi i link dei mesi (es: /march-2016/)
        if l.count("/") <= 5 and any(y in l for y in ["-2016", "-2017", "-2018", "-2019", "-2020", "-2021", "-2022", "-2023", "-2024", "-2025"]):
            continue
        valid.append(link)

    # Deduplica mantenendo ordine
    seen = set()
    ordered = [l for l in valid if not (l in seen or seen.add(l))]
    # Ordina dal più nuovo al più vecchio (inverso)
    return list(reversed(ordered))


def generate_items():
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        sorted_links = filter_and_sort_links(all_links)
        logger.info("[Parser] %s link articolo validi trovati.", len(sorted_links))
    except Exception as e:
        logger.error("Errore scaricando archivio: %s", e)
        return

    cache = load_cache()
    cached = {a["url"]: a for a in cache.get("items", [])}
    new_articles = []

    for link in sorted_links:
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
    logger.info("[Cache] Salvata cache ordinata e deduplicata (%s articoli).", len(cache["items"]))
    logger.info("[Processor] Aggiunti/aggiornati %s articoli (totale %s).", len(new_articles), len(cache["items"]))
