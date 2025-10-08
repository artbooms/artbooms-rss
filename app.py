import os
import threading
import time
from flask import Flask, jsonify, Response
import article_processor
import rss_generator

app = Flask(__name__)

CACHE_PATH = "articles_cache.json"

# ✅ ADD: URL del file cache su GitHub (serve per ricaricarlo dopo un riavvio)
GITHUB_CACHE_RAW_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/articles_cache.json"

def background_task():
    """Thread di background per aggiornare gli articoli."""
    while True:
        try:
            article_processor.generate_items()
        except Exception as e:
            print("Errore nel thread:", e)
        time.sleep(60)

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})

@app.route("/debug/cache")
def debug_cache():
    data = article_processor.load_cache()
    return jsonify({"count": len(data.get("articles", []))})

@app.route("/rss")
def rss():
    data = article_processor.load_cache()
    xml = rss_generator.build_rss(data.get("articles", []))
    return Response(xml, mimetype="application/rss+xml")

# ✅ ADD: nuovo endpoint per GitHub Actions (salva la cache vera)
@app.route("/cache/download")
def cache_download():
    """Restituisce la cache locale in formato JSON per GitHub Actions."""
    if not os.path.exists(CACHE_PATH):
        return jsonify({"error": "cache not found"}), 404
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/json")

# ✅ ADD: funzione per caricare la cache da GitHub all’avvio
def bootstrap_cache():
    """Se la cache locale manca, la scarica dal GitHub raw."""
    if os.path.exists(CACHE_PATH):
        return
    import requests
    try:
        print("[Bootstrap] Scarico la cache da GitHub...")
        r = requests.get(GITHUB_CACHE_RAW_URL, timeout=15)
        if r.status_code == 200 and r.content:
            with open(CACHE_PATH, "wb") as f:
                f.write(r.content)
            print("[Bootstrap] Cache scaricata con successo.")
        else:
            print(f"[Bootstrap] Nessuna cache disponibile (HTTP {r.status_code})")
    except Exception as e:
        print(f"[Bootstrap] Errore nel download cache: {e}")

if __name__ == "__main__":
    # ✅ ADD: tenta di ricaricare la cache da GitHub prima di avviare il thread
    bootstrap_cache()

    # avvia il thread di background
    t = threading.Thread(target=background_task, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
