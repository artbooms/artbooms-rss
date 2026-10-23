"""Offline Git Data transport, CAS and ambiguous-write reliability checks."""
import base64
import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import io
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPO = "fixture-owner/fixture-repository"
BRANCH = "websub-state"
STATE_PATH = "websub/state.json"
TOKEN = "offline-test-token-do-not-log"
TOPIC = "https://rss.artbooms.com/rss"


def initial_document():
    return {"format": 1, "topic": TOPIC, "mode": "active", "migration_id": "offline-migration-1", "revision": 0,
            "state": {"version": 2, "observed_guids": ["https://www.artbooms.com/blog/old"],
                      "pending": []}, "completed": {}}


class FakeResponse:
    def __init__(self, status=200, document=None, headers=None):
        self.status_code = status
        self._document = copy.deepcopy(document if document is not None else {})
        self.headers = headers or {}
        self.content = json.dumps(self._document).encode()
        self.text = self.content.decode()

    def json(self):
        return copy.deepcopy(self._document)

    def close(self):
        pass


class FakeGitHubSession:
    """Thread-safe fake for Git Data; all changes require a fast-forward ref CAS.

    A fault can happen before a request or after its mutation was applied,
    reproducing a lost response without issuing any real network request.
    """
    def __init__(self, document=None, branch=BRANCH, path=STATE_PATH):
        self.lock = threading.RLock()
        self.refs, self.commits, self.trees = {}, {}, {}
        self.calls, self.faults = [], []
        self._sequence = 0
        self.seed("main", {"application.py": "production source must stay unchanged"})
        if document is not None:
            self.seed(branch, {path: self.canonical(document)})

    @staticmethod
    def canonical(document):
        return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def identifier(self, kind, value):
        self._sequence += 1
        return hashlib.sha1((kind + str(self._sequence) + self.canonical(value)).encode()).hexdigest()

    def seed(self, branch, files):
        with self.lock:
            tree = self.identifier("tree", files)
            self.trees[tree] = copy.deepcopy(files)
            head = self.identifier("commit", {"tree": tree, "branch": branch})
            self.commits[head] = {"sha": head, "tree": {"sha": tree}, "parents": []}
            self.refs[branch] = head
            return head

    def queue_fault(self, method, suffix, *, status=None, exception=None,
                    after_apply=False, headers=None, body=None):
        self.faults.append(dict(method=method, suffix=suffix, status=status, exception=exception,
                                after_apply=after_apply, headers=headers, body=body))

    @property
    def writes(self):
        return [call for call in self.calls if call[0] not in ("GET", "HEAD")]

    def document(self, branch=BRANCH, path=STATE_PATH):
        with self.lock:
            head = self.refs[branch]
            return json.loads(self.trees[self.commits[head]["tree"]["sha"]][path])

    def request(self, method, url, **kwargs):
        with self.lock:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise AssertionError("Unexpected external request")
            prefix = "/repos/" + REPO
            if not parsed.path.startswith(prefix + "/"):
                raise AssertionError("Unexpected repository request")
            route = unquote(parsed.path[len(prefix):])
            method = method.upper()
            self.calls.append((method, route, copy.deepcopy(kwargs.get("json")),
                               copy.deepcopy(kwargs.get("params"))))
            fault = next((fault for fault in self.faults
                          if fault["method"] == method and route.endswith(fault["suffix"])), None)
            if fault:
                self.faults.remove(fault)
                if fault["after_apply"]:
                    self.apply(method, route, kwargs)
                if fault["exception"]:
                    raise fault["exception"]
                return FakeResponse(fault["status"], fault["body"], fault["headers"])
            return self.apply(method, route, kwargs)

    def apply(self, method, route, kwargs):
        payload = kwargs.get("json") or {}
        if method == "GET" and route.startswith("/git/ref/heads/"):
            branch = route[len("/git/ref/heads/"):]
            head = self.refs.get(branch)
            return FakeResponse(200, {"object": {"sha": head}}) if head else FakeResponse(404)
        if method == "GET" and route.startswith("/git/commits/"):
            commit = self.commits.get(route[len("/git/commits/"):])
            return FakeResponse(200, commit) if commit else FakeResponse(404)
        if method == "GET" and route.startswith("/contents/"):
            path = route[len("/contents/"):]
            ref = (kwargs.get("params") or {}).get("ref")
            head = self.refs.get(ref, ref)
            commit = self.commits.get(head)
            content = self.trees[commit["tree"]["sha"]].get(path) if commit else None
            if content is None:
                return FakeResponse(404)
            return FakeResponse(200, {"type": "file", "encoding": "base64",
                                      "content": base64.b64encode(content.encode()).decode()})
        if method == "POST" and route == "/git/trees":
            files = copy.deepcopy(self.trees.get(payload.get("base_tree"), {}))
            for entry in payload["tree"]:
                files[entry["path"]] = entry["content"]
            tree = self.identifier("tree", files)
            self.trees[tree] = files
            return FakeResponse(201, {"sha": tree})
        if method == "POST" and route == "/git/commits":
            head = self.identifier("commit", payload)
            self.commits[head] = {"sha": head, "tree": {"sha": payload["tree"]},
                                  "parents": [{"sha": parent} for parent in payload["parents"]]}
            return FakeResponse(201, {"sha": head})
        if method == "POST" and route == "/git/refs":
            branch = payload["ref"].removeprefix("refs/heads/")
            if branch in self.refs:
                return FakeResponse(422)
            self.refs[branch] = payload["sha"]
            return FakeResponse(201, {"object": {"sha": payload["sha"]}})
        if method == "PATCH" and route.startswith("/git/refs/heads/"):
            branch = route[len("/git/refs/heads/"):]
            if payload.get("force") is not False:
                raise AssertionError("A forced ref update is forbidden")
            if branch not in self.refs:
                return FakeResponse(404)
            parents = self.commits[payload["sha"]]["parents"]
            if self.refs[branch] != payload["sha"] and {"sha": self.refs[branch]} not in parents:
                return FakeResponse(422)
            self.refs[branch] = payload["sha"]
            return FakeResponse(200, {"object": {"sha": payload["sha"]}})
        raise AssertionError("Unconfigured Git Data request: %s %s" % (method, route))

    def close(self):
        pass


