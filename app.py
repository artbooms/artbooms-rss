import os
import json
import threading
import time
import logging
import requests
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items
from rss_generator import build_rss

# ---------------------------------------------------------
# CONFIGURAZIONE BASE
# ---------------------------------------------------------
CACHE_PATH = "cache/articles_cache.json"
RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
POPULATE_INTERVAL = 120   # Ogni 2 minuti
FORCE_REBUILD_AFTER = 900 # Forza rigenerazione ogni 15 minuti

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------
# BOOTSTRAP CACHE
# ---------------------------------------------------------
def bootstrap_cache():
    """Scarica o inizializza la cache locale."""
    os.makedirs("cache", exist_ok=True)
    if os.path.exists(CACHE_PATH) and os.path.getsize(CACHE_PATH) > 10:
        logging.info("Cache locale trovata, salto bootstrap.")
        return

    try:
        logging.info("Scarico cache persistente da GitHub...")
        r = requests.get(RAW_CACHE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.ok and r.text.strip() not in ("", "{}", "null"):
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                f.write(r.text)
            logging.info("Cache scaricata da GitHub (%s bytes).", len(r.text))
        else:
            logging.warning("Cache remota vuota — ne creo una nuova.")
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"items": []}, f)
    except Exception as e:
        logging.error("Errore nel bootstrap della cache: %s", e)
        if not os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"items": []}, f)

# ---------------------------------------------------------
# RIGENERAZIONE FEED
# ---------------------------------------------------------
def rebuild_feed():
    """Rigenera il feed RSS ordinando dal più nuovo al più vecchio."""
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error("Errore caricando la cache: %s", e)
        data = {"items": []}

    # Filtra articoli validi
    items = [
        i for i in data.get("items", [])
        if isinstance(i, dict) and "/blog/" in (i.get("url") or "")
    ]

    # Ordina dal più recente al più vecchio
    items.sort(key=lambda x: x.get("modified") or x.get("published") or "", reverse=True)

    meta = {
        "title": "Artbooms RSS Feed",
        "link": "https://www.artbooms.com",
        "description": "Ultimi articoli da Artbooms",
        "language": "it-IT",
    }

    rss_xml = build_rss(items, meta)

    # 🧩 FIX: build_rss restituisce una tupla (xml_bytes, headers)
    if isinstance(rss_xml, tuple):
        rss_xml = rss_xml[0]

    if isinstance(rss_xml, str):
        rss_xml = rss_xml.encode("utf-8")

    with open("feed.xml", "wb") as f:
        f.write(rss_xml)

    logging.info("Feed rigenerato con %s articoli (dal più nuovo al più vecchio).", len(items))

# ---------------------------------------------------------
# THREAD DI POPOLAMENTO
# ---------------------------------------------------------
def background_populator():
    """Aggiorna periodicamente la cache e il feed RSS."""
    last_rebuild = 0
    while True:
        try:
            generate_items()
            now = time.time()
            if now - last_rebuild > FORCE_REBUILD_AFTER:
                rebuild_feed()
                last_rebuild = now
        except Exception as e:
            logging.error("Errore nel popolatore: %s", e)
        time.sleep(POPULATE_INTERVAL)

# ---------------------------------------------------------
# ENDPOINTS FLASK
# ---------------------------------------------------------
@app.route("/rss")
def rss():
    if not os.path.exists("feed.xml") or os.path.getsize("feed.xml") < 100:
        rebuild_feed()
    with open("feed.xml", "rb") as f:
        return Response(f.read(), mimetype="application/rss+xml")

@app.route("/debug/cache")
def debug_cache():
    if not os.path.exists(CACHE_PATH):
        return jsonify({"articles_in_cache": 0})
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = len([i for i in data.get("items", []) if "/blog/" in (i.get("url") or "")])
    except Exception:
        count = 0
    return jsonify({"articles_in_cache": count})

@app.route("/cache/download")
def cache_download():
    return send_file(CACHE_PATH, mimetype="application/json")

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "artbooms-rss"})

# ---------------------------------------------------------
# AVVIO
# ---------------------------------------------------------
bootstrap_cache()
rebuild_feed()

if not any(t.name == "BackgroundPopulator" for t in threading.enumerate()):
    t = threading.Thread(target=background_populator, daemon=True, name="BackgroundPopulator")
    t.start()
    logging.info("Thread di popolamento avviato nel processo PID %s", os.getpid())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
