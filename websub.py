"""Best-effort WebSub publishing, independent of the article cache.

Missing state establishes an archive baseline rather than rebroadcasting it.
Acknowledged events survive restarts. An HTTP timeout after the hub accepted a
POST is inherently ambiguous: WebSub has no publisher idempotency key, so a
bounded retry may repeat that request (including after a crash before the ack).
"""
import errno
import json
import logging
import math
import os
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager

import requests
from article_processor import CACHE_PATH
from cache_safety import atomic_json, cache_transaction, _file_lock, _file_unlock

WEBSUB_HUB_URL = "https://pubsubhubbub.appspot.com/"
FEED_SELF_URL = "https://rss.artbooms.com/rss"
STATE_PATH = os.environ.get("WEBSUB_STATE_PATH", CACHE_PATH + ".websub.json")
HTTP_TIMEOUT = (3, 10)
MAX_ATTEMPTS = 3
RETRY_DELAYS = (5, 30, 120)
ATOM_LINK = "{http://www.w3.org/2005/Atom}link"
HEADERS = {"User-Agent": "ArtboomsRSS-WebSub/1.0 (+https://www.artbooms.com)"}
logger = logging.getLogger("websub")
_publisher_lock = threading.Lock()


def _feed_guids(content):
    root = ET.fromstring(content)
    channel = root.find("channel")
    if root.tag != "rss" or root.get("version") != "2.0" or channel is None:
        raise ValueError("RSS 2.0/channel assente")
    if any(not (channel.findtext(key) or "").strip()
           for key in ("title", "link", "description")):
        raise ValueError("Metadati RSS obbligatori assenti")
    links = {(link.get("rel"), link.get("href")) for link in channel.findall(ATOM_LINK)}
    if ("self", FEED_SELF_URL) not in links or ("hub", WEBSUB_HUB_URL) not in links:
        raise ValueError("Dichiarazione WebSub hub/self non valida")
    items = channel.findall("item")
    if any(not (item.findtext("title") or item.findtext("description") or "").strip()
           for item in items):
        raise ValueError("Titolo/descrizione articolo RSS assenti")
    guids = [(item.findtext("guid") or "").strip() for item in items]
    if any(not guid for guid in guids) or len(set(guids)) != len(guids):
        raise ValueError("GUID RSS mancanti o duplicati")
    return set(guids)


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return None
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Versione stato WebSub non valida")
    observed, pending = state.get("observed_guids"), state.get("pending")
    if (not isinstance(observed, list) or not isinstance(pending, list)
            or any(not isinstance(guid, str) or not guid for guid in observed)
            or len(set(observed)) != len(observed)):
        raise ValueError("Archivio GUID WebSub non valido")
    identifiers = set()
    for event in pending:
        if not isinstance(event, dict):
            raise ValueError("Evento WebSub non valido")
        identifier, guids = event.get("id"), event.get("guids")
        attempts, next_at = event.get("attempts"), event.get("next_attempt_at")
        if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                or not isinstance(guids, list) or not guids
                or any(not isinstance(guid, str) or not guid for guid in guids)
                or not set(guids).issubset(observed)
                or type(attempts) is not int or not 0 <= attempts <= MAX_ATTEMPTS
                or type(next_at) not in (float, int) or not math.isfinite(next_at)):
            raise ValueError("Tentativi/GUID evento WebSub non validi")
        identifiers.add(identifier)
    return state


def observe_feed(content, state_path=None):
    """Record a validated, atomically available feed; never perform network I/O.

    Call after releasing the cache/feed transaction. The state uses a separate
    transaction, and no article/cache/cursor data is written here.
    """
    path = state_path or STATE_PATH
    try:
        guids = _feed_guids(content)
        with cache_transaction(path):
            state = _load_state(path)
            if state is None:
                atomic_json(path, {"version": 1, "observed_guids": sorted(guids), "pending": []})
                logger.info("WebSub: baseline iniziale di %d GUID, nessuna notifica", len(guids))
                return False
            new_guids = guids.difference(state["observed_guids"])
            if not new_guids:
                return False
            state["observed_guids"] = sorted(set(state["observed_guids"]) | guids)
            # Only untouched events can coalesce: retry budgets are never reset.
            untouched = next((event for event in reversed(state["pending"])
                              if event["attempts"] == 0), None)
            if untouched is not None:
                untouched["guids"] = sorted(set(untouched["guids"]) | new_guids)
            else:
                state["pending"].append({"id": uuid.uuid4().hex, "guids": sorted(new_guids),
                                         "attempts": 0, "next_attempt_at": 0})
            atomic_json(path, state)
            logger.info("WebSub: %d nuovi GUID in attesa della disponibilità pubblica", len(new_guids))
            return True
    except Exception:
        logger.warning("WebSub: osservazione/stato non disponibile; RSS conservato", exc_info=True)
        return False


