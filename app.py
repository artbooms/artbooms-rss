import os
import json
import threading
import time
import logging
import requests
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from flask import Flask, Response, jsonify, send_file
from article_processor import generate_items
from rss_generator import build_rss
from news_sitemap import news_sitemap_view  # 👈 AGGIUNTO

CACHE_PATH = "cache/articles_cache.json"
RAW_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

POPULATE_INTERVAL = 120
FORCE_REBUILD_AFTER = 900
MAX_BATCH = 3

FEED_SELF_URL = "https://rss.artbooms.com/rss"

PING_URLS = [
    f"https://www.google.com/ping?sitemap={FEED_SELF_URL}",
    f"https://www.bing.com/ping?sitemap={FEED_SELF_URL}",
]

PING_MIN_INTERVAL = 600  # 10 minuti (in secondi)
_last_ping_time = 0

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ============================================================
# Cache persistente
# ============================================================
def _cache_entries(data):
    if not isinstance(data, dict):
        raise ValueError("La cache non è un oggetto JSON")
    raw = data.get("items")
    items = list(raw.values()) if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ValueError("La cache non contiene articoli")
    entries = {}
    for item in items:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("url", "title", "published")
        ):
            raise ValueError("Articolo incompleto nella cache")
        datetime.fromisoformat(item["published"])
        entries[item["url"]] = item
    return entries


def _atomic_write(path, content):
    folder = os.path.dirname(os.path.abspath(path))
    fd, temporary = tempfile.mkstemp(prefix=".artbooms-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _validate_rss(content):
    root = ET.fromstring(content)
    if root.tag != "rss" or not root.findall("./channel/item"):
        raise ValueError("RSS non valido o privo di articoli")


def bootstrap_cache():
    os.makedirs("cache", exist_ok=True)
    local_entries = {}
    try:
        with open(CACHE_PATH, "rb") as f:
            local_entries = _cache_entries(json.load(f))
    except Exception as exc:
        logging.warning("Cache locale non disponibile o non valida: %s", exc)

    try:
        r = requests.get(RAW_CACHE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        remote_entries = _cache_entries(json.loads(r.content))
        missing = set(local_entries) - set(remote_entries)
        if missing:
            raise ValueError("La cache remota perderebbe %d URL" % len(missing))
        for url, old in local_entries.items():
            before = old.get("_fetched_at")
            after = remote_entries[url].get("_fetched_at")
            if before and (not after or datetime.fromisoformat(after) < datetime.fromisoformat(before)):
                raise ValueError("La cache remota è meno recente della copia locale")
        _atomic_write(CACHE_PATH, r.content)
        logging.info("Cache di avvio verificata: %d articoli", len(remote_entries))
    except Exception as exc:
        if local_entries:
            logging.warning("Mantengo la cache locale valida (%d articoli): %s", len(local_entries), exc)
            return
        raise RuntimeError("Avvio interrotto: nessuna cache valida disponibile") from exc

# ============================================================
# Feed RSS e ping automatico
# ============================================================
def ping_search_engines():
    """Invia ping a Google e Bing (massimo 1 ogni 10 minuti)."""
    global _last_ping_time
    now = time.time()
    if now - _last_ping_time < PING_MIN_INTERVAL:
        logging.info("⏳ Ping saltato: ultimo inviato meno di 10 minuti fa.")
        return
    _last_ping_time = now

    for url in PING_URLS:
        try:
            r = requests.get(url, timeout=10)
            if r.ok:
                logging.info("🔔 Ping inviato con successo: %s", url)
            else:
                logging.warning("⚠️ Ping fallito (%s): status %s", url, r.status_code)
        except Exception as e:
            logging.warning("⚠️ Errore durante il ping %s: %s", url, e)

def rebuild_feed():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error("Errore caricando la cache: %s", e)
        data = {"items": {}}

    raw_items = data.get("items", [])
    if isinstance(raw_items, dict):
        items = list(raw_items.values())
    elif isinstance(raw_items, list):
        items = raw_items
    else:
        items = []

    items = [i for i in items if isinstance(i, dict) and "/blog/" in (i.get("url") or "")]
    if not items:
        logging.warning("Cache vuota: feed vuoto.")
        return

    # --- DEBUG iniziale ---
    logging.info("🧩 DEBUG FEED: %d articoli totali prima dell'ordinamento", len(items))
    for idx, it in enumerate(items[:5]):
        logging.info("🧩 [%d] titolo='%s' | img=%s | pub=%s | mod=%s",
                     idx, it.get("title"), it.get("image"), it.get("published"), it.get("modified"))

    # 🩹 Ordina principalmente per 'published', usa 'modified' solo se manca
    def sort_key(a):
        return a.get("published") or a.get("modified") or ""
    items_sorted = sorted(items, key=sort_key, reverse=True)

    newest = items_sorted[0].get("title") if items_sorted else "N/D"
    logging.info("🧩 Costruzione RSS: ricevuti %d articoli. Più recente: %s", len(items_sorted), newest)

    meta = {
        "title": "Artbooms RSS Feed",
        "link": "https://www.artbooms.com",
        "description": "Ultimi articoli da Artbooms",
        "language": "it-IT",
        "self": FEED_SELF_URL
    }

    try:
        rss_xml = build_rss(items_sorted, meta)
        if isinstance(rss_xml, tuple):
            rss_xml = rss_xml[0]
        if isinstance(rss_xml, str):
            rss_xml = rss_xml.encode("utf-8")
        _validate_rss(rss_xml)
        _atomic_write("feed.xml", rss_xml)
        logging.info("✅ Feed ricostruito da cache: %s articoli", len(items_sorted))

        # 🔔 PING AUTOMATICO DOPO COSTRUZIONE
        ping_search_engines()

    except Exception as e:
        logging.error("Errore generazione feed: %s", e)

# ============================================================
# Thread di aggiornamento automatico
# ============================================================
def background_populator():
    last_rebuild = 0
    while True:
        try:
            items, _ = generate_items()
            if items:
                logging.info("🆕 Batch completato, articoli aggiornati: %d", len(items))
                for idx, it in enumerate(items[:5]):
                    logging.info("🧩 Batch item %d | titolo=%s | img=%s", idx, it.get("title"), it.get("image"))
                rebuild_feed()
                last_rebuild = time.time()
            else:
                now = time.time()
                if now - last_rebuild > FORCE_REBUILD_AFTER:
                    logging.info("♻️ Rigenerazione periodica del feed (nessun nuovo articolo).")
                    rebuild_feed()
                    last_rebuild = now
        except Exception as e:
            logging.error("Errore popolatore: %s", e)
        time.sleep(POPULATE_INTERVAL)

# ============================================================
# Endpoint Flask
# ============================================================
@app.route("/rss")
@app.route("/rss.xml")
def rss():
    feed_path = os.path.join(os.getcwd(), "feed.xml")
    if not os.path.exists(feed_path) or os.path.getsize(feed_path) < 100:
        rebuild_feed()
    if not os.path.exists(feed_path):
        return jsonify({"error": "feed.xml non trovato"}), 404
    with open(feed_path, "rb") as f:
        return Response(f.read(), mimetype="application/rss+xml")

@app.route("/debug/cache")
def debug_cache():
    if not os.path.exists(CACHE_PATH):
        return jsonify({"articles_in_cache": 0})
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_items = data.get("items", [])
        count = len(raw_items.values()) if isinstance(raw_items, dict) else len(raw_items)
    except Exception:
        count = 0
    return jsonify({"articles_in_cache": count})

@app.route("/cache/download")
def cache_download():
    return send_file(CACHE_PATH, mimetype="application/json")

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "artbooms-rss"})