class GitHubStoreTests(unittest.TestCase):
    def setUp(self):
        import websub_store
        self.api = websub_store
        self.document = initial_document()
        self.fake = FakeGitHubSession(self.document)
        self.store = self.make_store()
        self.main_head = self.fake.refs["main"]
        self.guard = patch.object(socket.socket, "connect", side_effect=AssertionError("Real network forbidden"))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.addCleanup(lambda: self.assertEqual(self.fake.refs["main"], self.main_head))

    def make_store(self, fake=None):
        return self.api.GitHubStore(REPO, BRANCH, STATE_PATH, TOKEN, session=fake or self.fake, timeout=(1, 2))

    def test_initialize_is_explicit_and_idempotent_without_modifying_source_branch(self):
        fake = FakeGitHubSession()
        main_head = fake.refs["main"]
        store = self.make_store(fake)
        with self.assertRaises(self.api.Unavailable):
            store.load()
        self.assertEqual(fake.writes, [])
        snapshot = store.initialize(self.document)
        self.assertEqual(snapshot.document, self.document)
        self.assertEqual(fake.document(), self.document)
        self.assertEqual(fake.refs["main"], main_head)
        writes = len(fake.writes)
        self.assertEqual(store.initialize(copy.deepcopy(self.document)).document, self.document)
        self.assertEqual(len(fake.writes), writes)
        changed = copy.deepcopy(self.document)
        changed["migration_id"] = "different-bootstrap"
        with self.assertRaises(self.api.Conflict):
            store.initialize(changed)
        self.assertEqual(fake.document(), self.document)
        self.assertEqual(fake.refs["main"], main_head)

    def test_state_branch_restriction_prevents_writing_code_branches(self):
        for branch in ("main", "master", "codex/experiment", "production"):
            with self.subTest(branch=branch), self.assertRaises((ValueError, self.api.StoreError)):
                self.api.GitHubStore(REPO, branch, STATE_PATH, TOKEN, session=self.fake)
        self.assertEqual(self.fake.writes, [])

    def test_repeated_load_uses_head_cache_and_returns_independent_document(self):
        snapshot = self.store.load()
        snapshot.document["mode"] = "paused"
        before = len(self.fake.calls)
        again = self.store.load()
        self.assertEqual(again.document, self.document)
        self.assertEqual(len(self.fake.calls), before + 1)
        self.assertEqual(self.fake.calls[-1][0:2], ("GET", "/git/ref/heads/" + BRANCH))

    def test_noop_document_commit_issues_no_git_writes(self):
        snapshot = self.store.load()
        self.assertEqual(self.store.commit(snapshot, copy.deepcopy(snapshot.document)).document, self.document)
        self.assertEqual(self.fake.writes, [])

    def test_commit_preserves_unrelated_files_and_marks_state_commits_skip_render(self):
        self.fake.seed(BRANCH, {STATE_PATH: self.fake.canonical(self.document), "notes.txt": "keep this file"})
        snapshot = self.store.load()
        changed = copy.deepcopy(snapshot.document)
        changed["mode"] = "paused"
        result = self.store.commit(snapshot, changed)
        self.assertEqual(self.fake.trees[result.tree]["notes.txt"], "keep this file")
        commit_requests = [call[2] for call in self.fake.writes if call[1] == "/git/commits"]
        self.assertEqual(len(commit_requests), 1)
        self.assertIn("[skip render]", commit_requests[0]["message"])
        self.assertEqual(commit_requests[0]["parents"], [snapshot.head])

    def test_two_clients_compare_and_swap_has_one_winner_without_overwrite(self):
        stores = [self.make_store(), self.make_store()]
        snapshots = [store.load() for store in stores]
        barrier = threading.Barrier(2)
        results, errors = [], []
        def commit(index):
            document = copy.deepcopy(self.document)
            document["completed"]["winner"] = str(index)
            barrier.wait(timeout=10)
            try:
                results.append(stores[index].commit(snapshots[index], document))
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=commit, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], self.api.Conflict)
        self.assertEqual(self.make_store().load().document, results[0].document)
        self.assertEqual(len([call for call in self.fake.writes if call[0] == "PATCH"]), 2)

    def test_unavailable_reads_never_initialize_or_mutate_branch(self):
        for outcome in (401, 404, 429, 503, requests.Timeout("Offline read timeout")):
            with self.subTest(outcome=type(outcome).__name__ if isinstance(outcome, Exception) else outcome):
                # A transport outage must outlast the single safe GET retry.
                for _ in range(2 if isinstance(outcome, Exception) else 1):
                    self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH,
                                          exception=outcome if isinstance(outcome, Exception) else None,
                                          status=outcome if isinstance(outcome, int) else None,
                                          headers={"Retry-After": "120"})
                with self.assertRaises(self.api.Unavailable):
                    self.make_store().load()
                self.assertEqual(self.fake.writes, [])
                self.assertEqual(self.make_store().load().document, self.document)

    def test_unavailable_tree_write_keeps_previous_state_and_does_not_publish_ref(self):
        snapshot = self.store.load()
        changed = copy.deepcopy(snapshot.document)
        changed["mode"] = "paused"
        self.fake.queue_fault("POST", "/git/trees", status=503)
        with self.assertRaises(self.api.Unavailable):
            self.store.commit(snapshot, changed)
        self.assertEqual(self.fake.document(), self.document)
        self.assertEqual([call[0:2] for call in self.fake.writes], [("POST", "/git/trees")])

    def test_lost_ref_response_before_apply_cannot_ack_or_overwrite_state(self):
        snapshot = self.store.load()
        changed = copy.deepcopy(snapshot.document)
        changed["mode"] = "paused"
        self.fake.queue_fault("PATCH", "/git/refs/heads/" + BRANCH,
                              exception=requests.Timeout("Lost before ref apply"))
        with self.assertRaises(self.api.Unavailable):
            self.store.commit(snapshot, changed)
        self.assertEqual(self.make_store().load().document, self.document)
        self.assertEqual(len([call for call in self.fake.writes if call[0] == "PATCH"]), 1)

    def test_lost_ref_response_after_apply_recovers_without_duplicate_write(self):
        snapshot = self.store.load()
        changed = copy.deepcopy(snapshot.document)
        changed["mode"] = "paused"
        self.fake.queue_fault("PATCH", "/git/refs/heads/" + BRANCH, after_apply=True,
                              exception=requests.Timeout("Lost response after ref apply"))
        recovered = self.store.commit(snapshot, changed)
        self.assertEqual(recovered.document, changed)
        self.assertEqual(self.make_store().load().document, changed)
        self.assertEqual(len([call for call in self.fake.writes if call[0] == "PATCH"]), 1)
        self.assertEqual(self.store.commit(recovered, changed).document, changed)
        self.assertEqual(len([call for call in self.fake.writes if call[0] == "PATCH"]), 1)

    def test_lost_ref_response_as_503_after_apply_is_resolved_from_current_head(self):
        snapshot = self.store.load()
        changed = copy.deepcopy(snapshot.document)
        changed["mode"] = "paused"
        self.fake.queue_fault("PATCH", "/git/refs/heads/" + BRANCH, status=503, after_apply=True)
        self.assertEqual(self.store.commit(snapshot, changed).document, changed)
        self.assertEqual(self.make_store().load().document, changed)

    def test_missing_or_malformed_contents_fail_closed_without_baseline(self):
        malformed = ({"type": "file", "encoding": "base64", "content": "!!!"},
                     {"type": "file", "encoding": "base64", "content": base64.b64encode(b"{broken").decode()},
                     {"type": "file", "encoding": "base64", "content": base64.b64encode(b"[]").decode()},
                     {"type": "file", "encoding": "base64", "content": base64.b64encode(b'{"x":1,"x":2}').decode()})
        for body in malformed:
            with self.subTest(body=body):
                self.fake.queue_fault("GET", "/contents/" + STATE_PATH, status=200, body=body)
                with self.assertRaises(self.api.Unavailable):
                    self.make_store().load()
                self.assertEqual(self.fake.writes, [])
        self.fake.queue_fault("GET", "/contents/" + STATE_PATH, status=404)
        with self.assertRaises(self.api.Unavailable):
            self.make_store().load()
        self.assertEqual(self.fake.writes, [])

    def test_malformed_ref_or_commit_metadata_fail_closed_without_git_writes(self):
        for suffix, body in (("/git/ref/heads/" + BRANCH, {"object": []}),
                             ("/git/ref/heads/" + BRANCH, {"object": {}}),
                             ("/git/commits/" + self.fake.refs[BRANCH], {"tree": []}),
                             ("/git/commits/" + self.fake.refs[BRANCH], {"tree": {}})):
            with self.subTest(suffix=suffix, body=body):
                self.fake.queue_fault("GET", suffix, status=200, body=body)
                with self.assertRaises(self.api.Unavailable):
                    self.make_store().load()
                self.assertEqual(self.fake.writes, [])

    def test_token_is_absent_from_diagnostics_even_when_error_body_echoes_it(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH, status=401,
                              body={"message": "Authentication rejected for " + TOKEN})
        with self.assertRaises(self.api.Unavailable) as result:
            self.store.load()
        self.assertNotIn(TOKEN, str(result.exception))
        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertEqual(self.fake.writes, [])


