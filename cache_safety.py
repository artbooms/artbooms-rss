import copy
import hashlib
import os
import re
import threading
import time
import json
import tempfile

from contextlib import contextmanager
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlsplit
from editorial_authors import author_for

REQUIRED_FIELDS = ("url", "title", "author", "published")
OPTIONAL_FIELDS = ("description", "author", "image", "modified")
HASH_FIELDS = (
    "title", "description", "author",
    "published", "modified", "image",
)

_XML_INVALID = re.compile(
    r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD"
    r"\U00010000-\U0010FFFF]"
)


def parsed_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def xml_text(value):
    return _XML_INVALID.sub("", value) if isinstance(value, str) else ""


def valid_article(item):
    if not isinstance(item, dict) or not all(
        isinstance(item.get(key), str) and item[key].strip()
        for key in REQUIRED_FIELDS
    ):
        return False

    url = item["url"].strip()
    if (
        not xml_text(unescape(item["title"])).strip()
        or not xml_text(item["author"]).strip()
        or xml_text(url) != url
        or any(character.isspace() for character in url)
    ):
        return False

    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return False
    except ValueError:
        return False

    return parsed_time(item["published"]) is not None


def preserve_optional(incoming, previous=None):
    result = dict(incoming)
    previous = previous if isinstance(previous, dict) else {}

    for key in OPTIONAL_FIELDS:
        value = result.get(key)
        old = previous.get(key)

        valid = (
            parsed_time(value) is not None
            if key == "modified"
            else isinstance(value, str) and bool(value.strip())
        )
        old_valid = (
            parsed_time(old) is not None
            if key == "modified"
            else isinstance(old, str) and bool(old.strip())
        )

        if not valid:
            result[key] = (
                old if old_valid
                else None if key in ("image", "modified")
                else ""
            )

    return result


def article_hash(item):
    # Conserva l'algoritmo storico per compatibilità con la cache esistente.
    basis = "".join(
        item.get(key) if isinstance(item.get(key), str) else ""
        for key in HASH_FIELDS
    )
    return hashlib.sha256(
        basis.encode("utf-8", errors="surrogatepass")
    ).hexdigest()


def with_editorial_author(item):
    """Correct a verified attribution, without changing dates or other fields."""
    if not isinstance(item, dict):
        return item
    author = author_for(item.get("url"), item.get("author"))
    if author == item.get("author"):
        return item
    corrected = dict(item, author=author)
    # Do not erase a pre-existing signal of an unrelated manual edit.
    if item.get("_hash") is None or item["_hash"] == article_hash(item):
        corrected["_hash"] = article_hash(corrected)
    return corrected


def with_editorial_authors(data):
    """Preserve all records, order and scan metadata; apply explicit overrides."""
    normalized = normalize(data, "cache", allow_empty=True)
    result = copy.deepcopy(normalized)
    result["items"] = {
        key: with_editorial_author(item) for key, item in result["items"].items()
    }
    return result


_thread_lock = threading.RLock()
_lock_state = threading.local()


