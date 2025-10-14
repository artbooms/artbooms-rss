import json
import os
import logging
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article

logger = logging.getLogger("article_processor")

CACHE_PATH = "cache/articles_cache.json"
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
MAX_BATCH = 3


def normalize_url(url: str) -> str:
    """Forza https, rimuove spazi e slash finale."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if url.endswith("/"):
        url = url[:-1]
    return url


def load_cache():
    """Carica la cache locale e normalizza gli URL."""
    if not os.path.exists(CACHE_PATH):
        return {"items": []}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"items": data}
        if "items" not in data:
            data["items"] = []
        for a in data["items"]:
            if isinstance(a, dict) and "url" in a:
                a["url"] = normalize_url(a["url"])
        return data
    except Exception as e:
        logger.error("Errore caricando la cache: %s", e)
        return {"items": []}


def save_cache(data):
    """Salva la cache in ordine cronologico crescente per 'published'."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])
    items.sort(key=lambda x: x.get("published") or "")
    data["items"] = items
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("[Cache] Salvata cache (%s articoli).", len(items))


def generate_items():
    """Processa 3 articoli per ciclo: dal più vecchio al più nuovo. Considera anche 'modified'."""
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        html = fetch_html(ARCHIVE_URL)
        all_links = extract_article_links_from_archive_html(html, ARCHIVE_URL)

        # Filtra solo articoli veri; esclude link mese, tag e qualsiasi query
        raw_links = []
        seen = set()
        for l in all_links:
            if "/blog/" not in l:
                continue
            if "?month=" in l or "/tag/" in l or "?" in l:
                continue
            u = normalize_url(l)
            if u not in seen:
                seen.add(u)
                raw_links.append(u)

        # L'archivio è newest→oldest: inverti per processare oldest→newest
        blog_links = list(reversed(raw_links))
        logger.info("[Parser] %s link articolo validi (oldest→newest).", len(blog_links))
    except Exception as e:
        logger.error("Errore scaricando archivio: %s", e)
        return

    cache = load_cache()
    cached_by_url = {normalize_url(a.get("url")): a for a in cache.get("items", [])}
    batch = []

    for link in blog_links:
        cached_art = cached_by_url.get(link)

        if not cached_art:
            # Nuovo articolo
            art = parse_article(link)
            if not art or not art.get("title"):
                continue
            art["url"] = normalize_url(art.get("url") or link)
            batch.append(art)
            logger.info("[Processor] NUOVO articolo: %s", link)

        else:
            # Articolo già noto: se 'modified' cambia, reinseriscilo
            fresh = None
            try:
                fresh = parse_article(link)
            except Exception:
                pass
            if fresh:
                old_mod = (cached_art.get("modified") or "").strip()
                new_mod = (fresh.get("modified") or "").strip()
                if new_mod and new_mod != old_mod:
                    fresh["url"] = normalize_url(fresh.get("url") or link)
                    cached_by_url[link] = fresh
                    batch.append(fresh)
                    logger.info("[Processor] Articolo AGGIORNATO: %s (modified)", link)

        if len(batch) >= MAX_BATCH:
            break

    if not batch:
        logger.info("Nessun nuovo o aggiornato articolo trovato.")
        return

    # Aggiorna cache (sovrascrive gli aggiornati; aggiunge i nuovi)
    for art in batch:
        cached_by_url[normalize_url(art["url"])] = art

    cache["items"] = list(cached_by_url.values())
    save_cache(cache)
    logger.info("[Processor] Aggiunti/aggiornati %s articoli (totale %s).",
                len(batch), len(cache["items"]))