class GitHubArchiveIntegrationTests(unittest.TestCase):
    def test_real_archive_parser_acquisition_remote_pending_and_ack_survive_ephemeral_deploy(self):
        import test_websub_simulation as simulation
        import websub_durable
        import websub_store
        # Reuse the archive rehearsal helpers without inheriting or re-running
        # its five local-file scenarios as additional test cases.
        case = simulation.WebSubArchiveSimulationTests("test_full_archive_one_acquisition_ack_and_deploy_before_and_after")
        self.addCleanup(case.doCleanups)
        case.setUp()
        document = initial_document()
        document["state"] = case.state()
        fake = FakeGitHubSession(document)
        case.state = lambda: fake.document()["state"]
        case.stack.enter_context(patch.dict(os.environ, {"WEBSUB_BACKEND": "github"}))
        for name, value in (("_default_store", None), ("_default_config", None), ("_retry_not_before", 0)):
            case.stack.enter_context(patch.object(websub_durable, name, value))
        case.stack.enter_context(patch.object(websub_store.GitHubStore, "from_env", side_effect=lambda:
            websub_store.GitHubStore(REPO, BRANCH, STATE_PATH, TOKEN, session=fake)))
        case.acquire(real_source=True, pending_state=False)
        self.assertEqual(fake.calls, [])  # Publication cannot perform GitHub I/O.
        cache_bytes = case.cache_path.read_bytes()
        case.public_content = case.baseline_feed
        case.notify()
        case.hub.assert_not_called()
        pending = copy.deepcopy(case.state()["pending"][0])
        self.assertEqual(pending["guids"], [case.guid])
        self.assertEqual((pending["feed_checks"], pending["hub_attempts"]), (1, 0))
        case.state_path.unlink()  # No authoritative notification file survives.
        case.deploy()
        self.assertEqual(case.state()["pending"][0], pending)
        case.assert_quiet()
        case.now = pending["next_attempt_at"]
        case.public_content = None
        case.notify()
        case.hub.assert_called_once()
        self.assertEqual(case.state()["pending"], [])
        self.assertEqual(set(case.state()["observed_guids"]), set(case.archive["items"]) | {case.guid})
        completed = next(iter(fake.document()["completed"].values()))
        self.assertEqual((completed["status"], completed["guids"]), ("accepted", [case.guid]))
        case.deploy()
        case.assert_quiet()
        case.hub.assert_called_once()
        self.assertFalse(case.state_path.exists())
        self.assertEqual(case.cache_path.read_bytes(), cache_bytes)
        case.assert_editorial_outputs()
        case.socket_guard.assert_not_called()


