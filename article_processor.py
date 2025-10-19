import os
import json
import time
import hashlib
import logging
from datetime import datetime, timezone
import requests

from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html

logger = logging.getLogger("article_processor")

# ============================================================
# Config base (allineata a app.py)
# ============================================================
ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL    = os.environ.get("BASE_URL",    "https://www.artbooms.com")
CACHE_PATH  = os.environ.get("CACHE_PATH",  "cache/articles_cache.json")  # 👈 stesso file di app.py
MAX_BATCH   = int(os.environ.get("MAX_BATCH", "3"))  # quanti articoli per ciclo
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))  # prudenza sulle richieste


def _now_iso() -> str:
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _ensure_cache_dir():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)


def _load_cache():
    _ensure_cache_dir()
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Se era una lista, normalizza a dict keyed per URL
                if isinstance(data, list):
                    data = {"items": {it["url"]: it for it in data if isinstance(it, dict) and it.get("url")},
                            "cursor": 0, "last_scan": None, "links_hash": None}
                if isinstance(data.get("items"), list):
                    data["items"] = {it["url"]: it for it in data["items"] if isinstance(it, dict) and it.get("url")}
                # Struttura minima
                data.setdefault("items", {})
                data.setdefault("cursor", 0)
                data.setdefault("last_scan", None)
                data.setdefault("links_hash", None)
                return data
        except Exception:
            logger.exception("Errore caricando la cache; rigenero struttura vuota.")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}


def _save_cache(cache):
    _ensure_cache_dir()
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)
    logger.info("💾 Cache aggiornata: %s articoli totali.", len(cache.get("items", {})))


def _hash_links(links):
    h = hashlib.sha256()
    for u in links:
        h.update(u.encode("utf-8"))
    return h.hexdigest()


def _scan_archive(session=None):
    s = session or requests.Session()
    html = fetch_html(ARCHIVE_URL, session=s)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    # 👉 NON riordino: mi fido dell’ordine (vecchi → nuovi) dato dal parser
    logger.info("Archivio scansionato: %d articoli trovati.", len(links))
    return links


def _process_one(url, existing_item=None, session=None):
    s = session or requests.Session()
    try:
        item = parse_article(url, session=s)
    except Exception:
        logger.exception("Errore parsing articolo: %s", url)
        return None, False

    # Hash contenuto per capire se è cambiato
    content_hash = hashlib.sha256(
        (item.get("content_text", "") + (item.get("modified") or "")).encode("utf-8")
    ).hexdigest()

    if existing_item and existing_item.get("_hash") == content_hash:
        # invariato
        return existing_item, False

    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True


def generate_items(force=False):
    """
    1) Legge la lista link (in ordine cronologico vecchi→nuovi)
    2) Seleziona il batch (o tutti se force=True)
    3) COSTRUISCE GLI ITEM PER IL FEED (fresh_items)
    4) SOLO DOPO aggiorna la cache con i nuovi/aggiornati
    5) Avanza il cursore
    """
    cache = _load_cache()
    session = requests.Session()

    links = _scan_archive(session=session)
    if not links:
        logger.warning("Nessun link trovato nell’archivio.")
        return [], {}

    links_hash = _hash_links(links)

    # Se cambia la lista link, aggiorna markers
    if cache.get("links_hash") != links_hash:
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()
        cache.setdefault("cursor", 0)

    # Batch: rispetta cursore (vecchi → nuovi)
    if force:
        batch = links[:]  # attenzione: lentezza, solo per popolamenti una tantum
        cache["cursor"] = 0
        logger.info("Modalità FORCED: processerò %d articoli.", len(batch))
    else:
        cursor = cache.get("cursor", 0) or 0
        end = min(cursor + MAX_BATCH, len(links))
        batch = links[cursor:end]
        if not batch and links:
            # se siamo a fine corsa, riparti dall'inizio
            batch = links[:min(MAX_BATCH, len(links))]
            cursor = 0
        logger.info("Elaboro batch %d → %d (totale link %d)", cursor, cursor + len(batch), len(links))

    # 3) FEED FIRST: costruisci gli item aggiornati
    fresh_items = []
    for url in batch:
        existing = cache.get("items", {}).get(url)
        new_item, changed = _process_one(url, existing_item=existing, session=session)
        if new_item:
            fresh_items.append(new_item)
        time.sleep(REQUEST_DELAY)

    # Se non ho elaborato nulla di nuovo, usa gli ultimi conosciuti (così il feed non resta vuoto)
    if not fresh_items:
        fresh_items = list(cache.get("items", {}).values())

    # Ordina gli item per data di pubblicazione (vecchi → nuovi)
    def _to_key(a):
        # usa published, altrimenti modified, come stringhe ISO già ordinate
        return (a.get("published") or a.get("modified") or "")
    fresh_items.sort(key=_to_key)

    logger.info("✅ Feed generato: %s articoli totali.", len(fresh_items))

    # 4) SOLO ORA aggiorna la cache con i NUOVI/AGGIORNATI
    for it in fresh_items:
        url = it.get("url")
        if not url:
            continue
        old = cache.get("items", {}).get(url)
        if not old or old.get("_hash") != it.get("_hash"):
            cache.setdefault("items", {})[url] = it

    # 5) Avanza cursore
    #    (se ho consumato K elementi a partire da cursor, avanzo di K)
    current_cursor = cache.get("cursor", 0) or 0
    cache["cursor"] = (current_cursor + len(batch)) % max(1, len(links))
    _save_cache(cache)

    # Metadati per chi genera il feed
    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS - Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": datetime.utcnow().replace(tzinfo=timezone.utc),
    }

    return fresh_items, meta


def load_cache():
    return _load_cache()