@contextmanager
def _publisher_transaction(path):
    # Independent of cache_transaction's process-wide thread lock. A GET /rss
    # may itself observe the feed; holding its state lock here would deadlock.
    if not _publisher_lock.acquire(blocking=False):
        yield False
        return
    handle, locked = None, False
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        handle = open(path + ".publish.lock", "a+b")
        try:
            _file_lock(handle, blocking=False)
            locked = True
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
        yield locked
    finally:
        if handle is not None:
            try:
                if locked:
                    _file_unlock(handle)
            finally:
                handle.close()
        _publisher_lock.release()


def _prepare_attempt(path):
    with cache_transaction(path):
        state = _load_state(path)
        if state is None:
            return None
        exhausted = [event for event in state["pending"] if event["attempts"] >= MAX_ATTEMPTS]
        state["pending"] = [event for event in state["pending"] if event not in exhausted]
        if exhausted:
            logger.warning("WebSub: %d eventi esauriti dopo riavvio, nessun nuovo invio", len(exhausted))
        now = time.time()
        event = next((event for event in state["pending"] if event["next_attempt_at"] <= now), None)
        if event is not None:
            event["attempts"] += 1
            event["next_attempt_at"] = now + RETRY_DELAYS[event["attempts"] - 1]
        if event is not None or exhausted:
            # Reserve and persist BEFORE I/O: a restart cannot reset attempts.
            atomic_json(path, state)
        return event


def _finish_attempt(path, event, accepted):
    with cache_transaction(path):
        state = _load_state(path)
        if state is None:
            return
        if accepted or event["attempts"] >= MAX_ATTEMPTS:
            state["pending"] = [candidate for candidate in state["pending"]
                                if candidate["id"] != event["id"]]
            atomic_json(path, state)
            if not accepted:
                logger.warning("WebSub: evento %s esaurito dopo %d tentativi", event["id"], event["attempts"])


def _public_feed_ready(guids):
    response = requests.get(FEED_SELF_URL, headers=dict(HEADERS, **{"Cache-Control": "no-cache"}),
                            timeout=HTTP_TIMEOUT, allow_redirects=False)
    try:
        if response.status_code != 200:
            raise ValueError("Feed pubblico HTTP %s" % response.status_code)
        if not set(guids).issubset(_feed_guids(response.content)):
            raise ValueError("Nuovi GUID non ancora presenti nel feed pubblico")
    finally:
        response.close()


def publish_pending(state_path=None):
    """Leader-only background hook; one bounded event attempt per call.

    Readiness failures consume the same persisted, limited retry budget as hub
    failures. No network operation holds a cache/feed/state transaction.
    """
    path = state_path or STATE_PATH
    try:
        with _publisher_transaction(path) as acquired:
            if not acquired:
                return False
            event = _prepare_attempt(path)
            if event is None:
                return False
            accepted = False
            try:
                _public_feed_ready(event["guids"])
                response = requests.post(WEBSUB_HUB_URL,
                                         data={"hub.mode": "publish", "hub.url": FEED_SELF_URL},
                                         headers=HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=False)
                try:
                    if not 200 <= response.status_code < 300:
                        raise ValueError("Hub HTTP %s" % response.status_code)
                    accepted = True
                    logger.info("WebSub: hub accetta evento %s (%d GUID), tentativo %d",
                                event["id"], len(event["guids"]), event["attempts"])
                finally:
                    response.close()
            except Exception:
                logger.warning("WebSub: evento %s, tentativo %d/%d fallito; RSS conservato",
                               event["id"], event["attempts"], MAX_ATTEMPTS, exc_info=True)
            _finish_attempt(path, event, accepted)
            return accepted
    except Exception:
        logger.warning("WebSub: pubblicazione/stato non disponibile; RSS conservato", exc_info=True)
        return False
