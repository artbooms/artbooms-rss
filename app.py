import os
import logging
import threading
import time
from flask import Flask, jsonify, make_response
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

    items.sort(
        key=lambda x: max(
            _to_dt(x.get("modified")), _to_dt(x.get("published"))
        ),
        reverse=True,
    )
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
    resp.headers.setdefault(
        "Content-Type", "application/rss+xml; charset=utf-8"
    )
    return resp


@app.get("/refresh")
def refresh():
    try:
        generate_items(force=False)
        _rebuild_feed_from_disk_cache()
        return jsonify(
            {
                "status": "ok",
                "last_build": FEED_CACHE["last_build"].isoformat(),
            }
        )
    except Exception as e:
        logger.exception("Errore refresh")
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.get("/debug/cache")
def debug_cache():
    cache = load_cache() or {}
    return jsonify(
        {
            "items_count": len((cache.get("items") or {}).keys()),
            "cursor": cache.get("cursor", 0),
            "last_scan": cache.get("last_scan"),
            "feed_last_build": FEED_CACHE["last_build"].isoformat()
            if FEED_CACHE["last_build"]
            else None,
        }
    )


@app.get("/cache/download")
def cache_download():
    """Restituisce la cache completa come file JSON per GitHub Actions"""
    cache = load_cache() or {}
    return jsonify(cache)


def background_populator():
    """Thread che aggiorna la cache ogni minuto e rigenera il feed se cambia"""
    previous_count = 0
    while True:
        try:
            items, _ = generate_items(force=False)
            current_count = len(items)
            if current_count != previous_count:
                _rebuild_feed_from_disk_cache()
                logger.info(
                    "Feed ricostruito automaticamente: %d articoli", current_count
                )
                previous_count = current_count
            else:
                logger.info(
                    "Nessun nuovo articolo, feed invariato (%d articoli)", current_count
                )
        except Exception as e:
            logger.exception("Errore background_populator: %s", e)
        time.sleep(60)
