import os
import logging
from flask import Flask, Response, request, jsonify, make_response
from datetime import datetime, timezone
from threading import Thread
import time

from article_processor import generate_items, load_cache
from rss_generator import build_rss

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artbooms-rss")

app = Flask(__name__)

# Cache globale del feed
FEED_CACHE = {
    "xml": None,
    "headers": None,
    "last_update": None
}

# -------------------------------------------------------------
# Funzione che genera o aggiorna il feed
# -------------------------------------------------------------
def update_feed(force=False):
    try:
        items, meta = generate_items(force=force)
        xml_bytes, headers = build_rss(items, meta)
        FEED_CACHE["xml"] = xml_bytes
        FEED_CACHE["headers"] = headers
        FEED_CACHE["last_update"] = datetime.utcnow().replace(tzinfo=timezone.utc)
        logger.info(f"Feed aggiornato: {len(items)} articoli totali.")
        return True
    except Exception as e:
        logger.exception("Errore durante l'aggiornamento del feed: %s", e)
        return False


# -------------------------------------------------------------
# Endpoint: health check
# -------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "last_update": FEED_CACHE["last_update"].isoformat() if FEED_CACHE["last_update"] else None
    })


# -------------------------------------------------------------
# Endpoint principale /rss
# -------------------------------------------------------------
@app.get("/rss")
def rss():
    """
    Restituisce il feed RSS completo. Se non esiste in cache, lo genera.
    """
    if FEED_CACHE["xml"] is None:
        logger.info("Cache vuota: generazione iniziale del feed...")
        update_feed(force=True)

    resp = make_response(FEED_CACHE["xml"])
    resp.headers["Content-Type"] = "application/rss+xml; charset=utf-8"
    for h in ("ETag", "Last-Modified", "Cache-Control"):
        if FEED_CACHE["headers"] and FEED_CACHE["headers"].get(h):
            resp.headers[h] = FEED_CACHE["headers"][h]
    return resp


# -------------------------------------------------------------
# Endpoint /refresh - aggiornamento manuale o via cron
# -------------------------------------------------------------
@app.get("/refresh")
def refresh():
    """
    Forza l'aggiornamento del feed (per cron job o aggiornamento manuale).
    """
    updated = update_feed(force=True)
    return jsonify({
        "status": "ok" if updated else "error",
        "last_update": FEED_CACHE["last_update"].isoformat() if FEED_CACHE["last_update"] else None
    })


# -------------------------------------------------------------
# Endpoint debug/cache
# -------------------------------------------------------------
@app.get("/debug/cache")
def debug_cache():
    cache = load_cache()
    return jsonify({
        "cache_exists": bool(cache),
        "items_count": len(cache.get("items", {})),
        "cursor": cache.get("cursor", 0),
        "last_scan": cache.get("last_scan"),
        "feed_last_update": FEED_CACHE["last_update"].isoformat() if FEED_CACHE["last_update"] else None
    })


# -------------------------------------------------------------
# Thread automatico che aggiorna il feed ogni ora
# -------------------------------------------------------------
def background_updater(interval_minutes=60):
    while True:
        time.sleep(interval_minutes * 60)
        logger.info("Aggiornamento automatico pianificato...")
        update_feed(force=True)


def start_background_updater():
    t = Thread(target=background_updater, daemon=True)
    t.start()


# -------------------------------------------------------------
# Avvio app
# -------------------------------------------------------------
if __name__ == "__main__":
    start_background_updater()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
