
import os
import logging
import threading
import time
from flask import Flask, jsonify, make_response, Response
from datetime import datetime, timezone
import requests

from article_processor import generate_items, load_cache
from rss_generator import build_rss

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artbooms-rss")

app = Flask(__name__)

CACHE_PATH = "cache/articles_cache.json"
GITHUB_CACHE_RAW_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"

FEED_CACHE = {"xml": None, "headers": None, "last_build": None}
META = {
    "title": "ARTBOOMS - Archivio completo",
    "description": "Tutti gli articoli di Artbooms con aggiornamenti automatici",
    "language": "it-IT",
    "self_url": "https://artbooms-rss.onrender.com/rss",
}


def _items_from_cache_sorted():
    cache = load_cache() or {}
    items = list((cache.get("items") or {}).values())

    from dateutil import parser as _p
    def _to_dt(s):
        try:
            if not s:
                return datetime(1970, 1, 1, tzinfo=timezone.utc)
            dt = _p.parse(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

    items.sort(key=lambda x: max(_to_dt(x.get("modified")), _to_dt(x.get("published"))), reverse=True)
    return items


def _rebuild_feed_from_disk_cache():
    items = _items_from_cache_sorted()
    xml_bytes, headers = build_rss(items, META)
    FEED_CACHE["xml"] = xml_bytes
    FEED_CACHE["headers"] = headers
    FEED_CACHE["last_build"] = datetime.utcnow().replace(tzinfo=timezone.utc)
    logger.info(f"[Feed] Ricostruito da cache: {len(items)} articoli")


def bootstrap_cache():
    """Scarica la cache persistente da GitHub se non esiste in locale"""
    if os.path.exists(CACHE_PATH):
        logger.info(f"[Bootstrap] Cache locale trovata: {CACHE_PATH}")
        return
    try:
        logger.info(f"[Bootstrap] Scarico cache da GitHub...")
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        r = requests.get(GITHUB_CACHE_RAW_URL, timeout=15)
        if r.status_code == 200 and r.content:
            with open(CACHE_PATH, "wb") as f:
                f.write(r.content)
            logger.info("[Bootstrap] ✅ Cache scaricata da GitHub con successo.")
        else:
            logger.warning(f"[Bootstrap] ⚠️ Nessuna cache disponibile su GitHub (HTTP {r.status_code})")
    except Exception as e:
        logger.exception(f"[Bootstrap] ❌ Errore nel download cache: {e}")


def background_populator():
    """Thread che aggiorna la cache e ricostruisce il feed ogni minuto"""
    logger.info("[Worker] 🔁 Thread di aggiornamento avviato.")
    while True:
        try:
            generate_items(force=False)
            _rebuild_feed_from_disk_cache()
        except Exception as e:
            logger.exception(f"[Worker] ❌ Errore durante aggiornamento: {e}")
        time.sleep(60)


@app.get("/rss")
def rss():
    if FEED_CACHE["xml"] is None:
        _rebuild_feed_from_disk_cache()
    resp = make_response(FEED_CACHE["xml"])
    for h in ("ETag", "Last-Modified", "Cache-Control", "Content-Type"):
        if FEED_CACHE["headers"] and FEED_CACHE["headers"].get(h):
            resp.headers[h] = FEED_CACHE["headers"][h]
    resp.headers.setdefault("Content-Type", "application/rss+xml; charset=utf-8")
    return resp


@app.get("/debug/cache")
def debug_cache():
    cache = load_cache() or {}
    return jsonify({
        "items_count": len((cache.get("items") or {}).keys()),
        "cursor": cache.get("cursor", 0),
        "last_scan": cache.get("last_scan"),
        "feed_last_build": FEED_CACHE["last_build"].isoformat() if FEED_CACHE["last_build"] else None,
    })


@app.get("/cache/download")
def cache_download():
    if not os.path.exists(CACHE_PATH):
        return jsonify({"error": "cache not found"}), 404
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/json")


@app.get("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "last_build": FEED_CACHE["last_build"].isoformat() if FEED_CACHE["last_build"] else None
    })


# === Avvio immediato per Render ===
logger.info("[Main] 🚀 Avvio Artbooms RSS")
bootstrap_cache()
_rebuild_feed_from_disk_cache()
t = threading.Thread(target=background_populator, daemon=True)
t.start()
logger.info("[Main] ✅ Bootstrap completato e thread background attivo.")

port = int(os.environ.get("PORT", "5000"))
app.run(host="0.0.0.0", port=port)
