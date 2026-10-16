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
from email.utils import parsedate_to_datetime

import requests
from article_processor import CACHE_PATH
from cache_safety import atomic_json, cache_transaction, _file_lock, _file_unlock

WEBSUB_HUB_URL = "https://pubsubhubbub.appspot.com/"
FEED_SELF_URL = "https://rss.artbooms.com/rss"
HTTP_TIMEOUT = (3, 10)
EVENT_TTL = 72 * 3600
MAX_FEED_CHECKS = 40
MAX_HUB_ATTEMPTS = 8
FEED_RETRY_DELAYS = (120, 300, 900, 1800, 3600, 7200)
HUB_RETRY_DELAYS = (900, 3600, 7200, 14400, 28800, 43200, 86400, 86400)
COALESCE_WINDOW = 300
ATOM_LINK = "{http://www.w3.org/2005/Atom}link"
HEADERS = {"User-Agent": "ArtboomsRSS-WebSub/1.0 (+https://www.artbooms.com)"}
logger = logging.getLogger("websub")
_publisher_lock = threading.Lock()
_latest_feed_content = None


def resolve_state_path(cache_path=None):
    """One path policy for the app and standalone publisher; no storage setup."""
    configured = os.environ.get("WEBSUB_STATE_PATH", "").strip()
    return configured or os.fspath(cache_path or CACHE_PATH) + ".websub.json"


def _delay(delays, count):
    return delays[min(count - 1, len(delays) - 1)]


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


def _validate_state(state):
    """Validate persisted state without changing it or accessing storage."""
    if not isinstance(state, dict) or state.get("version") not in (1, 2):
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
        next_at = event.get("next_attempt_at")
        if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                or not isinstance(guids, list) or not guids
                or any(not isinstance(guid, str) or not guid for guid in guids)
                or not set(guids).issubset(observed)
                or type(next_at) not in (float, int) or not math.isfinite(next_at)):
            raise ValueError("Tentativi/GUID evento WebSub non validi")
        if state["version"] == 1:
            attempts = event.get("attempts")
            if type(attempts) is not int or not 0 <= attempts <= 3:
                raise ValueError("Tentativi WebSub legacy non validi")
        else:
            hub, checks = event.get("hub_attempts"), event.get("feed_checks")
            created, expires = event.get("created_at"), event.get("expires_at")
            if (type(hub) is not int or not 0 <= hub <= MAX_HUB_ATTEMPTS
                    or type(checks) is not int or not 0 <= checks <= MAX_FEED_CHECKS
                    or any(type(value) not in (float, int) or not math.isfinite(value)
                           for value in (created, expires))
                    or expires != created + EVENT_TTL):
                raise ValueError("Budget/scadenza evento WebSub non validi")
        identifiers.add(identifier)
    return state


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return None
    _validate_state(state)
    observed, pending = state["observed_guids"], state["pending"]
    if state["version"] == 1:
        # v1 mixed GET and POST attempts. Preserve the count conservatively as
        # hub attempts: migration never grants a fresh POST budget or replays
        # observed GUIDs. Events already removed by v1 cannot be recovered.
        now = time.time()
        for event in pending:
            attempts = event.pop("attempts")
            event.update(hub_attempts=attempts, feed_checks=0,
                         created_at=now, expires_at=now + EVENT_TTL)
            if attempts:
                event["next_attempt_at"] = max(event["next_attempt_at"],
                                                now + _delay(HUB_RETRY_DELAYS, attempts))
        state["version"] = 2
        # All callers hold the state transaction. Persist migration once even
        # when the feed is unchanged; restarts cannot extend this deadline.
        atomic_json(path, state)
        logger.info("WebSub: stato migrato a v2, %d GUID e %d pending conservati",
                    len(observed), len(pending))
    return state


