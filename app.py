import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests
from flask import Flask, Response, jsonify, request, send_file
from article_processor import CACHE_PATH, generate_items
from cache_safety import atomic_write, atomic_json, cache_entries, cache_transaction, merge_cache
from cache_safety import with_editorial_authors
from editorial_taxonomy import categories_for
from news_sitemap import news_sitemap_view
from rss_generator import build_rss

RAW_CACHE_URL = os.environ.get("RAW_CACHE_URL", "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json")
USER_AGENT = "ArtboomsRSS/1.0 (+https://www.artbooms.com)"
FEED_SELF_URL = "https://rss.artbooms.com/rss"
FEED_PATH = os.environ.get("FEED_PATH", "feed.xml")
POPULATE_INTERVAL = max(0.1, float(os.environ.get("POPULATE_INTERVAL", "120")))
LEADER_RETRY_INTERVAL = min(2.0, POPULATE_INTERVAL)
LEADER_PATH = CACHE_PATH + ".populator.lock"
WAKE_PATH = CACHE_PATH + ".wake"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("artbooms")
_snapshot = None  # (file token, validated bytes, ETag)
_source_token = None
_source_digest = None
_update_lock = threading.Lock()
_start_lock = threading.Lock()
_stop = threading.Event()
_background_thread = None


def _file_token(path):
    try:
        s = os.stat(path)
        return (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size)
    except FileNotFoundError:
        return None


def _read_file(path):
    with open(path, "rb") as handle:
        content = handle.read()
        s = os.fstat(handle.fileno())
        return content, (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size)


def _cache_entries(data):
    entries = cache_entries(data)
    raw = data.get("items", {})
    if len(entries) != len(raw):
        logger.warning("Lettura cache: %d record inutilizzabili esclusi dal feed", len(raw) - len(entries))
    return entries


def _validate_rss(content):
    root = ET.fromstring(content)
    items = root.findall("./channel/item")
    if root.tag != "rss" or root.get("version") != "2.0" or not items:
        raise ValueError("RSS non valido o privo di articoli")
    guids = [item.findtext("guid") for item in items]
    if None in guids or len(guids) != len(set(guids)):
        raise ValueError("GUID mancanti o duplicati")


def _load_snapshot():
    global _snapshot
    token = _file_token(FEED_PATH)
    if _snapshot is not None and token == _snapshot[0]:
        return _snapshot
    if token is not None:
        try:
            content, token = _read_file(FEED_PATH)
            _validate_rss(content)
            _snapshot = (token, content, hashlib.sha256(content).hexdigest())
        except (OSError, ValueError, ET.ParseError):
            logger.exception("XML su disco non utilizzabile: conservo l'ultima copia in memoria")
    return _snapshot


