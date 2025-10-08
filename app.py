import os
import json
import threading
import time
from datetime import datetime, timezone
from flask import Flask, Response, jsonify, abort

import article_processor as ap
import rss_generator as rg

app = Flask(__name__)

# ===== CONFIGURAZIONE FISSA (nessuna variabile Render necessaria) =====
CACHE_PATH = "articles_cache.json"

# URL del file cache su GitHub (serve per mantenere la memoria)
GITHUB_CACHE_RAW_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"

# Numero di articoli da scaricare per ciclo
BATCH_SIZE = 3

# Intervallo di aggiornamento in secondi
UPDATE_INTERVAL_SECONDS = 60

# ===== STATO INTERNO (debug) =====
_state = {
    "thread_started": False,
    "last_cycle_start": None,
    "last_cycle_end": None,
    "last_cycle_error": None,
    "cycles": 0,
}


# ===== FUNZIONI DI AVVIO =====
def bootstrap_cache():
    """Carica la cache locale, oppure la scarica da GitHub se manca."""
    ap.ensure_cache(local_path=CACHE_PATH, github_raw_url=GITHUB_CACHE_RAW_URL or None)


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
            print(f"[Worker] ✅ Aggiornati {result['updated']} articoli. Totale in cache: {result['total']}")
        except Exception as e:
            _state["last_cycle_error"] = str(e)
            print(f"[Worker] ⚠️ Errore durante aggiornamento: {e}")
        finally:
            _state["last_cycle_end"] = datetime.now(timezone.utc).isoformat()
            time.sleep(UPDATE_INTERVAL_SECONDS)


def start_background_thread_once():
    """Avvia il thread di background una sola volta."""
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        bootstrap_cache()
        t = threading.Thread(target=_background_worker, daemon=True)
        t.start()
        _bg_started = True
        _state["thread_started"] = True


# Avvio thread all'import (Render lo lancerà automaticamente)
start_background_thread_once()


# ===== ENDPOINTS =====
@app.get("/healthz")
def healthz():
    """Check di salute del servizio (usato da Render)."""
    return jsonify({
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "cycles": _state["cycles"],
        "last_error": _state["last_cycle_error"]
    })


@app.get("/debug/cache")
def debug_cache():
    """Mostra informazioni sulla cache."""
    cache = ap.read_cache(CACHE_PATH)
    return jsonify({
        "articles_count": len(cache.get("articles", [])),
        "last_updated": cache.get("last_updated"),
        "version": cache.get("version"),
        "state": _state
    })


@app.get("/cache/download")
def download_cache():
    """Usato da GitHub Actions per scaricare la cache."""
    if not os.path.exists(CACHE_PATH):
        abort(404, description="Cache file not found")
    with open(CACHE_PATH, "rb") as f:
        data = f.read()
    return Response(data, mimetype="application/json")


@app.get("/cache/refresh")
def cache_refresh():
    """Forza manualmente un ciclo di aggiornamento immediato."""
    try:
        result = ap.update_cache_batch(batch_size=BATCH_SIZE, local_path=CACHE_PATH)
        print(f"[Manual Refresh] ✅ {result}")
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        print(f"[Manual Refresh] ⚠️ Errore: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/rss")
def rss():
    """Genera il feed RSS completo, compatibile con Google News."""
    cache = ap.read_cache(CACHE_PATH)

    # Metadati del feed
    meta = {
        "title": "Artbooms RSS Feed",
        "link": "https://www.artbooms.com",
        "description": "Ultimi articoli pubblicati su Artbooms.com",
        "language": "it-IT"
    }

    try:
        # Se esiste generate_feed(), usala
        if hasattr(rg, "generate_feed"):
            xml = rg.generate_feed(cache)
        # Se build_rss accetta due argomenti, passiamo meta
        else:
            xml = rg.build_rss(cache, meta)
    except TypeError:
        # Fallback se build_rss accetta solo un argomento
        xml = rg.build_rss(cache)

    return Response(
        xml,
        mimetype="application/rss+xml; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    try:
        bootstrap_cache()
    except Exception:
        pass
    start_background_thread_once()
    app.run(host="0.0.0.0", port=port)