def observe_feed(content, state_path=None):
    """Record a validated, atomically available feed; never perform network I/O.

    Call after releasing the cache/feed transaction. The state uses a separate
    transaction, and no article/cache/cursor data is written here.
    """
    global _latest_feed_content
    backend = os.environ.get("WEBSUB_BACKEND", "file").strip().lower()
    if backend != "file":
        if backend == "github":
            # HTTP/cache paths only hand off immutable bytes; GitHub I/O belongs
            # exclusively to the existing background publisher.
            _latest_feed_content = content
        else:
            logger.error("WebSub: backend sconosciuto; pubblicazione sospesa")
        return False
    path = state_path or resolve_state_path()
    try:
        guids = _feed_guids(content)
        with cache_transaction(path):
            state = _load_state(path)
            if state is None:
                atomic_json(path, {"version": 2, "observed_guids": sorted(guids), "pending": []})
                logger.info("WebSub: baseline iniziale di %d GUID, nessuna notifica", len(guids))
                return False
            new_guids = guids.difference(state["observed_guids"])
            if not new_guids:
                return False
            state["observed_guids"] = sorted(set(state["observed_guids"]) | guids)
            now = time.time()
            # Only fresh, untouched events coalesce; their deadline is fixed.
            untouched = next((event for event in reversed(state["pending"])
                              if event["hub_attempts"] == event["feed_checks"] == 0
                              and 0 <= now - event["created_at"] <= COALESCE_WINDOW), None)
            if untouched is not None:
                untouched["guids"] = sorted(set(untouched["guids"]) | new_guids)
            else:
                state["pending"].append({"id": uuid.uuid4().hex, "guids": sorted(new_guids),
                                         "hub_attempts": 0, "feed_checks": 0,
                                         "created_at": now, "expires_at": now + EVENT_TTL,
                                         "next_attempt_at": 0})
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


def _exhausted(event, now):
    return (now >= event["expires_at"] or event["feed_checks"] >= MAX_FEED_CHECKS
            or event["hub_attempts"] >= MAX_HUB_ATTEMPTS)


def _reserve_feed_check(path):
    with cache_transaction(path):
        state = _load_state(path)
        if state is None:
            return None
        now = time.time()
        exhausted = [event for event in state["pending"] if _exhausted(event, now)]
        state["pending"] = [event for event in state["pending"] if event not in exhausted]
        if exhausted:
            for event in exhausted:
                logger.warning("WebSub: evento %s scaduto/esaurito (%d GET, %d POST); nessun replay",
                               event["id"], event["feed_checks"], event["hub_attempts"])
        event = next((event for event in state["pending"] if event["next_attempt_at"] <= now), None)
        if event is not None:
            event["feed_checks"] += 1
            event["next_attempt_at"] = min(event["expires_at"], now +
                _delay(FEED_RETRY_DELAYS, event["feed_checks"]))
        if event is not None or exhausted:
            # Reserve and persist BEFORE I/O: a restart cannot reset attempts.
            atomic_json(path, state)
        return dict(event) if event is not None else None


def _reserve_hub_attempt(path, event):
    with cache_transaction(path):
        state = _load_state(path)
        if state is None:
            return None
        current = next((candidate for candidate in state["pending"]
                        if candidate["id"] == event["id"]), None)
        now = time.time()
        if current is None or now >= current["expires_at"]:
            return None
        if current["hub_attempts"] >= MAX_HUB_ATTEMPTS:
            return None
        current["hub_attempts"] += 1
        current["next_attempt_at"] = min(current["expires_at"], now +
            _delay(HUB_RETRY_DELAYS, current["hub_attempts"]))
        # Reserve separately AFTER a valid public GET and BEFORE the POST.
        atomic_json(path, state)
        return dict(current)


def _finish_attempt(path, event, accepted=False, terminal=False, retry_after=None):
    with cache_transaction(path):
        state = _load_state(path)
        if state is None:
            return
        current = next((candidate for candidate in state["pending"]
                        if candidate["id"] == event["id"]), None)
        if current is None:
            return
        now = time.time()
        if accepted or terminal or _exhausted(current, now):
            state["pending"] = [candidate for candidate in state["pending"]
                                if candidate["id"] != event["id"]]
            atomic_json(path, state)
            if not accepted:
                logger.warning("WebSub: evento %s terminato (%d GET, %d POST, errore permanente=%s)",
                               event["id"], current["feed_checks"], current["hub_attempts"], terminal)
        elif retry_after is not None:
            current["next_attempt_at"] = min(current["expires_at"],
                max(current["next_attempt_at"], now + retry_after))
            atomic_json(path, state)


