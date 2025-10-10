import os
import json
import time
import threading
import logging
import requests
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items
from rss_generator import build_rss

# ============================================================
# ⚙️ CONFIGURAZIONE GENERALE
# ============================================================
CACHE_PATH = "cache/articles_cache.json"
RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = "artbooms-rss/1.0 (+https://artbooms.com)"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ============================================================
# 🧠 BOOTSTRAP CACHE
# ============================================================
def bootstrap_cache():
    """Scarica la cache da GitHub o crea una vuota se non disponibile."""
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
            logging.info("Cache scaricata da GitHub (%s byte).", len(r.text))
        else:
            logging.warning("Cache remota vuota o non trovata — parto da zero.")
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"items": []}, f)
    except Exception as e:
        logging.error("Errore bootstrap cache: %s", e)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": []}, f)


# ============================================================
# 🧩 RIGENERA FEED RSS
# ============================================================
def rebuild_feed():
    """Rigenera il feed RSS a partire dalla cache locale (tollerante a tutto)."""
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
    meta = data.get("meta", {
        "title": "Artbooms RSS Feed",
        "link": "https://www.artbooms.com",
        "description": "Ultimi articoli da Artbooms",
        "language": "it-IT"
    })

    # Se la cache è vuota → crea un feed XML minimo
    if not items:
        logging.info("Cache vuota — creo feed vuoto temporaneo.")
        empty_feed = b"<?xml version='1.0' encoding='UTF-8'?><rss><channel><title>Artbooms RSS Feed</title></channel></rss>"
        with open("feed.xml", "wb") as f:
            f.write(empty_feed)
        return

    # Protezione extra: accetta build_rss() con o senza meta
    try:
        try:
            rss_xml = build_rss(items, meta)
        except TypeError:
            rss_xml = build_rss(items)
    except Exception as e:
        logging.error("Errore durante la generazione del feed RSS: %s", e)
        empty_feed = b"<?xml version='1.0' encoding='UTF-8'?><rss><channel><title>Errore feed</title></channel></rss>"
        with open("feed.xml", "wb") as f:
            f.write(empty_feed)
        return

    with open("feed.xml", "wb") as f:
        f.write(rss_xml)
    logging.info("Feed rigenerato da cache: %s articoli", len(items))


# ============================================================
# 🔁 THREAD DI AGGIORNAMENTO
# ============================================================
def background_populator():
    """Aggiorna periodicamente la cache e il feed."""
    while True:
        try:
            generate_items()  # batch di 3 articoli
            rebuild_feed()
        except Exception as e:
            logging.error("Errore nel popolatore: %s", e)
        time.sleep(60)  # ogni minuto


# ============================================================
# 🌐 ENDPOINTS FLASK
# ============================================================
@app.route("/rss")
def rss():
    """Restituisce il feed RSS (rigenera se mancante)."""
    if not os.path.exists("feed.xml") or os.path.getsize("feed.xml") < 100:
        logging.warning("Feed assente o vuoto, rigenero...")
        rebuild_feed()
    if not os.path.exists("feed.xml"):
        return Response("Feed non ancora disponibile, riprova tra poco.", status=503)
    try:
        with open("feed.xml", "rb") as f:
            data = f.read()
    except Exception as e:
        logging.error("Errore leggendo feed.xml: %s", e)
        return Response("Errore nel feed RSS.", status=500)
    return Response(data, mimetype="application/rss+xml")


@app.route("/debug/cache")
def debug_cache():
    """Mostra quanti articoli sono attualmente in cache."""
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
    """Permette a GitHub Actions di scaricare la cache."""
    if not os.path.exists(CACHE_PATH):
        return "{}"
    return send_file(CACHE_PATH, mimetype="application/json")


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "artbooms-rss"})


# ============================================================
# 🚀 AVVIO
# ============================================================
def start_background():
    t = threading.Thread(target=background_populator, daemon=True)
    t.start()


if __name__ == "__main__":
    bootstrap_cache()
    rebuild_feed()
    start_background()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
