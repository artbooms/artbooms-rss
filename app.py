import os
import json
import threading
import time
import logging
import requests
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items
from rss_generator import build_rss

CACHE_PATH = "cache/articles_cache.json"
RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# 🧩 Controllo frequenza rigenerazione (in secondi)
POPULATE_INTERVAL = 120  # ogni 2 minuti
FORCE_REBUILD_AFTER = 900  # forza rigenerazione feed ogni 15 minuti

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ============================================================
# 🧠 CACHE PERSISTENTE
# ============================================================
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

# ============================================================
# 📰 FEED RSS
# ============================================================
def rebuild_feed():
    """Rigenera il feed RSS ordinando dal più nuovo al più vecchio."""
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error("Errore caricando la cache: %s", e)
        data = {"items": []}

    # Usa solo articoli reali del blog
    items = [
        i for i in data.get("items", [])
        if isinstance(i, dict) and "/blog/" in (i.get("url") or "")
    ]

    # 🌐 Normalizza e deduplica forzando HTTPS
    seen = set()
    clean_items = []
    for i in items:
        url = (i.get("url") or "").replace("http://", "https://")
        if url not in seen:
            seen.add(url)
            i["url"] = url
            clean_items.append(i)
    items = clean_items

    # 📅 Ordina dal più recente al più vecchio
    def get_dt(article):
        return article.get("modified") or article.get("published") or ""
    items_sorted = sorted(items, key=get_dt, reverse=True)

    meta = data.get("meta", {
        "title": "Artbooms RSS Feed",
        "link": "https://www.artbooms.com",
        "description": "Ultimi articoli da Artbooms",
        "language": "it-IT"
    })

    if not items_sorted:
        logging.warning("Cache vuota — genero feed vuoto temporaneo.")
        empty_feed = b"<?xml version='1.0' encoding='UTF-8'?><rss><channel><title>Artbooms RSS Feed</title></channel></rss>"
        with open("feed.xml", "wb") as f:
            f.write(empty_feed)
        return

    try:
        rss_xml = build_rss(items_sorted, meta)
        if isinstance(rss_xml, tuple):
            rss_xml = rss_xml[0]
        if isinstance(rss_xml, str):
            rss_xml = rss_xml.encode("utf-8")

        # ✂️ Rimuove eventuale commento <!--last_build-->
        rss_xml = rss_xml.replace(b"<!--last_build:", b"<!--removed:")

        with open("feed.xml", "wb") as f:
            f.write(rss_xml)

        logging.info("Feed rigenerato con %s articoli (dal più nuovo al più vecchio).", len(items_sorted))
    except Exception as e:
        logging.error("Errore durante la generazione del feed: %s", e)

# ============================================================
# 🔁 THREAD DI AGGIORNAMENTO (patch anti-sleep)
# ============================================================
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

# ============================================================
# 🌐 ENDPOINTS FLASK
# ============================================================
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

# ============================================================
# 🚀 AVVIO
# ============================================================
bootstrap_cache()
rebuild_feed()

if not any(t.name == "BackgroundPopulator" for t in threading.enumerate()):
    t = threading.Thread(target=background_populator, daemon=True, name="BackgroundPopulator")
    t.start()
    logging.info("Thread di popolamento avviato nel processo PID %s", os.getpid())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