def _retry_after(response):
    value = response.headers.get("Retry-After")
    if not isinstance(value, str):
        return None
    try:
        delay = int(value) if value.strip().isdigit() else (
            parsedate_to_datetime(value).timestamp() - time.time())
        return min(EVENT_TTL, max(0, delay)) if math.isfinite(delay) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _public_feed_ready(guids):
    response = requests.get(FEED_SELF_URL, headers=dict(HEADERS, **{"Cache-Control": "no-cache"}),
                            timeout=HTTP_TIMEOUT, allow_redirects=False)
    try:
        if response.status_code != 200:
            error = ValueError("Feed pubblico HTTP %s" % response.status_code)
            error.retry_after = _retry_after(response)
            raise error
        if not set(guids).issubset(_feed_guids(response.content)):
            raise ValueError("Nuovi GUID non ancora presenti nel feed pubblico")
    finally:
        response.close()


def publish_pending(state_path=None):
    """Leader-only background hook; one bounded event attempt per call.

    Separate GET/POST budgets and a fixed 72-hour deadline survive restarts.
    No network operation holds a cache/feed/state transaction.
    """
    backend = os.environ.get("WEBSUB_BACKEND", "file").strip().lower()
    if backend != "file":
        if backend != "github":
            logger.error("WebSub: backend sconosciuto; pubblicazione sospesa")
            return False
        if _latest_feed_content is None:
            return False
        try:
            from websub_durable import run
            return run(_latest_feed_content)
        except Exception:
            # Do not include request objects/headers in credential-bearing logs.
            logger.warning("WebSub: backend GitHub non disponibile; RSS conservato")
            return False
    path = state_path or resolve_state_path()
    try:
        with _publisher_transaction(path) as acquired:
            if not acquired:
                return False
            event = _reserve_feed_check(path)
            if event is None:
                return False
            try:
                _public_feed_ready(event["guids"])
            except Exception as exc:
                logger.warning("WebSub: feed non pronto per evento %s, controllo %d/%d; POST non consumati",
                               event["id"], event["feed_checks"], MAX_FEED_CHECKS, exc_info=True)
                _finish_attempt(path, event, retry_after=getattr(exc, "retry_after", None))
                return False
            event = _reserve_hub_attempt(path, event)
            if event is None:
                return False
            accepted, terminal, retry_after = False, False, None
            try:
                response = requests.post(WEBSUB_HUB_URL,
                                         data={"hub.mode": "publish", "hub.url": FEED_SELF_URL},
                                         headers=HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=False)
                try:
                    status = response.status_code
                    accepted = 200 <= status < 300
                    if accepted:
                        logger.info("WebSub: hub accetta evento %s (%d GUID), POST %d",
                                    event["id"], len(event["guids"]), event["hub_attempts"])
                    else:
                        terminal = status < 500 and status not in (408, 425, 429)
                        retry_after = _retry_after(response) if not terminal else None
                        logger.warning("WebSub: evento %s, hub HTTP %s, POST %d/%d, permanente=%s",
                                       event["id"], status, event["hub_attempts"], MAX_HUB_ATTEMPTS, terminal)
                finally:
                    response.close()
            except Exception:
                logger.warning("WebSub: evento %s, POST %d/%d fallito; RSS conservato",
                               event["id"], event["hub_attempts"], MAX_HUB_ATTEMPTS, exc_info=True)
            _finish_attempt(path, event, accepted, terminal, retry_after)
            return accepted
    except Exception:
        logger.warning("WebSub: pubblicazione/stato non disponibile; RSS conservato", exc_info=True)
        return False
