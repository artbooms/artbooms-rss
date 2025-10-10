import os
import json
import time
import threading
import logging
import requests
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items  # già esistente nel tuo pacchetto
from rss_generator import build_rss  # già esistente nel tuo pacchetto

# 🔹 CONFIGURAZIONE PERSISTENZA CACHE
CACHE_PATH = "cache/articles_cache.json"
RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = "artbooms-rss/1.0 (+https://artbooms.com)"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def bootstrap_cache():
    """Scarica la cache persistente da GitHub se non esiste localmente."""
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
            logging.info("Cache scaricata (%s byte).", len(r.text))
        else:
            logging.warning("Cache remota vuota o non trovata.")
    except Exception as e:
        logging.error("Errore bootstrap cache: %s", e)

def rebuild_feed():
    """Rigenera il feed RSS a partire dalla cache locale."""
    if not os.path.exists(CACHE_PATH):
        logging.warning("Nessuna cache trovata per rigenerare il feed.")
        return
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    rss_xml = build_rss(items)
    with open("feed.xml", "wb") as f:
        f.write(rss_xml)
    logging.info("Feed rigenerato da cache: %s articoli", len(items))

def background_populator():
    """Thread di popolamento periodico (riprende da dove era rimasto)."""
    while True:
        try:
            generate_items()  # la tua funzione esistente
            rebuild_feed()
        except Exception as e:
            logging.error("Errore nel popolatore: %s", e)
        time.sleep(60)  # ogni minuto aggiorna batch di 3 articoli

@app.route("/rss")
def rss():
    if not os.path.exists("feed.xml"):
        rebuild_feed()
    with open("feed.xml", "rb") as f:
        data = f.read()
    return Response(data, mimetype="application/rss+xml")

@app.route("/debug/cache")
def debug_cache():
    if not os.path.exists(CACHE_PATH):
        return jsonify({"status": "no cache"})
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify({"articles_in_cache": len(data.get("items", []))})

# 🔹 ENDPOINT PER GITHUB ACTIONS (cache persistente)
@app.route("/cache/download")
def cache_download():
    if not os.path.exists(CACHE_PATH):
        return "{}"
    return send_file(CACHE_PATH, mimetype="application/json")

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "artbooms-rss"})

def start_background():
    t = threading.Thread(target=background_populator, daemon=True)
    t.start()

# 🔹 BOOTSTRAP + AVVIO
if __name__ == "__main__":
    bootstrap_cache()
    rebuild_feed()
    start_background()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
