import json
import os
import logging
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article
from datetime import datetime

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
    """Salva la cache su disco, ordinata e senza duplicati."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])

    # 🔒 Rimuovi duplicati per URL
    unique = {}
    for art in items:
        url = art.get("url")
        if url and url not in unique:
            unique[url] = art
    items = list(unique.values())

    # 🧭 Ordina prima per pubblicazione, poi per modifica
    def sort_key(x):
        pub = x.get("published") or ""
        mod = x.get("modified") or pub
        return (pub, mod)

    items.sort(key=sort_key)
    data["items"] = items

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info("[Cache] Salvata cache ordinata e deduplicata (%s articoli).", len(items))


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
                # fallback se non c'è data
                results.append((link, "9999-12-31T00:00:00Z"))
        except Exception as e:
            logger.warning("Errore leggendo data per %s: %s", link, e)
    results.sort(key=lambda x: x[1])
    return [r[0] for r in results]


def generate_items():
    """Scarica articoli e aggiorna la cache con controllo su modifiche e duplicati."""
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        blog_links = [l for l in all_links if "/blog/" in l and "?" not in l and "/tag/" not in l]
        blog_links = list(set(blog_links))  # rimuove eventuali duplicati di scraping
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
            # ✅ Nuovo articolo
            logger.info("[Processor] NUOVO articolo: %s", link)
            cached[link] = art
            new_articles.append(art)

        else:
            # ✅ Articolo già noto — verifica se modificato
            old_mod = cached_art.get("modified")
            new_mod = art.get("modified")

            if new_mod and old_mod and new_mod != old_mod:
                logger.info("[Processor] Articolo AGGIORNATO: %s (modified %s → %s)", link, old_mod, new_mod)
                cached[link] = art
                new_articles.append(art)

        # 🔁 Limita batch per ciclo
        if len(new_articles) >= MAX_BATCH:
            break

    if not new_articles:
        logger.info("[Processor] Nessun nuovo o aggiornato articolo trovato.")
        return

    # ✅ Aggiorna e salva la cache ordinata e deduplicata
    cache["items"] = list(cached.values())
    save_cache(cache)

    logger.info("[Processor] Aggiunti/aggiornati %s articoli (totale %s).",
                len(new_articles), len(cache["items"]))
