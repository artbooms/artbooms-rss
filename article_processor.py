import os
import json
import time
import hashlib
import logging
from datetime import datetime, timezone
import requests

from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html

logger = logging.getLogger("article_processor")

# Config base
ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "3"))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))


def _now_iso():
    """Restituisce la data/ora corrente in ISO UTC"""
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _load_cache():
    """Carica la cache locale e corregge il formato se necessario"""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data.get("items"), list):
                    # Corregge il formato lista → dizionario
                    data["items"] = {
                        it["url"]: it for it in data["items"]
                        if isinstance(it, dict) and it.get("url")
                    }
                return data
        except Exception:
            logger.exception("Errore caricamento cache, rigenero")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}


def _save_cache(cache):
    """Salva la cache su disco"""
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)
    logger.info("💾 Cache aggiornata: %s articoli totali.", len(cache.get("items", {})))


def _hash_links(links):
    """Crea hash della lista dei link"""
    h = hashlib.sha256()
    for u in links:
        h.update(u.encode("utf-8"))
    return h.hexdigest()


def _scan_archive(session=None):
    """Legge la pagina archivio e restituisce la lista di link ordinati"""
    s = session or requests.Session()
    html = fetch_html(ARCHIVE_URL, session=s)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    logger.info("Archivio scansionato: %d articoli trovati.", len(links))
    return links


def _process_one(url, existing_item=None, session=None):
    """Scarica e parse un articolo singolo"""
    s = session or requests.Session()
    try:
        item = parse_article(url, session=s)
    except Exception:
        logger.exception("Errore parsing articolo: %s", url)
        return None, False

    # Calcola hash del contenuto per verificare modifiche
    content_hash = hashlib.sha256(
        (item.get("content_text", "") + (item.get("modified") or "")).encode("utf-8")
    ).hexdigest()

    if existing_item and existing_item.get("_hash") == content_hash:
        return existing_item, False

    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True


def generate_items(force=False):
    """
    Genera feed aggiornato (articoli ordinati dal più vecchio al più nuovo)
    e POI salva il risultato nella cache come memoria persistente.
    """
    cache = _load_cache()
    session = requests.Session()

    links = _scan_archive(session=session)
    if not links:
        logger.warning("Nessun link trovato nell’archivio.")
        return [], {}

    # Ordine cronologico: vecchi → nuovi
    links.sort()
    links_hash = _hash_links(links)

    # Batch di lavoro
    if force:
        batch = links
        cache["cursor"] = 0
        logger.info("Modalità FORCED: processerò tutti gli articoli.")
    else:
        cursor = cache.get("cursor", 0)
        end = min(cursor + MAX_BATCH, len(links))
        batch = links[cursor:end]
        logger.info("Elaboro batch %d → %d", cursor, end)

    # 🧠 PRIMA costruiamo il feed
    fresh_items = []
    for url in batch:
        existing = cache.get("items", {}).get(url)
        new_item, changed = _process_one(url, existing_item=existing, session=session)
        if new_item:
            fresh_items.append(new_item)
        time.sleep(REQUEST_DELAY)

    # Se nessun articolo nuovo, riusa quelli della cache
    if not fresh_items:
        fresh_items = list(cache.get("items", {}).values())

    # Ordina per data di pubblicazione (dal più vecchio al più recente)
    fresh_items.sort(key=lambda x: (x.get("published") or ""), reverse=False)

    # Feed completo
    logger.info("✅ Feed generato: %s articoli totali.", len(fresh_items))

    # 🗂️ SOLO ORA aggiorniamo la cache
    for item in fresh_items:
        cache.setdefault("items", {})[item["url"]] = item

    cache["cursor"] = (cache.get("cursor", 0) + len(batch)) % max(1, len(links))
    cache["last_scan"] = _now_iso()
    cache["links_hash"] = links_hash
    _save_cache(cache)

    # Metadati del feed
    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS - Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": datetime.utcnow().replace(tzinfo=timezone.utc)
    }

    return fresh_items, meta


def load_cache():
    """Restituisce la cache corrente"""
    return _load_cache()
