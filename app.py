import os
import logging
from flask import Flask, jsonify, make_response
from datetime import datetime, timezone

from article_processor import generate_items, load_cache
from rss_generator import build_rss

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artbooms-rss")

app = Flask(__name__)

# Cache in memoria del feed (XML già pronto)
FEED_CACHE = {
    "xml": None,
    "headers": None,
    "last_build": None,
}

META = {
    "title": "ARTBOOMS - Archivio completo",
    "description": "Tutti gli articoli di Artbooms con aggiornamenti automatici",
    "language": "it-IT",
    "self_url": "https://artbooms-rss.onrender.com/rss",  # URL assoluto del feed
}


def _items_from_cache_sorted():
    """Legge gli articoli dalla cache su disco e li ordina per data (modified/published) desc."""
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
    """Ricostruisce l'XML del feed a partire dalla cache su disco (nessuna rete)."""
    items = _items_from_cache_sorted()
    xml_bytes, headers = build_rss(items, META)
    FEED_CACHE["xml"] = xml_bytes
    FEED_CACHE["headers"] = headers
    FEED_CACHE["last_build"] = datetime.utcnow().replace(tzinfo=timezone.utc)
    logger.info("Feed ricostruito da cache: %d articoli", len(items))


@app.get("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "last_build": FEED_CACHE["last_build"].isoformat() if FEED_CACHE["last_build"] else None
    })


@app.get("/")
def root():
    return jsonify({
        "service": "artbooms-rss",
        "description": "Feed generator per Artbooms",
        "endpoints": {
            "/rss": "feed RSS",
            "/refresh": "aggiorna la cache (da cron)",
            "/debug/cache": "stato cache su disco"
        }
    })


@app.get("/rss")
def rss():
    """
    Restituisce subito il feed costruito dalla cache su disco (mai scraping qui).
    Se è la prima volta (cache vuota), costruisce una versione iniziale leggendo il file cache.
    """
    if FEED_CACHE["xml"] is None:
        _rebuild_feed_from_disk_cache()

    resp = make_response(FEED_CACHE["xml"])
    # Header HTTP (ETag, Last-Modified, Cache-Control)
    for h in ("ETag", "Last-Modified", "Cache-Control", "Content-Type"):
        v = FEED_CACHE["headers"].get(h) if FEED_CACHE["headers"] else None
        if v:
            resp.headers[h] = v
    else:
        if not resp.headers.get("Content-Type"):
            resp.headers["Content-Type"] = "application/rss+xml; charset=utf-8"
    return resp


@app.get("/refresh")
def refresh():
    """
    Aggiorna la cache su disco processando un piccolo batch (nessun blocco su /rss).
    Chiamare questo endpoint via cron (es. ogni 15 min).
    """
    try:
        # processa un batch leggero (rispetta MAX_BATCH da env; default 1)
        generate_items(force=False)
        # ricostruisce l'XML dal file cache appena aggiornato
        _rebuild_feed_from_disk_cache()
        return jsonify({"status": "ok", "last_build": FEED_CACHE["last_build"].isoformat()})
    except Exception as e:
        logger.exception("Errore refresh: %s", e)
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.get("/debug/cache")
def debug_cache():
    cache = load_cache() or {}
    return jsonify({
        "items_count": len((cache.get("items") or {}).keys()),
        "cursor": cache.get("cursor", 0),
        "last_scan": cache.get("last_scan"),
        "feed_last_build": FEED_CACHE["last_build"].isoformat() if FEED_CACHE["last_build"] else None
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
