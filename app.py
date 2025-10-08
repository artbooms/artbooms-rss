import os
import json
import threading
import time
from datetime import datetime, timezone
from flask import Flask, Response, jsonify, abort

import article_processor as ap
import rss_generator as rg

app = Flask(__name__)

# ===== Config =====
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
GITHUB_CACHE_RAW_URL = os.environ.get(
    "GITHUB_CACHE_RAW_URL",
    # es: "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
    ""
)
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "3"))
UPDATE_INTERVAL_SECONDS = int(os.environ.get("UPDATE_INTERVAL_SECONDS", "60"))

# Stato per debug
_state = {
    "thread_started": False,
    "last_cycle_start": None,
    "last_cycle_end": None,
    "last_cycle_error": None,
    "cycles": 0,
}

# ===== Bootstrap =====
def bootstrap_cache():
    """Assicura che la cache locale esista; se manca, prova a scaricarla dal raw GitHub."""
    ap.ensure_cache(local_path=CACHE_PATH, github_raw_url=GITHUB_CACHE_RAW_URL or None)

# ===== Background worker =====
_bg_lock = threading.Lock()
_bg_started = False

def _background_worker():
    while True:
        try:
            _state["last_cycle_start"] = datetime.now(timezone.utc).isoformat()
            ap.update_cache_batch(batch_size=BATCH_SIZE, local_path=CACHE_PATH)
            _state["cycles"] += 1
            _state["last_cycle_error"] = None
        except Exception as e:
            _state["last_cycle_error"] = str(e)
        finally:
            _state["last_cycle_end"] = datetime.now(timezone.utc).isoformat()
            time.sleep(UPDATE_INTERVAL_SECONDS)

def start_background_thread_once():
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        # bootstrap prima di partire
        bootstrap_cache()
        t = threading.Thread(target=_background_worker, daemon=True)
        t.start()
        _bg_started = True
        _state["thread_started"] = True

# Avvio il thread già in import (per ambienti WSGI)
start_background_thread_once()

# ===== Endpoints =====

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z", "cycles": _state["cycles"]})

@app.get("/debug/cache")
def debug_cache():
    cache = ap.read_cache(CACHE_PATH)
    return jsonify({
        "articles_count": len(cache.get("articles", [])),
        "last_updated": cache.get("last_updated"),
        "version": cache.get("version"),
        "state": _state,
    })

@app.get("/cache/download")
def download_cache():
    if not os.path.exists(CACHE_PATH):
        abort(404, description="Cache file not found")
    with open(CACHE_PATH, "rb") as f:
        data = f.read()
    return Response(data, mimetype="application/json")

@app.get("/rss")
def rss():
    cache = ap.read_cache(CACHE_PATH)
    xml = rg.generate_feed(cache) if hasattr(rg, "generate_feed") else rg.build_rss(cache)  # compatibilità col tuo modulo
    return Response(xml, mimetype="application/rss+xml; charset=utf-8")

if __name__ == "__main__":
    # utile in dev; su Render usa gunicorn
    port = int(os.environ.get("PORT", "10000"))
    try:
        bootstrap_cache()
    except Exception:
        pass
    start_background_thread_once()
    app.run(host="0.0.0.0", port=port)