class DurablePublisherTests(unittest.TestCase):
    def setUp(self):
        import websub_durable
        import websub_store
        from test_websub import OLD, NEW, feed
        self.publisher, self.api = websub_durable, websub_store
        self.old, self.new, self.feed = OLD, NEW, feed
        self.document = initial_document()
        self.fake = FakeGitHubSession(self.document)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "WEBSUB_BACKEND": "github", "CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}))
        self.now = 1000.0
        self.stack.enter_context(patch("time.time", side_effect=lambda: self.now))
        self.network = self.stack.enter_context(patch.object(
            requests.Session, "request", side_effect=AssertionError("Real GitHub HTTP forbidden")))
        self.socket = self.stack.enter_context(patch.object(
            socket.socket, "connect", side_effect=AssertionError("Real public sockets forbidden")))
        self.get = self.stack.enter_context(patch.object(requests, "get", return_value=Mock(
            status_code=200, content=self.feed(self.old, self.new), headers={})))
        self.post = self.stack.enter_context(patch.object(requests, "post", return_value=Mock(status_code=204, headers={})))
        disabled = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, disabled)
        self.module_names = []
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in self.module_names])

    def store(self):
        return self.api.GitHubStore(REPO, BRANCH, STATE_PATH, TOKEN, session=self.fake)

    def publish(self):
        return self.publisher.run(self.feed(self.old, self.new), store=self.store(), now=self.now)

    def event(self):
        return self.fake.document()["state"]["pending"][0]

    def test_canonical_feed_reconciles_one_new_guid_and_persists_ack_across_fresh_clients(self):
        def accepted(*args, **kwargs):
            event = self.event()
            self.assertEqual(event["hub_attempts"], 1)
            self.assertEqual(event["feed_checks"], 1)
            self.assertGreater(event["lease_until"], self.now)
            return Mock(status_code=204, headers={})
        self.post.side_effect = accepted
        self.assertTrue(self.publish())
        document = self.fake.document()
        self.assertEqual(set(document["state"]["observed_guids"]), {self.old, self.new})
        self.assertEqual(document["state"]["pending"], [])
        self.assertEqual(len(document["completed"]), 1)
        completed = next(iter(document["completed"].values()))
        self.assertEqual(completed["status"], "accepted")
        self.assertEqual(completed["guids"], [self.new])
        writes = len(self.fake.writes)
        for _ in range(3):
            self.assertFalse(self.publish())
        self.assertEqual(len(self.fake.writes), writes)
        self.post.assert_called_once()
        self.get.assert_called_once()
        self.network.assert_not_called()
        self.socket.assert_not_called()

    def test_separate_clients_preserve_feed_budget_and_wait_for_public_new_guid(self):
        self.get.return_value = Mock(status_code=200, content=self.feed(self.old), headers={})
        self.assertFalse(self.publish())
        initial = self.event().copy()
        self.assertEqual((initial["feed_checks"], initial["hub_attempts"]), (1, 0))
        self.now += 8 * 3600
        self.assertFalse(self.publish())
        self.assertEqual((self.event()["feed_checks"], self.event()["hub_attempts"]), (2, 0))
        self.assertEqual(self.event()["expires_at"], initial["expires_at"])
        self.post.assert_not_called()
        self.now = self.event()["next_attempt_at"]
        self.get.return_value = Mock(status_code=200, content=self.feed(self.old, self.new), headers={})
        self.assertTrue(self.publish())
        self.post.assert_called_once()
        self.assertEqual(self.fake.document()["state"]["pending"], [])

    def test_hub_timeout_503_429_retries_keep_durable_budget_and_fixed_deadline(self):
        deadline = None
        for count, outcome in enumerate((requests.Timeout("Offline hub timeout"), 503, 429), start=1):
            self.post.side_effect = outcome if isinstance(outcome, Exception) else None
            self.post.return_value = Mock(status_code=outcome if isinstance(outcome, int) else 204,
                                          headers={"Retry-After": "7200"} if outcome == 429 else {})
            self.assertFalse(self.publish())
            event = self.event().copy()
            deadline = event["expires_at"] if deadline is None else deadline
            self.assertEqual(event["expires_at"], deadline)
            self.assertEqual((event["hub_attempts"], event["feed_checks"]), (count, count))
            calls = self.get.call_count, self.post.call_count
            self.assertFalse(self.publish())
            self.assertEqual((self.get.call_count, self.post.call_count), calls)
            if outcome == 429:
                self.assertGreaterEqual(event["next_attempt_at"] - self.now, 7200)
            self.now = event["next_attempt_at"]
        self.post.side_effect = None
        self.post.return_value = Mock(status_code=204, headers={})
        self.assertTrue(self.publish())
        self.assertEqual(self.post.call_count, 4)
        self.assertFalse(self.publish())

    def test_two_instances_share_one_live_lease_and_issue_only_one_post(self):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        def public_get(*args, **kwargs):
            self.assertGreater(self.event()["lease_until"], self.now)
            entered.set()
            self.assertTrue(release.wait(timeout=15), "The test must release the simulated public GET")
            return Mock(status_code=200, content=self.feed(self.old, self.new), headers={})
        def first_instance():
            try:
                results.append(self.publish())
            except BaseException as error:
                errors.append(error)
        self.get.side_effect = public_get
        worker = threading.Thread(target=first_instance)
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=10))
            self.assertFalse(self.publish())
            self.get.assert_called_once()
            self.post.assert_not_called()
        finally:
            release.set()
            worker.join(timeout=20)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [True])
        self.post.assert_called_once()
        self.assertEqual(self.fake.document()["state"]["pending"], [])

    def test_changed_lease_owner_after_get_fences_original_publisher_before_post(self):
        def steal_lease(*args, **kwargs):
            store = self.store()
            snapshot = store.load()
            changed = copy.deepcopy(snapshot.document)
            changed["state"]["pending"][0]["lease_token"] = "another-live-owner"
            changed["state"]["pending"][0]["lease_until"] = self.now + 180
            changed["revision"] += 1
            store.commit(snapshot, changed)
            return Mock(status_code=200, content=self.feed(self.old, self.new), headers={})
        self.get.side_effect = steal_lease
        self.assertFalse(self.publish())
        self.post.assert_not_called()
        self.assertEqual(self.event()["lease_token"], "another-live-owner")

    def test_github_read_or_reservation_outage_never_contacts_hub_and_restores_without_replay(self):
        self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH, status=503)
        self.assertFalse(self.publish())
        self.assertEqual(self.fake.document(), self.document)
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.fake.queue_fault("POST", "/git/trees", exception=requests.Timeout("State unavailable"))
        self.assertFalse(self.publish())
        self.assertEqual(self.fake.document(), self.document)
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.assertTrue(self.publish())
        self.assertFalse(self.publish())
        self.post.assert_called_once()
        self.assertEqual(set(self.fake.document()["state"]["observed_guids"]), {self.old, self.new})

    def test_ambiguous_ack_cas_is_recovered_or_retried_once_without_resetting_budget(self):
        for applied in (False, True):
            with self.subTest(ack_applied=applied):
                self.fake = FakeGitHubSession(self.document)
                self.get.reset_mock()
                self.post.reset_mock()
                self.now = 1000.0
                def accepted_but_lost_response(*args, **kwargs):
                    if self.post.call_count == 1:
                        self.fake.queue_fault("PATCH", "/git/refs/heads/" + BRANCH, after_apply=applied,
                                              exception=requests.Timeout("Acknowledgement response lost"))
                    return Mock(status_code=204, headers={})
                self.post.side_effect = accepted_but_lost_response
                self.publish()
                self.post.assert_called_once()
                if applied:
                    self.assertEqual(self.fake.document()["state"]["pending"], [])
                    self.assertEqual(len(self.fake.document()["completed"]), 1)
                    self.assertFalse(self.publish())
                    self.post.assert_called_once()
                else:
                    pending = self.event().copy()
                    self.assertEqual(pending["hub_attempts"], 1)
                    self.assertEqual(self.fake.document()["completed"], {})
                    self.assertFalse(self.publish())
                    self.post.assert_called_once()
                    self.now = pending["next_attempt_at"]
                    self.assertTrue(self.publish())
                    self.assertEqual(self.post.call_count, 2)
                    self.assertEqual(self.fake.document()["state"]["pending"], [])
                    completed = next(iter(self.fake.document()["completed"].values()))
                    self.assertEqual(completed["hub_attempts"], 2)
                    self.assertEqual(completed["expires_at"], pending["expires_at"])
                    self.assertFalse(self.publish())

    def test_invalid_envelopes_fail_closed_without_history_baseline_or_notification(self):
        for field, value in (("format", 99), ("topic", "https://example.invalid/rss"),
                             ("state", {"version": 2, "observed_guids": "invalid", "pending": []}),
                             ("completed", []), ("mode", "unknown")):
            with self.subTest(field=field):
                malformed = copy.deepcopy(self.document)
                malformed[field] = value
                self.fake = FakeGitHubSession(malformed)
                self.assertFalse(self.publish())
                self.assertEqual(self.fake.document(), malformed)
                self.assertEqual(self.fake.writes, [])
                self.get.assert_not_called()
                self.post.assert_not_called()

    def test_paused_or_cancelled_remote_state_never_reconciles_or_sends(self):
        for mode, cancelled in (("paused", False), ("active", True)):
            with self.subTest(mode=mode, cancelled=cancelled):
                document = copy.deepcopy(self.document)
                document.update(mode=mode, cancelled=cancelled)
                self.fake = FakeGitHubSession(document)
                self.assertFalse(self.publish())
                self.assertEqual(self.fake.document(), document)
                self.assertEqual(self.fake.writes, [])
                self.get.assert_not_called()
                self.post.assert_not_called()

    def test_expired_durable_event_is_tombstoned_and_not_replayed_after_restart(self):
        self.post.return_value = Mock(status_code=503, headers={})
        self.assertFalse(self.publish())
        pending = self.event().copy()
        self.now = pending["expires_at"]
        self.assertFalse(self.publish())
        document = self.fake.document()
        self.assertEqual(document["state"]["pending"], [])
        self.assertIn(pending["id"], document["completed"])
        self.assertNotEqual(document["completed"][pending["id"]]["status"], "accepted")
        self.assertEqual(set(document["state"]["observed_guids"]), {self.old, self.new})
        self.assertFalse(self.publish())
        self.post.assert_called_once()

    def make_service(self, cache_bytes):
        if os.name == "nt" and "fcntl" not in sys.modules:
            shim = types.ModuleType("fcntl")
            shim.LOCK_EX, shim.LOCK_NB, shim.LOCK_UN = 2, 4, 8
            shim.flock = lambda *args, **kwargs: self.fail("The simulated service cannot start a leader")
            sys.modules["fcntl"] = shim
        import article_processor
        import news_sitemap
        # A cold process has no cached client or process-local storage backoff.
        for attribute, value in (("_default_store", None), ("_default_config", None), ("_retry_not_before", 0)):
            self.stack.enter_context(patch.object(self.publisher, attribute, value))
        runtime = tempfile.TemporaryDirectory()
        self.stack.enter_context(runtime)
        cache_path, feed_path = Path(runtime.name) / "cache.json", Path(runtime.name) / "feed.xml"
        cache_path.write_bytes(cache_bytes)
        name = "_websub_github_service_%d" % len(self.module_names)
        self.module_names.append(name)
        spec = importlib.util.spec_from_file_location(name, ROOT / "app.py")
        application = importlib.util.module_from_spec(spec)
        sys.modules[name] = application
        spec.loader.exec_module(application)
        for module, attribute, value in ((application, "CACHE_PATH", str(cache_path)),
                                         (application, "FEED_PATH", str(feed_path)),
                                         (article_processor, "CACHE_PATH", str(cache_path)),
                                         (news_sitemap, "LOCAL_CACHE_PATH", str(cache_path)),
                                         (news_sitemap, "LOCAL_FIRST", True)):
            self.stack.enter_context(patch.object(module, attribute, value))
        application.start_worker()
        return types.SimpleNamespace(application=application, client=application.app.test_client(),
                                     runtime=runtime, cache_path=cache_path, feed_path=feed_path)

    def test_rss_news_stay_200_during_github_outage_and_complete_filesystem_restore(self):
        instant = datetime.now(timezone.utc)
        old = {"url": self.old, "title": "Archive", "description": "Existing article", "author": "natasha barbieri",
               "published": (instant - timedelta(days=3)).isoformat(), "image": "https://example.org/old.jpg"}
        new = dict(old, url=self.new, title="New article", published=(instant - timedelta(hours=1)).isoformat())
        cache_bytes = json.dumps({"items": {self.old: old, self.new: new}, "cursor": 17,
                                 "last_scan": instant.isoformat(), "links_hash": "offline"}).encode()
        with patch.object(self.api.GitHubStore, "from_env", side_effect=lambda: self.store()) as factory:
            service = self.make_service(cache_bytes)
            for path in ("/rss", "/news-sitemap.xml"):
                self.assertEqual(service.client.get(path).status_code, 200)
            factory.assert_not_called()
            self.assertEqual(self.fake.calls, [])
            self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH, status=503)
            service.application._notify_websub()
            self.post.assert_not_called()
            for path in ("/rss", "/news-sitemap.xml"):
                self.assertEqual(service.client.get(path).status_code, 200)
            self.assertEqual(service.cache_path.read_bytes(), cache_bytes)
            self.assertEqual(self.fake.document(), self.document)
            # Restore article-cache bytes into a fully new ephemeral directory;
            # the Git Data fake represents the external authoritative state.
            service.runtime.cleanup()
            restored = self.make_service(cache_bytes)
            restored.application._notify_websub()
            self.post.assert_called_once()
            self.assertEqual(set(self.fake.document()["state"]["observed_guids"]), {self.old, self.new})
            self.assertEqual(self.fake.document()["state"]["pending"], [])
            restored.runtime.cleanup()
            after_ack = self.make_service(cache_bytes)
            after_ack.application._notify_websub()
            self.post.assert_called_once()
            self.assertEqual(after_ack.cache_path.read_bytes(), cache_bytes)
            for path in ("/rss", "/news-sitemap.xml"):
                self.assertEqual(after_ack.client.get(path).status_code, 200)
            self.network.assert_not_called()
            self.socket.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
