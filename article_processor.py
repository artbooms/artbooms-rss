import os
import json
import time
import hashlib
import logging
import subprocess
from datetime import datetime, timezone
import requests

from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html

logger = logging.getLogger("article_processor")

# ============================================================
# Config base
# ============================================================
ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL    = os.environ.get("BASE_URL", "https://www.artbooms.com")
CACHE_PATH  = os.environ.get("CACHE_PATH",  "cache/articles_cache.json")
MAX_BATCH   = int(os.environ.get("MAX_BATCH", "3"))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.8"))


def _now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _ensure_cache_dir():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)


def _load_cache():
    _ensure_cache_dir()
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    data = {"items": {it["url"]: it for it in data if it.get("url")},
                            "cursor": 0, "last_scan": None, "links_hash": None}
                if isinstance(data.get("items"), list):
                    data["items"] = {it["url"]: it for it in data["items"] if it.get("url")}
                data.setdefault("items", {})
                data.setdefault("cursor", 0)
                data.setdefault("last_scan", None)
                data.setdefault("links_hash", None)
                return data
        except Exception:
            logger.exception("Errore caricando la cache; rigenero vuota.")
    return {"items": {}, "cursor": 0, "last_scan": None, "links_hash": None}


def _save_cache(cache):
    _ensure_cache_dir()
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)
    logger.info("💾 Cache aggiornata: %s articoli totali.", len(cache.get("items", {})))

    # 🔄 Sincronizza su GitHub (forzato)
    try:
        subprocess.run(["git", "config", "user.name", "rss-bot"], check=False)
        subprocess.run(["git", "config", "user.email", "rss-bot@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", CACHE_PATH], check=False)
        subprocess.run(["git", "commit", "-m", f"Update cache {time.strftime('%Y-%m-%d %H:%M:%S')}"], check=False)
        subprocess.run(["git", "push"], check=False)
        logger.info("🚀 Cache sincronizzata su GitHub con successo.")
    except Exception as e:
        logger.warning("⚠️ Errore nel push su GitHub: %s", e)


def _hash_links(links):
    h = hashlib.sha256()
    for u in links:
        h.update(u.encode("utf-8"))
    return h.hexdigest()


def _scan_archive(session=None):
    s = session or requests.Session()
    html = fetch_html(ARCHIVE_URL, session=s)
    links = extract_article_links_from_archive_html(html, BASE_URL)
    logger.info("Archivio scansionato: %d articoli trovati.", len(links))
    return links


def _process_one(url, existing_item=None, session=None):
    s = session or requests.Session()
    try:
        item = parse_article(url, session=s)
    except Exception:
        logger.exception("Errore parsing articolo: %s", url)
        return None, False

    # ✅ Hash basato sui campi SEO (non su content_text)
    hash_basis = (
        (item.get("title") or "") +
        (item.get("description") or "") +
        (item.get("author") or "") +
        (item.get("published") or "") +
        (item.get("modified") or "") +
        (item.get("image") or "")
    )
    content_hash = hashlib.sha256(hash_basis.encode("utf-8")).hexdigest()

    if existing_item and existing_item.get("_hash") == content_hash:
        return existing_item, False

    item["_hash"] = content_hash
    item["_fetched_at"] = _now_iso()
    return item, True


def generate_items(force=False):
    cache = _load_cache()
    session = requests.Session()

    links = _scan_archive(session=session)
    if not links:
        logger.warning("Nessun link trovato nell’archivio.")
        return [], {}

    links_hash = _hash_links(links)
    if cache.get("links_hash") != links_hash:
        cache["links_hash"] = links_hash
        cache["last_scan"] = _now_iso()
        cache.setdefault("cursor", 0)

    cursor = cache.get("cursor", 0) or 0

    # ============================================================
    # ✅ FIX DEFINITIVO DEL RITARDO (fast-path):
    # Se la cache NON è vuota e ci sono URL nuovi nell’archivio (non ancora in cache),
    # processiamo SUBITO gli ultimi MAX_BATCH nuovi (i più recenti).
    #
    # - NON cambia l’ordine del feed finale.
    # - NON ricarica tutto.
    # - Mantiene la "prima carica": se cache è vuota, si parte dal vecchio (cursor).
    # ============================================================
    items_dict = cache.get("items", {}) or {}
    use_missing_batch = False
    batch = []

    if items_dict:
        missing = [u for u in links if u not in items_dict]
        if missing:
            batch = missing[-MAX_BATCH:]  # links è old→new: gli ultimi sono i più recenti
            use_missing_batch = True
            logger.info("🆕 Nuovi URL trovati (%d). Processiamo subito gli ultimi %d.", len(missing), len(batch))

    if not batch:
        end = min(cursor + MAX_BATCH, len(links))
        batch = links[cursor:end]
        if not batch and links:
            batch = links[:min(MAX_BATCH, len(links))]
            cursor = 0
        logger.info("Elaboro batch %d → %d (totale link %d)", cursor, cursor + len(batch), len(links))

    fresh_items = []
    for url in batch:
        existing = cache.get("items", {}).get(url)
        new_item, changed = _process_one(url, existing_item=existing, session=session)
        if new_item:
            fresh_items.append(new_item)
        time.sleep(REQUEST_DELAY)

    if not fresh_items:
        fresh_items = list(cache.get("items", {}).values())

    fresh_items.sort(key=lambda a: (a.get("published") or a.get("modified") or ""))

    logger.info("✅ Feed generato: %s articoli totali.", len(fresh_items))

    for it in fresh_items:
        url = it.get("url")
        if not url:
            continue
        old = cache.get("items", {}).get(url)
        if not old or old.get("_hash") != it.get("_hash"):
            cache.setdefault("items", {})[url] = it

    # Cursor: se abbiamo processato solo i nuovi, NON tocchiamo il cursor
    # (così non alteriamo la rotazione normale).
    if not use_missing_batch:
        cache["cursor"] = (cursor + len(batch)) % max(1, len(links))

    _save_cache(cache)

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
