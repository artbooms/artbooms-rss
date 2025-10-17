import os
import logging
from flask import Flask, Response, request, jsonify, make_response
from datetime import datetime, timezone

from article_processor import generate_items, load_cache
from rss_generator import build_rss

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artbooms-rss")

app = Flask(__name__)

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "time": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()})

@app.get("/")
def root():
    return jsonify({
        "service": "artbooms-rss",
        "description": "Feed generator per Artbooms",
        "endpoints": {
            "/healthz": "health check",
            "/rss": "il feed RSS generato"
        }
    })

@app.get("/rss")
def rss():
    """
    Genera e restituisce il feed RSS. Il feed viene prodotto a partire
    dalla cache gestita da article_processor.generate_items()
    """
    try:
        # generate_items ritorna (items_list, meta)
        items, meta = generate_items(force=False)
    except Exception as e:
        logger.exception("Errore durante generate_items")
        return jsonify({"error": "feed generation failed", "detail": str(e)}), 503

    try:
        xml_bytes, headers = build_rss(items, meta)
    except Exception as e:
        logger.exception("Errore durante build_rss")
        return jsonify({"error": "rss build failed", "detail": str(e)}), 500

    resp = make_response(xml_bytes)
    resp.headers["Content-Type"] = "application/rss+xml; charset=utf-8"
    for hname in ("ETag", "Last-Modified", "Cache-Control"):
        if headers.get(hname):
            resp.headers[hname] = headers[hname]
    return resp

@app.get("/debug/cache")
def debug_cache():
    """Per controllo: restituisce lo stato della cache (solo in debug)."""
    cache = load_cache()
    return jsonify({
        "cache_exists": bool(cache),
        "items_count": len(cache.get("items", {})),
        "cursor": cache.get("cursor", 0),
        "last_scan": cache.get("last_scan")
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