@app.route("/")
def home():
    html = """
    <!DOCTYPE html>
    <html lang="it">
    <head>
      <meta charset="utf-8" />
      <title>Artbooms RSS</title>
      <meta name="google-site-verification" content="kB6T4eVcha1nR3EBJ3VdvbgYYMQ-WwhxUwG45_5Af60" />
    </head>
    <body>
      <h2>✅ Artbooms RSS è attivo</h2>
      <p>Feed: <a href="/rss">/rss</a> — Debug: <a href="/debug/cache">/debug/cache</a></p>
      <p>News sitemap: <a href="/news-sitemap.xml">/news-sitemap.xml</a></p>
    </body>
    </html>
    """
    return Response(html, mimetype="text/html")

@app.route("/news-sitemap.xml")
def news_sitemap():
    return news_sitemap_view()

# ============================================================
# 🔹 NUOVA ROTTA DI "WAKE" (risveglio manuale)
# ============================================================
@app.route("/wake")
def wake():
    """
    Risveglia manualmente Render e forza un aggiornamento del feed.
    Puoi chiamare: https://rss.artbooms.com/wake
    """
    try:
        threading.Thread(target=background_populator, daemon=True).start()
        return jsonify({"status": "ok", "message": "Popolatore avviato manualmente"}), 200
    except Exception as e:
        logging.error("Errore durante il wake: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# Avvio
# ============================================================
bootstrap_cache()
rebuild_feed()
with open("feed.xml", "rb") as _initial_feed:
    _validate_rss(_initial_feed.read())

if not any(t.name == "BackgroundPopulator" for t in threading.enumerate()):
    t = threading.Thread(target=background_populator, daemon=True, name="BackgroundPopulator")
    t.start()
    logging.info("Thread di popolamento avviato nel processo PID %s", os.getpid())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
