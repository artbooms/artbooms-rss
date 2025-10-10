import os
import json
import time
import threading
import logging
import requests
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items
from rss_generator import build_rss

# 🔹 CONFIGURAZIONE CACHE PERSISTENTE
CACHE_PATH = "cache/articles_cache.json"
RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = "artbooms-rss/1.0 (+https://artbooms.com)"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 🧠 Scarica cache da GitHub o crea una nuova cache vuota
def bootstrap_cache():
    os.makedirs("cache", exist_ok=True)
    if os.path.exists(CACHE_PATH) and os.path.getsize(CACHE_PATH) > 10:
        logging.info("Cache locale trovata, salto bootstrap.")
        return

    try:
        logging.info(f"Tento download cache da: {RAW_CACHE_URL}")
        r = requests.get(RAW_CACHE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.ok and r.text.strip() not in ("", "{}", "null"):
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                f.write(r.text)
            logging.info("Cache scaricata da GitHub (%s bytes).", len(r.text))
        else:
            logging.warning("Cache remota vuota o non trovata — parto da zero.")
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"items": []}, f)
    except Exception as e:
        logging.error("Errore bootstrap cache: %s", e)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": []}, f)

# 🧩 Rigenera il feed RSS da cache locale (tollerante)
def rebuild_feed():
    if not os.path.exists(CACHE_PATH):
        logging.warning("Nessuna cache trovata, creo nuova cache vuota.")
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": []}, f)
        return

    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logging.error("Cache JSON danneggiata — riparto da zero.")
        data = {"items": []}

    items = data.get("items", [])
    if not items:
        logging.info("Cache vuota — feed iniziale verrà popolato nei prossimi cicli.")
        return

    rss_xml = build_rss(items)
    with open("feed.xml", "wb") as f:
        f.write(rss_xml)
    logging.info("Feed rigenerato da cache: %s articoli", len(items))

# 🔁 Thread che aggiorna la cache ogni minuto
def background_populator():
    while True:
        try:
            generate_items()  # batch di 3 articoli alla volta
            rebuild_feed()
        except Exception as e:
            logging.error("Errore nel popolatore: %s", e)
        time.sleep(60)  # aggiorna ogni 60 secondi

# 🔗 Endpoint principale per il feed RSS
@app.route("/rss")
def rss():
    """Serve il feed RSS; se non esiste o è vuoto, lo rigenera."""
    if not os.path.exists("feed.xml") or os.path.getsize("feed.xml") < 100:
        logging.warning("Feed assente o vuoto, rigenero...")
        rebuild_feed()
    if not os.path.exists("feed.xml"):
        return Response("Feed non ancora disponibile. Riprova tra 1 minuto.", status=503)
    try:
        with open("feed.xml", "rb") as f:
            data = f.read()
    except Exception as e:
        logging.error("Errore leggendo feed.xml: %s", e)
        return Response("Errore nel feed RSS.", status=500)
    return Response(data, mimetype="application/rss+xml")

# 🔍 Endpoint debug: mostra quanti articoli in cache
@app.route("/debug/cache")
def debug_cache():
    if not os.path.exists(CACHE_PATH):
        return jsonify({"articles_in_cache": 0})
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = len(data.get("items", []))
    except Exception:
        count = 0
    return jsonify({"articles_in_cache": count})

# 📦 Endpoint per GitHub Actions
@app.route("/cache/download")
def cache_download():
    if not os.path.exists(CACHE_PATH):
        return "{}"
    return send_file(CACHE_PATH, mimetype="application/json")

# ❤️ Health check
@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "artbooms-rss"})

# 🧵 Avvio thread e bootstrap
def start_background():
    t = threading.Thread(target=background_populator, daemon=True)
    t.start()

# 🚀 MAIN
if __name__ == "__main__":
    bootstrap_cache()
    rebuild_feed()
    start_background()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
