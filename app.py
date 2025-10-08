import os
import json
import threading
import time
from datetime import datetime, timezone
from flask import Flask, Response, jsonify, abort

import article_processor as ap
import rss_generator as rg

app = Flask(__name__)

# ===== Config (fissa, nessuna variabile richiesta su Render) =====
CACHE_PATH = "articles_cache.json"

# URL pubblico del file cache su GitHub (serve per mantenere la memoria dopo i riavvii)
GITHUB_CACHE_RAW_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"

# Numero di articoli da scaricare a ogni ciclo
BATCH_SIZE = 3

# Intervallo (in secondi) tra un ciclo e l'altro
UPDATE_INTERVAL_SECONDS = 60

# ===== Stato interno =====
_state = {
    "thread_started": False,
    "last_cycle_start": None,
    "last_cycle_end": None,
    "last_cycle_error": None,
    "cycles": 0,
}


# ===== Bootstrap =====
def bootstrap_cache():
    """Assicura che la cache locale esista; se manca, la scarica da GitHub."""
    ap.ensure_cache(local_path=CACHE_PATH, github_raw_url=GITHUB_CACHE_RAW_URL or None)


# ===== Background worker =====
_bg_lock = threading.Lock()
_bg_started = False


def _background_worker():
    """Aggiorna periodicamente la cache in background."""
    while True:
        try:
            _state["last_cycle_start"] = datetime.now(timezone.utc).isoformat()
            result = ap.update_cache_batch(batch_size=BATCH_SIZE, local_path=CACHE_PATH)
            _state["cycles"] += 1
            _state["last_cycle_error"] = None
            print(f"[Worker] ✅ Batch aggiornato: {result['updated']} articoli, totale {result['total']}")
        except Exception as e:
            _state["last_cycle_error"] = str(e)
            print(f"[Worker] ⚠️ Errore: {e}")
        finally:
            _state["last_cycle_end"] = datetime.now(timezone.utc).isoformat()
            time.sleep(UPDATE_INTERVAL_SECONDS)


def start_background_thread_once():
    """Avvia il thread solo una volta (anche con più worker)."""
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        bootstrap_cache()
        t = threading.Thread(target=_background_worker, daemon=True)
        t.start()
        _bg_started = True
        _state["thread_started"] = True


# Avvio thread subito (Render lo lancia al boot)
start_background_thread_once()


# ===== Endpoints =====
@app.get("/healthz")
def healthz():
    """Stato rapido del servizio (usato da Render per check di salute)."""
    return jsonify({
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "cycles": _state["cycles"],
        "last_error": _state["last_cycle_error"],
