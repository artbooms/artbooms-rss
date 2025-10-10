import os
import json
import threading
import time
import logging
import requests
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items
from rss_generator import build_rss

# ============================================================
# ⚙️ CONFIGURAZIONE
# ============================================================
CACHE_PATH = "cache/articles_cache.json"
RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ============================================================
# 🧠 CACHE PERSISTENTE
# ============================================================
def bootstrap_cache():
    """Scarica la cache da GitHub (se esiste) oppure crea una nuova cache vuota."""
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
            if not os.path.exists(CACHE_PATH) or os.path.getsize(CACHE_PATH) < 10:
                logging.warning("Cache remota vuota — nessuna cache locale trovata, ne creo una nuova.")
                with open(CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump({"items": []}, f)
            else:
                logging.warning("Cache remota vuota — mantengo la cache locale esistente.")
    except Exception as e:
        logging.error("Errore nel bootstrap della cache: %s", e)
        if not os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"items": []}, f)


# ============================================================
# 📰 FEED RSS
# ============================================================
def rebuild_feed():
    """Rigenera il feed RSS a partire dalla cache locale."""
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error("Errore caricando la cache: %s", e)
        data = {"items": []}

    items = data.get("items", [])
    meta = data.get("meta", {
        "title": "Artbooms RSS Feed",
        "link": "https://www.artbooms.com",
        "description": "Ultimi articoli da Artbooms",
        "language": "it-IT"
    })

    if not items:
        logging.warning("Cache vuota — genero feed vuoto temporaneo.")
        empty_feed = b"<?xml version='1.0' encoding='UTF-8'?><rss><channel><title>Artbooms RSS Feed</title></channel></rss>"
        with open("feed.xml", "wb") as f:
            f.write(empty_feed)
        return

    try:
        logging.info("Chiamo build_rss() con %s articoli.", len(items))
        try:
            result = build_rss(items, meta)
        except TypeError:
            result = build_rss(items)

        logging.info("build_rss() ha restituito tipo: %s", type(result))

        # 🔧 Estrai il vero XML da qualunque formato
        if isinstance(result, tuple):
            rss_xml = result[0]
        elif isinstance(result, (bytes, str)):
            rss_xml = result
        else:
            rss_xml = str(result)

        # 🔧 Converte in bytes se serve
        if isinstance(rss_xml, str):
            rss_xml = rss_xml.encode("utf-8")

        with open("feed.xml", "wb") as f:
            f.write(rss_xml)

        logging.info("Feed rigenerato con %s articoli.", len(items))
    except Exception as e:
        logging.error("Errore durante la generazione del feed: %s", e)


# ============================================================
# 🔁 THREAD DI AGGIORNAMENTO
# ============================================================
def background_populator():
    """Aggiorna periodicamente la cache e il feed RSS."""
    while True:
        try:
            generate_items()  # batch di 3 articoli per ciclo
            rebuild_feed()
        except Exception as e:
            logging.error("Errore nel popolatore: %s", e)
        time.sleep(60)


# ============================================================
# 🌐 ENDPOINTS FLASK
# ============================================================
@app.route("/rss")
def rss():
    if not os.path.exists("feed.xml") or os.path.getsize("feed.xml") < 100:
        rebuild_feed()
    with open("feed.xml", "rb") as f:
        data = f.read()
    return Response(data, mimetype="application/rss+xml")


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


@app.route("/cache/download")
def cache_download():
    if not os.path.exists(CACHE_PATH):
        return "{}"
    return send_file(CACHE_PATH, mimetype="application/json")


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "artbooms-rss"})


# ============================================================
# 🚀 AVVIO COMPATIBILE CON RENDER
# ============================================================
bootstrap_cache()
rebuild_feed()

# Avvia il thread di popolamento anche con Gunicorn
if not any(t.name == "BackgroundPopulator" for t in threading.enumerate()):
    t = threading.Thread(target=background_populator, daemon=True, name="BackgroundPopulator")
    t.start()
    logging.info("Thread di popolamento avviato.")

# Se eseguito localmente, avvia Flask normalmente
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
