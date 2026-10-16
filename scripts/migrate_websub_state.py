"""Explicit WebSub migration/control CLI; importing it performs no I/O.

The fence/export commands also work with the currently deployed v2 publisher:
only cache_safety is needed, not the new GitHub backend or a feed-derived baseline.
"""
import argparse
import copy
import errno
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cache_safety import atomic_json, cache_transaction, _file_lock, _file_unlock

TOPIC = "https://rss.artbooms.com/rss"
FENCE_VERSION = "migration-fence-v1"
EVENT_TTL = 72 * 3600


class MigrationError(ValueError):
    """A safe, operator-readable error without remote bodies or credentials."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError("Chiave JSON duplicata: migrazione rifiutata")
        result[key] = value
    return result


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_unique_object)


def _migration_id(value):
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise MigrationError("migration_id deve essere un UUID valido") from None
    return value


def validate_legacy_state(state):
    """Validate existing v2 without upgrading, normalizing, or changing budgets.

    Kept independent of the new websub module so this reviewed script can fence
    the old deployment before the new application code is deployed.
    """
    if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 2:
        raise MigrationError("Richiesto stato WebSub v2 esistente; nessuna baseline o conversione")
    observed, pending = state.get("observed_guids"), state.get("pending")
    if (not isinstance(observed, list) or not isinstance(pending, list)
            or any(not isinstance(guid, str) or not guid for guid in observed)
            or len(set(observed)) != len(observed)):
        raise MigrationError("Archivio GUID WebSub v2 non valido")
    identifiers = set()
    for event in pending:
        if not isinstance(event, dict):
            raise MigrationError("Evento WebSub v2 non valido")
        identifier, guids = event.get("id"), event.get("guids")
        values = [event.get(key) for key in ("created_at", "expires_at", "next_attempt_at")]
        if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                or not isinstance(guids, list) or not guids
                or any(not isinstance(guid, str) or not guid for guid in guids)
                or len(set(guids)) != len(guids)
                or not set(guids).issubset(observed)
                or type(event.get("hub_attempts")) is not int or not 0 <= event["hub_attempts"] <= 8
                or type(event.get("feed_checks")) is not int or not 0 <= event["feed_checks"] <= 40
                or any(type(value) not in (float, int) or not math.isfinite(value) for value in values)
                or event["expires_at"] != event["created_at"] + EVENT_TTL):
            raise MigrationError("Budget, scadenza o GUID evento WebSub v2 non validi")
        identifiers.add(identifier)
    try:
        _canonical(state)
    except (TypeError, ValueError, UnicodeError):
        raise MigrationError("Stato WebSub v2 non serializzabile senza perdite") from None
    return state


def _validate_fence(fence, migration_id=None):
    if not isinstance(fence, dict) or fence.get("version") != FENCE_VERSION:
        raise MigrationError("Fence di migrazione assente: non ricostruire lo stato dal feed")
    _migration_id(fence.get("migration_id"))
    if migration_id is not None and fence["migration_id"] != _migration_id(migration_id):
        raise MigrationError("Il fence appartiene a una migrazione diversa")
    if fence.get("topic") != TOPIC:
        raise MigrationError("Topic del fence non valido")
    validate_legacy_state(fence.get("legacy_state"))
    expected = hashlib.sha256(_canonical(fence["legacy_state"])).hexdigest()
    if fence.get("legacy_sha256") != expected:
        raise MigrationError("Il contenuto del fence non corrisponde alla copia originale")
    return fence


def read_fenced_state(path, migration_id=None):
    return _validate_fence(_read_json(path), migration_id)


@contextmanager
def _legacy_transaction(path, wait_timeout=60, poll_interval=0.1):
    """Wait boundedly for an in-flight publisher; hold no lock during HTTP."""
    if (type(wait_timeout) not in (float, int) or not math.isfinite(wait_timeout)
            or not 0 <= wait_timeout <= 300 or not 0 < poll_interval <= 1):
        raise MigrationError("Attesa lock non valida (massimo 300 secondi)")
    deadline = time.monotonic() + wait_timeout
    handle = open(os.fspath(path) + ".publish.lock", "a+b")
    locked = False
    try:
        while not locked:
            try:
                _file_lock(handle, blocking=False)
                locked = True
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise MigrationError("Publisher occupato: nessun fence scritto; riprovare") from None
                time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
        while True:
            with cache_transaction(path, blocking=False) as acquired:
                if acquired:
                    yield
                    return
            if time.monotonic() >= deadline:
                raise MigrationError("Stato occupato: nessuna modifica; riprovare")
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    finally:
        try:
            if locked:
                _file_unlock(handle)
        finally:
            handle.close()


def fence_legacy_state(path, migration_id, wait_timeout=60, poll_interval=0.1):
    """Stop only legacy WebSub and preserve the complete authoritative v2 file."""
    _migration_id(migration_id)
    with _legacy_transaction(path, wait_timeout, poll_interval):
        state = _read_json(path)  # missing/unreadable/corrupt never means empty
        if isinstance(state, dict) and state.get("version") == FENCE_VERSION:
            return copy.deepcopy(_validate_fence(state, migration_id))
        validate_legacy_state(state)
        fence = {"version": FENCE_VERSION, "migration_id": migration_id, "topic": TOPIC,
                 "legacy_state": copy.deepcopy(state),
                 "legacy_sha256": hashlib.sha256(_canonical(state)).hexdigest(),
                 "fenced_at": datetime.now(timezone.utc).isoformat()}
        atomic_json(path, fence)
        return copy.deepcopy(fence)


def build_document(fence):
    _validate_fence(fence)
    return {"format": 1, "topic": TOPIC, "state": copy.deepcopy(fence["legacy_state"]),
            "completed": {}, "mode": "paused", "migration_id": fence["migration_id"], "revision": 0}


def _validate_document(document):
    if (not isinstance(document, dict) or type(document.get("format")) is not int
            or document["format"] != 1 or document.get("topic") != TOPIC
            or document.get("mode") not in ("paused", "active")
            or not isinstance(document.get("completed"), dict)
            or type(document.get("revision")) is not int or document["revision"] < 0
            or ("cancelled" in document and type(document["cancelled"]) is not bool)):
        raise MigrationError("Documento remoto WebSub non valido")
    _migration_id(document.get("migration_id"))
    state = validate_legacy_state(document.get("state"))
    pending_ids = {event["id"] for event in state["pending"]}
    for event in state["pending"]:
        if "lease_token" in event or "lease_until" in event:
            until = event.get("lease_until")
            if (not isinstance(event.get("lease_token"), str) or not event["lease_token"]
                    or type(until) not in (int, float) or not math.isfinite(until)):
                raise MigrationError("Prenotazione remota WebSub non valida")
    for identifier, receipt in document["completed"].items():
        if not isinstance(receipt, dict):
            raise MigrationError("Conferma remota WebSub non valida")
        guids = receipt.get("guids")
        if (not isinstance(identifier, str) or not identifier or identifier in pending_ids
                or receipt.get("status") not in ("accepted", "expired", "terminal")
                or any(type(receipt.get(key)) not in (int, float)
                       or not math.isfinite(receipt[key]) for key in ("at", "expires_at"))
                or not isinstance(guids, list) or not guids
                or any(not isinstance(guid, str) or not guid for guid in guids)
                or not set(guids).issubset(state["observed_guids"])
                or type(receipt.get("hub_attempts")) is not int or not 0 <= receipt["hub_attempts"] <= 8
                or type(receipt.get("feed_checks")) is not int or not 0 <= receipt["feed_checks"] <= 40):
            raise MigrationError("Conferma remota WebSub non valida")
    _canonical(document)
    return document


def _validate_initial(document):
    _validate_document(document)
    if (document["mode"] != "paused" or document["revision"] != 0 or document["completed"]
            or document.get("cancelled", False)):
        raise MigrationError("Upload ammesso solo per la copia iniziale paused, revision 0")
    return document


def initialize_remote(store, document, allow_initialize=False):
    """Explicit creation only; verify content and never overwrite existing state."""
    _validate_initial(document)
    if allow_initialize:
        store.initialize(copy.deepcopy(document))
    # Without --initialize-branch, even a missing branch is only an error.
    snapshot = store.load()
    remote = _validate_document(snapshot.document)
    if (remote["migration_id"] != document["migration_id"]
            or _canonical(remote) != _canonical(document)):
        raise MigrationError("Il remoto è diverso o più recente: nessuna sovrascrittura")
    return snapshot


def remote_status(store):
    snapshot = store.load()
    _validate_document(snapshot.document)
    return snapshot


def set_remote_mode(store, mode, migration_id):
    if mode not in ("paused", "active"):
        raise MigrationError("Modalità WebSub non valida")
    snapshot = remote_status(store)
    document = snapshot.document
    if document["migration_id"] != _migration_id(migration_id):
        raise MigrationError("migration_id remoto diverso")
    if document.get("cancelled", False):
        raise MigrationError("Migrazione annullata: riattivazione vietata")
    if document["mode"] == mode:
        return snapshot
    candidate = copy.deepcopy(document)
    candidate.update(mode=mode, revision=document["revision"] + 1)
    # No blind retry after a conflict, and no reset of pending/leases/history.
    result = store.commit(snapshot, candidate)
    _validate_document(result.document)
    if result.document.get("mode") != mode or result.document.get("cancelled", False):
        raise MigrationError("Modalità cambiata durante il controllo: rileggere lo stato")
    return result


def rollback_fence(store, path, migration_id, wait_timeout=60):
    """Cancel remotely before restoring v2; allowed only before any transition."""
    _migration_id(migration_id)
    local = _read_json(path)
    if isinstance(local, dict) and local.get("version") == FENCE_VERSION:
        legacy = _validate_fence(local, migration_id)["legacy_state"]
    else:
        legacy = validate_legacy_state(local)
    snapshot = remote_status(store)
    remote = snapshot.document
    if (remote["migration_id"] != migration_id or remote["mode"] != "paused"
            or remote["completed"] or _canonical(remote["state"]) != _canonical(legacy)):
        raise MigrationError("Remoto attivo o cambiato: ripristino locale vietato")
    if remote.get("cancelled", False):
        if remote["revision"] != 1:
            raise MigrationError("Remoto avanzato dopo l'annullamento: ripristino vietato")
    else:
        if remote["revision"] != 0:
            raise MigrationError("Il remoto ha già transizioni: ripristino locale vietato")
        cancelled = copy.deepcopy(remote)
        cancelled.update(cancelled=True, revision=1)
        store.commit(snapshot, cancelled)
        confirmed = remote_status(store).document
        if _canonical(confirmed) != _canonical(cancelled):
            raise MigrationError("Annullamento remoto non verificato: fence conservato")
    # All remote HTTP has completed before either local lock is acquired.
    with _legacy_transaction(path, wait_timeout):
        current = _read_json(path)
        if isinstance(current, dict) and current.get("version") == FENCE_VERSION:
            original = _validate_fence(current, migration_id)["legacy_state"]
            if _canonical(original) != _canonical(legacy):
                raise MigrationError("Il fence locale è cambiato: nessun ripristino")
            atomic_json(path, copy.deepcopy(original))
        elif _canonical(validate_legacy_state(current)) != _canonical(legacy):
            raise MigrationError("Lo stato legacy è già avanzato: nessuna sovrascrittura")
    return remote_status(store)


def export_document(document, output):
    """Write a new private backup outside cache; never overwrite an old export."""
    _validate_document(document)
    target = Path(output).resolve()
    cache_dir = Path(os.environ.get("CACHE_PATH", "cache/articles_cache.json")).resolve().parent
    if target == cache_dir or cache_dir in target.parents:
        raise MigrationError("Salvare il backup fuori dalla directory cache")
    content = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _store(args):
    # Lazy import: fence/export work on the legacy deployment without this file.
    from websub_store import GitHubStore
    token = os.environ.get("WEBSUB_GITHUB_TOKEN", "")
    if not token:
        if not sys.stdin.isatty():
            raise MigrationError("Token assente: usare una shell interattiva con prompt nascosto")
        token = getpass.getpass("Token GitHub (input nascosto): ")
    return GitHubStore(args.repo, args.branch, args.file, token)


def _summary(document, head=None):
    return {"migration_id": document["migration_id"], "mode": document["mode"],
            "revision": document["revision"], "cancelled": document.get("cancelled", False),
            "observed_guids": len(document["state"]["observed_guids"]),
            "pending": len(document["state"]["pending"]), "completed": len(document["completed"]),
            **({"head": head} if head else {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("WEBSUB_STATE_REPOSITORY", "artbooms/artbooms-rss"))
    parser.add_argument("--branch", default=os.environ.get("WEBSUB_STATE_BRANCH", "websub-state"))
    parser.add_argument("--file", default=os.environ.get("WEBSUB_STATE_FILE", "websub-state.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    fence = commands.add_parser("fence", help="Ferma solo il publisher legacy, conserva tutto lo stato v2")
    fence.add_argument("--state-path", required=True)
    fence.add_argument("--migration-id", default=None)
    fence.add_argument("--wait-timeout", type=float, default=60)
    export = commands.add_parser("export", help="Esporta una copia locale del fence o del remoto")
    export.add_argument("--state-path")
    export.add_argument("--migration-id")
    export.add_argument("--output", required=True)
    upload = commands.add_parser("upload", help="Importa la copia iniziale paused, senza sovrascrivere")
    upload.add_argument("--input", required=True)
    upload.add_argument("--initialize-branch", action="store_true")
    status = commands.add_parser("status", help="Mostra solo conteggi, modalità e revisione")
    status.add_argument("--state-path")
    status.add_argument("--migration-id")
    pause = commands.add_parser("pause", help="Pausa persistente del publisher remoto")
    pause.add_argument("--migration-id", required=True)
    resume = commands.add_parser("resume", help="Attiva esplicitamente il publisher remoto")
    resume.add_argument("--migration-id", required=True)
    resume.add_argument("--confirm-activation", action="store_true", required=True)
    rollback = commands.add_parser("rollback-fence", help="Annulla prima dell'attivazione, conserva la storia remota")
    rollback.add_argument("--state-path", required=True)
    rollback.add_argument("--migration-id", required=True)
    rollback.add_argument("--wait-timeout", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        if args.command == "fence":
            document = build_document(fence_legacy_state(args.state_path, args.migration_id or str(uuid.uuid4()),
                                                         args.wait_timeout))
            result = _summary(document)
        elif args.command in ("status", "export") and args.state_path:
            document = build_document(read_fenced_state(args.state_path, args.migration_id))
            if args.command == "export":
                export_document(document, args.output)
            result = _summary(document)
        elif args.command == "upload":
            snapshot = initialize_remote(_store(args), _read_json(args.input), args.initialize_branch)
            result = _summary(snapshot.document, snapshot.head)
        elif args.command == "rollback-fence":
            snapshot = rollback_fence(_store(args), args.state_path, args.migration_id, args.wait_timeout)
            result = _summary(snapshot.document, snapshot.head)
        else:
            store = _store(args)
            if args.command in ("pause", "resume"):
                snapshot = set_remote_mode(store, "paused" if args.command == "pause" else "active", args.migration_id)
            else:
                snapshot = remote_status(store)
                if args.command == "export":
                    export_document(snapshot.document, args.output)
            result = _summary(snapshot.document, snapshot.head)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        # Remote exceptions, file errors and HTTP URLs never echo credentials.
        print("Operazione non completata (%s); stato non azzerato, verificare e riprovare" % type(exc).__name__,
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
