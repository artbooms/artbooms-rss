import json
import os
import logging
from datetime import datetime
from dateutil import parser as dtparser
from urllib.parse import urlparse, urlunparse

# 🔁 usa il nuovo parser rinominato
from article_parser_new import fetch_html, extract_article_links_from_archive_html, parse_article

logger = logging.getLogger("article_processor")

CACHE_PATH = "cache/articles_cache.json"
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
MAX_BATCH = 3


def parse_date_safe(value):
    if not value:
        return datetime.min
    try:
        return dtparser.parse(value)
    except Exception:
        return datetime.min


def canonicalize(url: str) -> str:
    """Forza https://www.artbooms.com/blog/... e rimuove /archivio-completo dal path."""
    if not url:
        return url
    p = urlparse(url)
    path = (p.path or "").replace("/archivio-completo", "").replace("//", "/")
    return urlunparse(("https", "www.artbooms.com", path, "", "", ""))


def load_cache() -> dict:
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


def save_cache(data: dict):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])
    # ordina in modo crescente (vecchi → nuovi)
    items.sort(key=lambda x: parse_date_safe(x.get("published") or x.get("modified")))
    data["items"] = items
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sanitize_cache(data: dict) -> dict:
    """Rimuove o corregge elementi con URL non canonici (es. /archivio-completo nel path)."""
    clean = []
    seen = set()
    for it in data.get("items", []):
        if not isinstance(it, dict):
            continue
        url = canonicalize(it.get("url", ""))
        if ("/blog/" not in url) or any(seg in url for seg in ["/tag/", "/category"]):
            continue
        it["url"] = url
        key = url
        if key in seen:
            continue
        seen.add(key)
        clean.append(it)
    data["items"] = clean
    return data


def generate_items():
    """Scarica articoli e aggiorna la cache in batch da MAX_BATCH, lavorando solo su URL canonici."""
    # 1) Carica e sanifica cache (una volta)
    cache = load_cache()
    cache = sanitize_cache(cache)
    save_cache(cache)

    # 2) Scarica lista link dall’archivio e rendili canonici/deduplicati
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        blog_links = [canonicalize(l) for l in all_links if "/blog/" in l]
        blog_links = list(dict.fromkeys(blog_links))
        logger.info("[Parser] %s link articolo validi trovati.", len(blog_links))
    except Exception as e:
        logger.error("Errore scaricando archivio: %s", e)
        return

    # 3) Mappa URL → articolo in cache
    cached = {canonicalize(a.get("url")): a for a in cache.get("items", [])}
    new_articles = []

    # 4) Scorri in ordine, interrompi dopo MAX_BATCH nuovi/aggiornati
    for link in blog_links:
        link = canonicalize(link)
        cached_art = cached.get(link)
        try:
            art = parse_article(link)
        except Exception as e:
            logger.warning("Errore parse %s: %s", link, e)
            continue

        if not art or not art.get("title"):
            continue

        art["url"] = canonicalize(art.get("url"))

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

    # 5) Aggiorna cache
    for art in new_articles:
        cached[art["url"]] = art

    cache["items"] = list(cached.values())
    save_cache(cache)

    logger.info(
        "[Processor] Aggiunti/aggiornati %s articoli (totale %s).",
        len(new_articles), len(cache["items"])
    )