def bootstrap_cache():
    # Do not hold the write lock during a remote request.
    remote = None
    if os.environ.get("CACHE_BOOTSTRAP_REMOTE", "1") == "1":
        try:
            response = requests.get(RAW_CACHE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
            response.raise_for_status()
            remote = response.json()
            _cache_entries(remote)
        except Exception:
            logger.warning("Cache GitHub non disponibile all'avvio", exc_info=True)
            remote = None
    with cache_transaction(CACHE_PATH):
        local = None
        try:
            with open(CACHE_PATH, encoding="utf-8") as handle:
                local = json.load(handle)
            _cache_entries(local)
        except (OSError, ValueError, TypeError):
            local = None
        if local is None and remote is None:
            raise RuntimeError("Nessuna cache utilizzabile disponibile")
        if remote is None:
            selected = local
        else:
            selected = remote if local is None else merge_cache(local, remote)[0]
        selected = with_editorial_authors(selected)
        if selected != local:
            atomic_json(CACHE_PATH, selected)


def _editorial_xml(content):
    return re.sub(br"<lastBuildDate>[^<]*</lastBuildDate>", b"", content)


def rebuild_feed():
    """Refresh from the cache; keep last valid bytes on every failure."""
    global _snapshot, _source_digest, _source_token
    try:
        if (_snapshot is not None and _file_token(CACHE_PATH) == _source_token
                and _file_token(FEED_PATH) == _snapshot[0]):
            return False
        with cache_transaction(FEED_PATH):
            old = _load_snapshot()
            cache_bytes, cache_token = _read_file(CACHE_PATH)
            entries = _cache_entries(json.loads(cache_bytes))
            items = [i for i in entries.values() if "/blog/" in i["url"]]
            if not items:
                raise ValueError("Nessun articolo utilizzabile")
            fields = ("url", "title", "description", "author", "published", "image")
            editorial = [
                ({k: i.get(k) for k in fields}, categories_for(i))
                for i in sorted(items, key=lambda i: i["url"])
            ]
            digest = hashlib.sha256(json.dumps(editorial, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
            if (old is not None and digest == _source_digest
                    and _file_token(FEED_PATH) == old[0]):
                _source_token = cache_token
                return False
            xml = build_rss(items, {
                "title": "Artbooms RSS Feed", "link": "https://www.artbooms.com",
                "description": "Ultimi articoli da Artbooms", "language": "it-IT",
                "self": FEED_SELF_URL,
            })
            if isinstance(xml, tuple):
                xml = xml[0]
            content = xml.encode("utf-8") if isinstance(xml, str) else xml
            _validate_rss(content)
            changed = old is None or _editorial_xml(old[1]) != _editorial_xml(content)
            # Repair a missing/corrupt disk copy even if in-memory bytes survive.
            if changed:
                atomic_write(FEED_PATH, content)
            elif _file_token(FEED_PATH) != old[0]:
                atomic_write(FEED_PATH, old[1])
            _load_snapshot()
            _source_token, _source_digest = cache_token, digest
            if changed:
                logger.info("Feed pubblicato: %d articoli", len(items))
            return changed
    except Exception:
        logger.exception("Ricostruzione fallita: conservo l'ultimo feed valido")
        _load_snapshot()
        return False


def _update_once():
    if not _update_lock.acquire(blocking=False):
        return False, False
    changed, rebuilt = False, False
    try:
        try:
            items, _ = generate_items()
            changed = bool(items)
        finally:
            rebuilt = rebuild_feed()
    finally:
        _update_lock.release()
    return changed, rebuilt


def background_populator():
    # This lock is independent of cache/feed write locks. Never delete it:
    # locks follow an inode, not the pathname. Kernel release covers worker death.
    os.makedirs(os.path.dirname(os.path.abspath(LEADER_PATH)), exist_ok=True)
    while not _stop.is_set():
        with open(LEADER_PATH, "a+b") as leader:
            try:
                fcntl.flock(leader.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                _stop.wait(LEADER_RETRY_INTERVAL)
                continue
            logger.info("Popolatore leader PID %s", os.getpid())
            try:
                leader.seek(0)
                leader.truncate()
                leader.write(str(os.getpid()).encode())
                leader.flush()
                next_run = 0.0
                wake_token = _file_token(WAKE_PATH)
                while not _stop.is_set():
                    token = _file_token(WAKE_PATH)
                    now = time.monotonic()
                    if now >= next_run or token != wake_token:
                        wake_token = token
                        try:
                            _update_once()
                        except Exception:
                            logger.exception("Ciclo popolatore fallito; riprovo al ciclo successivo")
                        next_run = time.monotonic() + POPULATE_INTERVAL
                    _stop.wait(LEADER_RETRY_INTERVAL)
            finally:
                fcntl.flock(leader.fileno(), fcntl.LOCK_UN)


def start_background():
    global _background_thread
    if os.environ.get("POPULATOR_ENABLED", "1") != "1":
        return
    with _start_lock:
        if _background_thread is None or not _background_thread.is_alive():
            _stop.clear()
            _background_thread = threading.Thread(
                target=background_populator, name="BackgroundPopulator", daemon=True)
            _background_thread.start()


def start_worker():
    # Called after fork by Gunicorn, including when --preload is used.
    try:
        bootstrap_cache()
    except RuntimeError:
        if _load_snapshot() is None:
            raise
        logger.exception("Avvio con l'ultimo XML valido; cache da ripristinare")
    rebuild_feed()
    if _load_snapshot() is None:
        raise RuntimeError("Avvio interrotto: nessun feed valido")
    start_background()


def stop_worker():
    _stop.set()


@app.route("/rss")
@app.route("/rss.xml")
@app.route("/feed.xml")
def rss():
    rebuild_feed()
    snapshot = _load_snapshot()
    if snapshot is None:
        response = Response("Feed temporaneamente non disponibile", status=503, mimetype="text/plain")
        response.headers["Retry-After"] = "120"
        response.headers["Cache-Control"] = "no-store"
        return response
    response = Response(snapshot[1], mimetype="application/rss+xml")
    response.set_etag(snapshot[2])
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response.make_conditional(request)


@app.route("/debug/cache")
def debug_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return jsonify(articles_in_cache=len(data.get("items", {})), usable_articles=len(_cache_entries(data)))
    except (OSError, ValueError, TypeError):
        return jsonify(articles_in_cache=0, usable_articles=0)


@app.route("/cache/download")
def cache_download():
    response = send_file(os.path.abspath(CACHE_PATH), mimetype="application/json", conditional=False)
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


@app.route("/healthz")
def healthz():
    return jsonify(ok=True, service="artbooms-rss")


@app.route("/")
def home():
    return Response('''<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<title>Artbooms RSS</title>
<meta name="google-site-verification" content="kB6T4eVcha1nR3EBJ3VdvbgYYMQ-WwhxUwG45_5Af60">
<link rel="alternate" type="application/rss+xml" title="ARTBOOMS RSS" href="https://rss.artbooms.com/rss">
</head><body><h2>Artbooms RSS</h2><p><a href="/rss">Feed RSS</a></p>
<p><a href="/news-sitemap.xml">News sitemap</a></p></body></html>''', mimetype="text/html")


@app.route("/news-sitemap.xml")
def news_sitemap():
    return news_sitemap_view()


@app.route("/wake")
def wake():
    start_background()
    atomic_write(WAKE_PATH, str(time.time_ns()).encode())
    return jsonify(status="ok", message="Aggiornamento richiesto al popolatore unico"), 200


# Importing app (including Gunicorn preload) has no network or thread side effects.
if __name__ == "__main__":
    start_worker()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), use_reloader=False)
