import os, json, logging
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items, load_cache, _date_safe
from rss_generator import build_rss

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("app")

CACHE_PATH = "cache/articles_cache.json"

def _ensure_cache_file():
    os.makedirs("cache", exist_ok=True)
    if not os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": []}, f)

@app.route("/rss")
@app.route("/rss.xml")
def rss():
    _ensure_cache_file()
    # carica/alimenta batch (MAX_BATCH=3) prima di servire il feed
    try:
        generate_items()
    except Exception as e:
        logger.error("Errore generate_items: %s", e)

    data = load_cache()
    items = data.get("items", [])

    # feed: più nuovi in cima
    items.sort(key=lambda x: _date_safe(x.get("published") or x.get("modified")), reverse=True)

    xml = build_rss(items, {
        "title": "Artbooms RSS Feed",
        "link": "https://www.artbooms.com",
        "description": "Ultimi articoli da Artbooms",
        "language": "it-IT",
        "self": os.environ.get("FEED_SELF_URL", "https://artbooms-rss.onrender.com/rss")
    })
    return Response(xml, mimetype="application/rss+xml; charset=utf-8")

@app.route("/debug/cache")
def debug_cache():
    _ensure_cache_file()
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = sum(1 for it in data.get("items", []) if isinstance(it, dict) and "/blog/" in (it.get("url") or ""))
    except Exception:
        count = 0
    return jsonify({"articles_in_cache": count})

@app.route("/cache/download")
def cache_download():
    _ensure_cache_file()
    return send_file(CACHE_PATH, mimetype="application/json")

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
