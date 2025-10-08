import os
import logging
import threading
import time
from flask import Flask, jsonify, make_response, Response
from datetime import datetime, timezone

from article_processor import generate_items, load_cache
from rss_generator import build_rss

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artbooms-rss")

app = Flask(__name__)

FEED_CACHE = {"xml": None, "headers": None, "last_build": None}
META = {
    "title": "ARTBOOMS - Archivio completo",
    "description": "Tutti gli articoli di Artbooms con aggiornamenti automatici",
    "language": "it-IT",
    "self_url": "https://artbooms-rss.onrender.com/rss",
}

CACHE_PATH = "articles_cache.json"
# ✅ URL GitHub raw da cui leggere la cache in caso di riavvio
GITHUB_CACHE_RAW_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/articles_cache.json"


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
    logger.info("Feed ricostruito da cache: %d articoli", len(items))


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


@app.get("/refresh")
def refresh():
    try:
        generate_items(force=False)
        _rebuild_feed_from_disk_cache()
        return jsonify({"status": "ok", "last_build": FEED_CACHE["last_build"].isoformat()})
    except Exception as e:
        logger.exception("Errore refresh")
        return jsonify({"status": "error", "detail": str(e)}), 500


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
    """✅ Serve al workflow GitHub per scaricare la cache"""
    if not os.path.exists(CACHE_PATH):
        return jsonify({"error": "cache not found"}), 404
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/json")


def background_populator():
    """Thread che aggiorna la cache ogni minuto senza bloccare Flask"""
    while True:
        try:
            generate_items(force=False)
            _rebuild_feed_from_disk_cache()
            logger.info("Cache aggiornata automaticamente")
        except Exception as e:
            logger.exception("Errore background_populator: %s", e)
        time.sleep(60)
