"""Bounded WebSub publishing with synchronous GitHub state transitions.

Only the background hook calls this module. Remote state is authoritative:
there is no automatic initialization or local fallback after a storage error.
WebSub has no idempotency key; a lost hub acknowledgement can consume another
bounded attempt, whereas a durably acknowledged event is never replayed.
"""
import copy
import math
import os
import threading
import time
import uuid

import requests

import websub as legacy
from websub_store import Conflict, GitHubStore, StoreError, Unavailable

LEASE_SECONDS = 180
CAS_ATTEMPTS = 3
_run_lock = threading.Lock()
_default_store = None
_default_config = None
_retry_not_before = 0
logger = legacy.logger


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _validate_document(document):
    if (not isinstance(document, dict) or type(document.get("format")) is not int
            or document["format"] != 1
            or document.get("topic") != legacy.FEED_SELF_URL
            or document.get("mode") not in ("active", "paused")
            or not isinstance(document.get("migration_id"), str)
            or not document["migration_id"].strip()
            or type(document.get("revision")) is not int or document["revision"] < 0
            or not isinstance(document.get("completed"), dict)
            or type(document.get("cancelled", False)) is not bool):
        raise ValueError("Documento durevole WebSub non valido")
    state = legacy._validate_state(document.get("state"))
    if state["version"] != 2:
        raise ValueError("La migrazione durevole richiede lo stato v2 originale")
    pending_ids = {event["id"] for event in state["pending"]}
    for event in state["pending"]:
        if len(set(event["guids"])) != len(event["guids"]):
            raise ValueError("GUID evento durevole duplicati")
        if "lease_token" in event or "lease_until" in event:
            if (not isinstance(event.get("lease_token"), str) or not event["lease_token"]
                    or not _finite(event.get("lease_until"))):
                raise ValueError("Prenotazione WebSub non valida")
    for identifier, receipt in document["completed"].items():
        if (not isinstance(identifier, str) or not identifier or identifier in pending_ids
                or not isinstance(receipt, dict)
                or receipt.get("status") not in ("accepted", "expired", "terminal")
                or not _finite(receipt.get("at"))
                or not isinstance(receipt.get("guids"), list) or not receipt["guids"]
                or any(not isinstance(guid, str) or not guid for guid in receipt["guids"])
                or not set(receipt["guids"]).issubset(state["observed_guids"])
                or type(receipt.get("hub_attempts")) is not int
                or not 0 <= receipt["hub_attempts"] <= legacy.MAX_HUB_ATTEMPTS
                or type(receipt.get("feed_checks")) is not int
                or not 0 <= receipt["feed_checks"] <= legacy.MAX_FEED_CHECKS
                or not _finite(receipt.get("expires_at"))):
            raise ValueError("Conferma durevole WebSub non valida")
    return document


def _clock(now):
    result = time.time if now is None else now if callable(now) else lambda: now
    if not _finite(result()):
        raise ValueError("Orologio WebSub non valido")
    return result


def _store_from_env():
    global _default_config, _default_store
    config = tuple(os.environ.get(key, "") for key in (
        "WEBSUB_GITHUB_TOKEN", "WEBSUB_STATE_REPOSITORY", "WEBSUB_STATE_BRANCH", "WEBSUB_STATE_FILE"))
    if _default_store is None or config != _default_config:
        _default_store = GitHubStore.from_env()
        _default_config = config
    return _default_store


def _transaction(store, change):
    """Retry only revision conflicts; ambiguous writes must first be resolved."""
    for attempt in range(CAS_ATTEMPTS):
        snapshot = store.load()
        original = _validate_document(snapshot.document)
        document = copy.deepcopy(original)
        change(document)
        if document == original:
            return original
        document["revision"] += 1
        _validate_document(document)
        try:
            return _validate_document(store.commit(snapshot, document).document)
        except Conflict:
            if attempt + 1 == CAS_ATTEMPTS:
                raise
    raise Conflict("WebSub state remained busy")


def _active(document):
    return document["mode"] == "active" and not document.get("cancelled", False)


def _event(document, identifier):
    return next((event for event in document["state"]["pending"]
                 if event["id"] == identifier), None)


def _owned(document, identifier, token):
    event = _event(document, identifier)
    return event if event is not None and event.get("lease_token") == token else None


def _leased(event, now):
    return event.get("lease_until", 0) > now


def _complete(document, event, status, now):
    document["completed"][event["id"]] = {
        "status": status, "at": now, "guids": list(event["guids"]),
        "hub_attempts": event["hub_attempts"], "feed_checks": event["feed_checks"],
        "expires_at": event["expires_at"]}
    document["state"]["pending"].remove(event)


def _reconcile(document, guids, now):
    if not _active(document):
        return
    state = document["state"]
    new = guids.difference(state["observed_guids"])
    if new:
        state["observed_guids"] = sorted(set(state["observed_guids"]) | guids)
        untouched = next((event for event in reversed(state["pending"])
                          if event["hub_attempts"] == event["feed_checks"] == 0
                          and not _leased(event, now)
                          and 0 <= now - event["created_at"] <= legacy.COALESCE_WINDOW), None)
        if untouched is not None:
            untouched["guids"] = sorted(set(untouched["guids"]) | new)
        else:
            state["pending"].append({"id": uuid.uuid4().hex, "guids": sorted(new),
                "hub_attempts": 0, "feed_checks": 0, "created_at": now,
                "expires_at": now + legacy.EVENT_TTL, "next_attempt_at": 0})
    for event in list(state["pending"]):
        if not _leased(event, now) and legacy._exhausted(event, now):
            _complete(document, event, "expired", now)


