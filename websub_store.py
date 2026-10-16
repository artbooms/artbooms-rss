"""Conditional, synchronous WebSub state storage on a dedicated GitHub branch.

This module never creates a branch while loading, never writes the source
branch, and never logs credentials or response bodies. Network failures are
closed for publishing; callers keep RSS serving independently.
"""
import base64
import binascii
import copy
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

MAX_DOCUMENT_BYTES = 900 * 1024
API_ROOT = "https://api.github.com"


class StoreError(RuntimeError):
    def __init__(self, message, retry_after=None, status_code=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


class Conflict(StoreError):
    """The authoritative revision moved; reload before another transition."""


class Unavailable(StoreError):
    """No authoritative state or write acknowledgement could be established."""


@dataclass(frozen=True)
class Snapshot:
    head: str
    tree: str
    document: dict


def _canonical(document):
    if not isinstance(document, dict):
        raise Unavailable("WebSub state must be a JSON object")
    try:
        content = json.dumps(document, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise Unavailable("WebSub state exceeds the safe storage limit")
        return content
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise Unavailable("WebSub state is not valid JSON") from None


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", value):
        raise Unavailable("GitHub returned an invalid object identifier")
    return value


def _object_sha(result, field):
    value = result.get(field)
    if not isinstance(value, dict):
        raise Unavailable("GitHub returned invalid object metadata")
    return _sha(value.get("sha"))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate state key")
        result[key] = value
    return result


def _retry_after(headers):
    if not headers:
        return None
    # Real response headers are case-insensitive; retain that behavior for
    # injected transports too. Never retry before either documented deadline.
    normalized = {str(key).lower(): value for key, value in headers.items()}
    now = datetime.now(timezone.utc).timestamp()
    delays = []
    value = normalized.get("retry-after")
    if isinstance(value, str):
        try:
            if value.strip().isdigit():
                seconds = float(value)
            else:
                deadline = parsedate_to_datetime(value)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                seconds = deadline.timestamp() - now
            if math.isfinite(seconds):
                delays.append(max(0, seconds))
        except (TypeError, ValueError, OverflowError):
            pass
    if str(normalized.get("x-ratelimit-remaining", "")).strip() == "0":
        try:
            seconds = float(normalized["x-ratelimit-reset"]) - now
            if math.isfinite(seconds):
                delays.append(max(0, seconds))
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    return max(delays) if delays else None


class GitHubStore:
    def __init__(self, repo, branch, path, token, session=None, timeout=(3, 10)):
        parts = repo.split("/") if isinstance(repo, str) else []
        if (len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
                                   for part in parts)
                or len(parts[0]) > 39 or len(parts[1]) > 100):
            raise Unavailable("Invalid WebSub state repository")
        if (not isinstance(branch, str) or len(branch) > 200
                or not (branch == "websub-state" or branch.startswith(("websub-state-", "websub-state/")))
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", branch)
                or ".." in branch or "//" in branch
                or any(not part or part.startswith(".") or part.endswith((".lock", "."))
                       for part in branch.split("/"))):
            raise Unavailable("WebSub requires a dedicated websub-state branch")
        segments = path.split("/") if isinstance(path, str) else []
        if (not segments or len(path) > 240 or not path.endswith(".json")
                or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part)
                       or part in (".", "..") or ".." in part
                       or part.lower() in ("cache", "articles_cache.json", ".github")
                       for part in segments)):
            raise Unavailable("Invalid dedicated WebSub state file path")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", token.strip()):
            raise Unavailable("WEBSUB_GITHUB_TOKEN is required")
        if (not isinstance(timeout, (tuple, list)) or len(timeout) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       or not 0 < value <= 60 for value in timeout)):
            raise Unavailable("WebSub storage requires bounded HTTP timeouts")
        self.repo, self.branch, self.path = repo, branch, path
        self.timeout = tuple(timeout)
        self.session = session if session is not None else requests.Session()
        self._headers = {"Authorization": "Bearer " + token.strip(),
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28",
                         "User-Agent": "ArtboomsRSS-WebSub-State/1.0"}
        self._base = API_ROOT + "/repos/" + repo
        self._ref = "/git/ref/heads/" + quote(branch, safe="")
        self._write_ref = "/git/refs/heads/" + quote(branch, safe="")
        self._cached = None

    @classmethod
    def from_env(cls, environ=None, session=None):
        env = os.environ if environ is None else environ
        return cls(env.get("WEBSUB_STATE_REPOSITORY", "artbooms/artbooms-rss"),
                   env.get("WEBSUB_STATE_BRANCH", "websub-state"),
                   env.get("WEBSUB_STATE_FILE", "websub-state.json"),
                   env.get("WEBSUB_GITHUB_TOKEN", ""), session=session)

    def _request(self, method, endpoint, *, conflict=False, **kwargs):
        response = None
        try:
            response = self.session.request(method, self._base + endpoint,
                                            headers=dict(self._headers), timeout=self.timeout,
                                            allow_redirects=False, **kwargs)
            status = response.status_code
            if not 200 <= status < 300:
                error = Conflict if conflict and status in (409, 422) else Unavailable
                raise error("GitHub WebSub state request failed (HTTP %d)" % status,
                            retry_after=_retry_after(response.headers), status_code=status)
            try:
                result = response.json()
            except (ValueError, TypeError):
                raise Unavailable("GitHub returned invalid JSON") from None
            if not isinstance(result, dict):
                raise Unavailable("GitHub returned an invalid state response")
            return result
        except requests.RequestException:
            # Request exceptions can contain the Authorization header or URL.
            raise Unavailable("GitHub WebSub state request did not complete") from None
        finally:
            if response is not None:
                response.close()

    def _head(self):
        result = self._request("GET", self._ref)
        return _object_sha(result, "object")

    def load(self):
        head = self._head()
        if self._cached is not None and self._cached.head == head:
            return copy.deepcopy(self._cached)
        commit = self._request("GET", "/git/commits/" + head)
        tree = _object_sha(commit, "tree")
        result = self._request("GET", "/contents/" + quote(self.path, safe="/"), params={"ref": head})
        content = result.get("content")
        if result.get("encoding") != "base64" or not isinstance(content, str):
            raise Unavailable("GitHub state file is not a base64 JSON file")
        # GitHub wraps base64 lines. Bound allocation without rejecting a valid
        # near-limit document because of the transport's line breaks.
        if len(content) > MAX_DOCUMENT_BYTES * 2:
            raise Unavailable("GitHub WebSub state exceeds the safe storage limit")
        try:
            decoded = base64.b64decode("".join(content.split()), validate=True)
            if len(decoded) > MAX_DOCUMENT_BYTES:
                raise Unavailable("GitHub WebSub state exceeds the safe storage limit")
            document = json.loads(decoded.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, binascii.Error, RecursionError):
            raise Unavailable("GitHub WebSub state file is invalid") from None
        _canonical(document)
        self._cached = Snapshot(head, tree, document)
        return copy.deepcopy(self._cached)

    def _create_commit(self, document, parent=None, base_tree=None):
        content = _canonical(document)
        tree_payload = {"tree": [{"path": self.path, "mode": "100644", "type": "blob",
                                   "content": content}]}
        if base_tree is not None:
            tree_payload["base_tree"] = base_tree
        tree = _sha(self._request("POST", "/git/trees", json=tree_payload).get("sha"))
        commit = self._request("POST", "/git/commits", json={
            "message": "Update WebSub state [skip render]", "tree": tree,
            "parents": [] if parent is None else [parent]})
        return Snapshot(_sha(commit.get("sha")), tree, json.loads(content))

    def _confirm_commit(self, expected):
        """A bounded ancestry check resolves a lost ref-update response."""
        self._cached = None
        current = self.load()
        remaining, visited = [current.head], set()
        while remaining and len(visited) < 8:
            head = remaining.pop(0)
            if head == expected:
                return current
            if head in visited:
                continue
            visited.add(head)
            commit = self._request("GET", "/git/commits/" + head)
            parents = commit.get("parents")
            if not isinstance(parents, list):
                raise Unavailable("GitHub returned invalid commit ancestry")
            remaining.extend(_sha(parent.get("sha")) for parent in parents if isinstance(parent, dict))
        # The last inspected commit can itself point to the expected ancestor.
        if expected in remaining:
            return current
        raise Unavailable("GitHub WebSub state write acknowledgement is uncertain")

    def commit(self, snapshot, document):
        if not isinstance(snapshot, Snapshot):
            raise Unavailable("A verified WebSub state snapshot is required")
        content = _canonical(document)
        original = (self._cached.document if self._cached is not None
                    and self._cached.head == snapshot.head else snapshot.document)
        if content == _canonical(original):
            return copy.deepcopy(snapshot)
        candidate = self._create_commit(json.loads(content), _sha(snapshot.head), _sha(snapshot.tree))
        try:
            self._request("PATCH", self._write_ref, conflict=True,
                          json={"sha": candidate.head, "force": False})
        except Conflict:
            self._cached = None
            raise
        except Unavailable as exc:
            if exc.status_code is None or exc.status_code >= 500:
                try:
                    return self._confirm_commit(candidate.head)
                except StoreError:
                    raise Unavailable("GitHub WebSub state write acknowledgement is uncertain",
                                      retry_after=exc.retry_after) from None
            raise
        self._cached = candidate
        return copy.deepcopy(candidate)

    def _matching_initialization(self, document):
        self._cached = None
        current = self.load()
        if (current.document.get("migration_id") == document["migration_id"]
                and _canonical(current.document) == _canonical(document)):
            return current
        raise Conflict("The dedicated WebSub state branch already exists")

    def initialize(self, document):
        """Explicit migration-only operation; create a root branch, never replace."""
        document = json.loads(_canonical(document))
        if not isinstance(document.get("migration_id"), str) or not document["migration_id"].strip():
            raise Unavailable("WebSub initialization requires a migration identifier")
        try:
            self._head()
        except Unavailable as exc:
            if exc.status_code != 404:
                raise
        else:
            return self._matching_initialization(document)
        candidate = self._create_commit(document)
        try:
            self._request("POST", "/git/refs", conflict=True,
                          json={"ref": "refs/heads/" + self.branch, "sha": candidate.head})
        except Conflict:
            return self._matching_initialization(document)
        except Unavailable as exc:
            if exc.status_code is None or exc.status_code >= 500:
                try:
                    return self._matching_initialization(document)
                except StoreError:
                    raise Unavailable("GitHub WebSub initialization acknowledgement is uncertain",
                                      retry_after=exc.retry_after) from None
            raise
        self._cached = candidate
        return copy.deepcopy(candidate)