def atomic_write(path, content):
    """Replace one complete file; readers see either old or new bytes."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".artbooms-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def atomic_json(path, data):
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write(path, content.encode("utf-8", errors="backslashreplace"))


def cache_entries(data):
    """Consumer view only: never remove rejected records from stored JSON."""
    data = with_editorial_authors(data)
    result = {
        key: item for key, item in data["items"].items()
        if valid_article(item) and key == item["url"]
    }
    if not result:
        raise ValueError("La cache non contiene articoli utilizzabili")
    return result


def _file_lock(handle, blocking):
    if os.name == "nt":
        import msvcrt

        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()

        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if not blocking or exc.errno not in (11, 13):
                    raise
                time.sleep(0.05)
                handle.seek(0)
    else:
        import fcntl

        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(handle.fileno(), flags)


def _file_unlock(handle):
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def cache_transaction(path, blocking=True):
    if not _thread_lock.acquire(blocking=blocking):
        yield False
        return

    canonical = os.path.normcase(os.path.abspath(path))
    depth = getattr(_lock_state, "depth", 0)
    handle = None
    entered = False

    try:
        if depth and getattr(_lock_state, "path", None) != canonical:
            raise RuntimeError("Transazioni annidate su cache diverse")

        if not depth:
            os.makedirs(os.path.dirname(canonical), exist_ok=True)
            handle = open(canonical + ".lock", "a+b")
            try:
                _file_lock(handle, blocking)
            except OSError as exc:
                if blocking or exc.errno not in (11, 13):
                    raise
                handle.close()
                handle = None
                yield False
                return

        _lock_state.path = canonical
        _lock_state.depth = depth + 1
        entered = True
        yield True
    finally:
        if entered:
            _lock_state.depth = depth

        if handle is not None:
            try:
                if entered:
                    _file_unlock(handle)
            finally:
                handle.close()

        _thread_lock.release()


def normalize(data, label, allow_empty=False):
    if not isinstance(data, dict):
        raise ValueError(label + ": radice JSON non valida")

    items = data.get("items")
    if isinstance(items, list):
        data = copy.deepcopy(data)
        data["items"] = {
            item["url"]: item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("url"), str)
        }
        items = data["items"]

    if not isinstance(items, dict) or (not items and not allow_empty):
        raise ValueError(label + ": items vuoto o non valido")

    return data


def merge_cache(current, candidate, allow_empty_current=False):
    current = normalize(
        current, "cache esistente", allow_empty_current
    )
    candidate = normalize(candidate, "cache candidata")

    merged = with_editorial_authors(current)
    output = merged["items"]
    trusted = set(current["items"]).issubset(candidate["items"])
    stats = dict(
        valid=0, added=0, updated=0,
        invalid=0, stale=0, conflict=0,
    )

    for key, raw in candidate["items"].items():
        raw = with_editorial_author(raw)
        if not valid_article(raw) or key != raw["url"]:
            stats["invalid"] += 1
            # A known defective row remains stored, but must not freeze an
            # otherwise valid scan. New or changed invalid data is untrusted.
            if key not in output or raw != output[key]:
                trusted = False
            continue

        stats["valid"] += 1
        old = output.get(key)

        # Un record difettoso può essere riparato anche con una copia storica.
        if not valid_article(old):
            incoming = preserve_optional(raw)
            incoming["_hash"] = article_hash(incoming)
            output[key] = incoming
            category = "added" if key not in current["items"] else "updated"
            stats[category] += 1
            continue

        incoming = copy.deepcopy(old)
        incoming.update(preserve_optional(raw, old))

        before = parsed_time(old.get("_fetched_at"))
        after = parsed_time(raw.get("_fetched_at"))

        if before is not None and (after is None or after < before):
            stats["stale"] += 1
            trusted = False
            continue

        same = all(
            (incoming.get(key) or "") == (old.get(key) or "")
            for key in ("url",) + HASH_FIELDS
        )
        if same:
            continue

        old_digest = article_hash(old)
        if (
            before is None
            or after is None
            or after <= before
            or (
                old.get("_hash") is not None
                and old["_hash"] != old_digest
            )
        ):
            stats["conflict"] += 1
            trusted = False
            continue

        incoming["_hash"] = article_hash(incoming)
        output[key] = incoming
        stats["updated"] += 1

    if not stats["valid"]:
        raise ValueError("La candidata non contiene articoli utilizzabili")

    cursor = candidate.get("cursor")
    links_hash = candidate.get("links_hash")
    scanned = parsed_time(candidate.get("last_scan"))
    previous = parsed_time(current.get("last_scan"))

    if (
        trusted
        and type(cursor) is int
        and cursor >= 0
        and isinstance(links_hash, str)
        and links_hash.strip()
        and scanned is not None
        and (previous is None or scanned >= previous)
        and (
            links_hash == current.get("links_hash")
            or previous is None
            or scanned > previous
        )
    ):
        # I tre metadati appartengono alla stessa scansione.
        # Il cursore può legittimamente tornare a zero.
        for key in ("cursor", "links_hash", "last_scan"):
            merged[key] = candidate[key]

    stats.update(
        current=len(current["items"]),
        candidate=len(candidate["items"]),
        candidate_valid=stats["valid"],
        candidate_rejected=stats["invalid"],
        merged=len(output),
    )
    return merged, stats