def _reserve_feed(document, token, now):
    if not _active(document):
        return
    event = next((candidate for candidate in document["state"]["pending"]
                  if candidate["next_attempt_at"] <= now and not _leased(candidate, now)
                  and not legacy._exhausted(candidate, now)), None)
    if event is not None:
        event["feed_checks"] += 1
        event.update(lease_token=token, lease_until=now + LEASE_SECONDS,
                     next_attempt_at=min(event["expires_at"], now +
                         legacy._delay(legacy.FEED_RETRY_DELAYS, event["feed_checks"])))


def _reserve_hub(document, identifier, token, now):
    event = _owned(document, identifier, token)
    if (not _active(document) or event is None or not _leased(event, now)
            or now >= event["expires_at"] or event["hub_attempts"] >= legacy.MAX_HUB_ATTEMPTS):
        return
    event["hub_attempts"] += 1
    event["lease_until"] = now + LEASE_SECONDS
    event["next_attempt_at"] = min(event["expires_at"], now +
        legacy._delay(legacy.HUB_RETRY_DELAYS, event["hub_attempts"]))


def _finish(document, identifier, token, now, accepted=False, terminal=False, retry_after=None):
    event = _owned(document, identifier, token)
    # A pause permits acknowledgement of an already sent request. Cancellation
    # is only available before first activation and must never authorize sends.
    if event is None or document.get("cancelled", False):
        return
    if accepted or terminal or legacy._exhausted(event, now):
        _complete(document, event, "accepted" if accepted else "terminal" if terminal else "expired", now)
    else:
        event.pop("lease_token", None)
        event.pop("lease_until", None)
        if retry_after is not None:
            event["next_attempt_at"] = min(event["expires_at"],
                max(event["next_attempt_at"], now + retry_after))


def run(content, store=None, now=None):
    """One background cycle, at most one hub POST, with all budgets durable.

    ``store`` and ``now`` allow isolated tests using the production transitions.
    Never call this function from an HTTP handler.
    """
    global _retry_not_before
    explicit_store = store is not None
    clock = _clock(now)
    if not explicit_store and clock() < _retry_not_before:
        return False
    # A different local invocation returns immediately; different instances use
    # the authoritative per-event lease and fast-forward Git ref transactions.
    # No filesystem state is needed after a restart/deploy.
    lock = _run_lock if not explicit_store else None
    if lock is not None and not lock.acquire(blocking=False):
        return False
    try:
        guids = legacy._feed_guids(content)
        store = store or _store_from_env()
        document = _transaction(store, lambda doc: _reconcile(doc, guids, clock()))
        if not _active(document):
            return False
        if not any(event["next_attempt_at"] <= clock() and not _leased(event, clock())
                   and not legacy._exhausted(event, clock())
                   for event in document["state"]["pending"]):
            # An unchanged feed costs one authoritative ref read, no commits.
            return False
        token = uuid.uuid4().hex
        document = _transaction(store, lambda doc: _reserve_feed(doc, token, clock()))
        event = next((candidate for candidate in document["state"]["pending"]
                      if candidate.get("lease_token") == token), None)
        if event is None or not _leased(event, clock()):
            return False
        identifier = event["id"]
        try:
            legacy._public_feed_ready(event["guids"])
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            _transaction(store, lambda doc: _finish(doc, identifier, token, clock(),
                                                    retry_after=retry_after))
            logger.warning("WebSub GitHub: feed non pronto, evento %s; POST non consumati", identifier)
            return False
        previous_attempts = event["hub_attempts"]
        document = _transaction(store, lambda doc: _reserve_hub(doc, identifier, token, clock()))
        event = _owned(document, identifier, token)
        if event is None or event["hub_attempts"] != previous_attempts + 1:
            return False
        # Confirm ownership after the reservation, including a possibly lost
        # PATCH response resolved via ancestry, before the irreversible POST.
        document = _validate_document(store.load().document)
        event = _owned(document, identifier, token)
        if (not _active(document) or event is None or not _leased(event, clock())
                or clock() >= event["expires_at"]):
            return False
        accepted, terminal, retry_after = False, False, None
        try:
            response = requests.post(legacy.WEBSUB_HUB_URL,
                data={"hub.mode": "publish", "hub.url": legacy.FEED_SELF_URL},
                headers=legacy.HEADERS, timeout=legacy.HTTP_TIMEOUT, allow_redirects=False)
            try:
                status = response.status_code
                accepted = 200 <= status < 300
                terminal = not accepted and status < 500 and status not in (408, 425, 429)
                retry_after = legacy._retry_after(response) if not accepted and not terminal else None
                if not accepted:
                    logger.warning("WebSub GitHub: hub HTTP %d, evento %s, POST %d/%d",
                                   status, identifier, event["hub_attempts"], legacy.MAX_HUB_ATTEMPTS)
            finally:
                response.close()
        except requests.RequestException:
            logger.warning("WebSub GitHub: esito hub incerto, evento %s, POST %d/%d",
                           identifier, event["hub_attempts"], legacy.MAX_HUB_ATTEMPTS)
        document = _transaction(store, lambda doc: _finish(doc, identifier, token, clock(),
            accepted=accepted, terminal=terminal, retry_after=retry_after))
        acknowledged = document["completed"].get(identifier, {}).get("status") == "accepted"
        if accepted and acknowledged:
            logger.info("WebSub GitHub: conferma durevole evento %s (%d GUID), POST %d",
                        identifier, len(event["guids"]), event["hub_attempts"])
            return True
        return False
    except (StoreError, ValueError, TypeError, KeyError) as exc:
        if not explicit_store:
            delay = getattr(exc, "retry_after", None)
            _retry_not_before = clock() + max(120, delay or 0)
        logger.warning("WebSub GitHub: stato non confermato (%s); invio sospeso, RSS conservato",
                       type(exc).__name__)
        return False
    finally:
        if lock is not None:
            lock.release()
