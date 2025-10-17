import json, os, logging
from datetime import datetime
from dateutil import parser as dateparser
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article

logger = logging.getLogger("article_processor")

# ✅ MAX BATCH qui (come da richiesta)
MAX_BATCH = 3

CACHE_PATH = "cache/articles_cache.json"
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"

def _date_safe(s):
    if not s:
        return datetime.min
    try:
        return dateparser.parse(s)
    except Exception:
        return datetime.min

def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {"items": []}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"items": data}
        return data
    except Exception as e:
        logger.error("Errore caricando la cache: %s", e)
        return {"items": []}

def save_cache(data):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])
    # ordine crescente (vecchi → nuovi) in cache
    items.sort(key=lambda x: _date_safe(x.get("published") or x.get("modified")))
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)
    logger.info("[Processor] Cache salvata (%s articoli).", len(items))

def generate_items():
    """
    - Legge l'archivio
    - Estrae i link *ordinati cronologicamente* (grazie al parser)
    - Aggiunge al massimo MAX_BATCH articoli nuovi (o aggiornati)
    """
    html = fetch_html(ARCHIVE_URL)
    ordered_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)  # già vecchi → nuovi

    cache = load_cache()
    cached_by_url = {it.get("url"): it for it in cache.get("items", [])}

    added = 0
    for link in ordered_links:
        if link in cached_by_url:
            # check aggiornamento via modified
            try:
                art = parse_article(link)
                old_mod = cached_by_url[link].get("modified")
                new_mod = art.get("modified")
                if new_mod and old_mod and new_mod != old_mod:
                    logger.info("[Processor] AGGIORNATO: %s", link)
                    cached_by_url[link] = art
                    added += 1
            except Exception as e:
                logger.warning("Skip aggiornamento %s: %s", link, e)
        else:
            # nuovo
            try:
                art = parse_article(link)
                if art and art.get("title"):
                    logger.info("[Processor] NUOVO: %s — %s", art["url"], art.get("published") or art.get("modified"))
                    cached_by_url[link] = art
                    added += 1
            except Exception as e:
                logger.warning("Skip nuovo %s: %s", link, e)

        if added >= MAX_BATCH:
            break

    if added == 0:
        logger.info("[Processor] Nessun nuovo/aggiornato articolo (batch=%s).", MAX_BATCH)
        return

    cache["items"] = list(cached_by_url.values())
    save_cache(cache)
