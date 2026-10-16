"""Offline migration proofs: fence, exact transfer, cancellation, no baseline."""
import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import migrate_websub_state as migration
from cache_safety import atomic_json

MIGRATION_ID = "ad8546f0-8ed2-4071-ad15-265c9e803408"
OTHER_ID = "add37c01-243f-4236-9786-5f3ace967cc2"


def legacy_state():
    return {"version": 2, "observed_guids": ["https://example.org/archive", "https://example.org/new"],
            "pending": [{"id": "pending-original", "guids": ["https://example.org/new"],
                         "hub_attempts": 3, "feed_checks": 7, "created_at": 100,
                         "expires_at": 259300, "next_attempt_at": 301}],
            "preserved_extension": {"original": True}}


class FakeStore:
    def __init__(self, document=None):
        self.document = copy.deepcopy(document)
        self.calls = []
        self.head = "initial"
        self.unavailable = False
        self.conflict = False

    def load(self):
        self.calls.append("load")
        if self.unavailable or self.document is None:
            raise RuntimeError("GitHub unavailable")
        return types.SimpleNamespace(head=self.head, document=copy.deepcopy(self.document))

    def initialize(self, document):
        self.calls.append("initialize")
        if self.unavailable:
            raise RuntimeError("GitHub unavailable")
        if self.document is not None and self.document != document:
            raise RuntimeError("Existing state is different")
        self.document = copy.deepcopy(document)
        return self.load()

    def commit(self, snapshot, document):
        self.calls.append("commit")
        if self.unavailable or self.conflict or snapshot.head != self.head:
            raise RuntimeError("CAS unavailable/conflict")
        self.document = copy.deepcopy(document)
        self.head += "-next"
        return self.load()


class WebSubMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / "legacy.websub.json"
        self.original = legacy_state()
        atomic_json(self.path, self.original)

    def fence(self):
        return migration.fence_legacy_state(self.path, MIGRATION_ID)

    def test_fence_preserves_every_field_and_pending_budget_without_feed(self):
        fence = self.fence()
        self.assertEqual(fence["legacy_state"], self.original)
        self.assertEqual(json.loads(self.path.read_text()), fence)
        document = migration.build_document(fence)
        self.assertEqual(document, {"format": 1, "topic": migration.TOPIC, "state": self.original,
                                   "completed": {}, "mode": "paused", "migration_id": MIGRATION_ID, "revision": 0})

    def test_restart_with_same_identifier_never_rewrites_fence(self):
        fence = self.fence()
        before = self.path.read_bytes()
        self.assertEqual(migration.fence_legacy_state(self.path, MIGRATION_ID), fence)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(migration.MigrationError):
            migration.fence_legacy_state(self.path, OTHER_ID)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_corrupt_legacy_v1_or_bad_v2_never_becomes_baseline(self):
        for content in (None, b"{broken", b'{"version":1,"observed_guids":[],"pending":[]}',
                        b'{"version":2,"observed_guids":[],"pending":[{}]}'):
            with self.subTest(content=content):
                if self.path.exists():
                    self.path.unlink()
                if content is not None:
                    self.path.write_bytes(content)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.fence()
                self.assertEqual(self.path.read_bytes() if self.path.exists() else None, content)

    def test_tampered_embedded_state_or_duplicate_json_keys_are_rejected(self):
        fence = self.fence()
        fence["legacy_state"]["pending"][0]["hub_attempts"] = 0
        atomic_json(self.path, fence)
        with self.assertRaises(migration.MigrationError):
            migration.read_fenced_state(self.path)
        self.path.write_text('{"version":2,"version":2,"observed_guids":[],"pending":[]}')
        with self.assertRaises(migration.MigrationError):
            self.fence()

    def test_busy_publisher_times_out_without_acquiring_state_lock_or_writing(self):
        before = self.path.read_bytes()
        with patch.object(migration, "_file_lock", side_effect=BlockingIOError(11, "busy")), \
             patch.object(migration, "cache_transaction", side_effect=AssertionError("State lock too early")):
            with self.assertRaises(migration.MigrationError):
                migration.fence_legacy_state(self.path, MIGRATION_ID, wait_timeout=0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_waits_for_inflight_publisher_then_fences_after_its_final_state(self):
        acquired = []
        def lock(handle, blocking):
            if not acquired:
                acquired.append("inflight")
                finished = copy.deepcopy(self.original)
                finished["pending"] = []
                atomic_json(self.path, finished)
                raise BlockingIOError(11, "finishing POST")
            acquired.append("locked")
        with patch.object(migration, "_file_lock", side_effect=lock), \
             patch.object(migration, "_file_unlock"), patch.object(migration.time, "sleep"):
            fence = self.fence()
        self.assertEqual(acquired, ["inflight", "locked"])
        self.assertEqual(fence["legacy_state"]["pending"], [])
        self.assertEqual(fence["legacy_state"]["observed_guids"], self.original["observed_guids"])

    def test_real_process_publisher_finishes_before_fence_without_holding_state_lock(self):
        ready, release = self.folder / "ready", self.folder / "release"
        code = """
import json, pathlib, sys, time
from cache_safety import _file_lock, _file_unlock, atomic_json, cache_transaction
path, ready, release = map(pathlib.Path, sys.argv[1:])
with open(str(path) + '.publish.lock', 'a+b') as lock:
    _file_lock(lock, blocking=False)
    ready.write_text('ready')
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Parent did not release simulated POST')
        time.sleep(.01)
    with cache_transaction(path):
        state = json.loads(path.read_text())
        state['pending'] = []
        atomic_json(path, state)
    _file_unlock(lock)
"""
        process = subprocess.Popen([sys.executable, "-c", code, str(self.path), str(ready), str(release)],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def stop_process():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        self.addCleanup(stop_process)
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(ready.exists(), "Simulated publisher failed to acquire its real process lock")
        results, errors = [], []
        def migrate():
            try:
                results.append(migration.fence_legacy_state(self.path, MIGRATION_ID, wait_timeout=5))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=migrate)
        worker.start()
        time.sleep(.05)
        self.assertTrue(worker.is_alive())
        with migration.cache_transaction(self.path, blocking=False) as acquired:
            self.assertTrue(acquired, "Waiting for publisher must not hold the state lock")
            self.assertEqual(json.loads(self.path.read_text()), self.original)
        release.write_text("finish simulated POST")
        worker.join(timeout=6)
        self.assertFalse(worker.is_alive())
        output, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, error)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["legacy_state"]["pending"], [])
        self.assertEqual(results[0]["legacy_state"]["observed_guids"], self.original["observed_guids"])

    def test_explicit_initialization_and_recovery_after_github_outage(self):
        document = migration.build_document(self.fence())
        store = FakeStore()
        with self.assertRaises(RuntimeError):
            migration.initialize_remote(store, document)
        self.assertNotIn("initialize", store.calls)
        store.unavailable = True
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            migration.initialize_remote(store, document, allow_initialize=True)
        self.assertEqual(self.path.read_bytes(), before)
        store.unavailable = False
        snapshot = migration.initialize_remote(store, document, allow_initialize=True)
        self.assertEqual(snapshot.document, document)
        self.assertEqual(migration.initialize_remote(store, document).document, document)
        self.assertNotIn("commit", store.calls)

    def test_existing_newer_state_is_not_overwritten(self):
        document = migration.build_document(self.fence())
        for change in ({"migration_id": OTHER_ID}, {"revision": 1}, {"mode": "active"},
                       {"state": dict(self.original, pending=[])}):
            remote = dict(copy.deepcopy(document), **change)
            store = FakeStore(remote)
            with self.subTest(change=change), self.assertRaises(migration.MigrationError):
                migration.initialize_remote(store, document)
            self.assertEqual(store.document, remote)
            self.assertNotIn("commit", store.calls)

    def test_pause_resume_preserve_history_and_cas_conflict_is_not_retried(self):
        document = migration.build_document(self.fence())
        document["completed"] = {"past-event": {"status": "accepted", "at": 100,
            "guids": [self.original["observed_guids"][0]], "hub_attempts": 1,
            "feed_checks": 1, "expires_at": 259300}}
        store = FakeStore(document)
        self.assertEqual(migration.set_remote_mode(store, "active", MIGRATION_ID).document["revision"], 1)
        self.assertEqual(store.document["state"], document["state"])
        self.assertEqual(store.document["completed"], document["completed"])
        migration.set_remote_mode(store, "paused", MIGRATION_ID)
        self.assertEqual(store.document["revision"], 2)
        store.conflict = True
        before = copy.deepcopy(store.document)
        count = store.calls.count("commit")
        with self.assertRaises(RuntimeError):
            migration.set_remote_mode(store, "active", MIGRATION_ID)
        self.assertEqual(store.calls.count("commit"), count + 1)
        self.assertEqual(store.document, before)

    def test_network_never_runs_under_local_migration_transaction(self):
        document = migration.build_document(self.fence())
        store = FakeStore(document)
        @contextmanager
        def reject_local_locks(*args, **kwargs):
            raise AssertionError("Local lock during remote I/O")
            yield
        with patch.object(migration, "_legacy_transaction", reject_local_locks):
            migration.initialize_remote(store, document)
            migration.set_remote_mode(store, "active", MIGRATION_ID)
            migration.remote_status(store)

    def test_pre_activation_rollback_tombstones_remote_before_restoring(self):
        store = FakeStore(migration.build_document(self.fence()))
        with patch.object(migration, "atomic_json", wraps=atomic_json) as write:
            migration.rollback_fence(store, self.path, MIGRATION_ID)
            write.assert_called_once_with(self.path, self.original)
        self.assertEqual(json.loads(self.path.read_text()), self.original)
        self.assertTrue(store.document["cancelled"])
        self.assertEqual(store.document["revision"], 1)
        with self.assertRaises(migration.MigrationError):
            migration.set_remote_mode(store, "active", MIGRATION_ID)
        # Lost CLI output after restoring is harmless: repeat makes no write.
        migration.rollback_fence(store, self.path, MIGRATION_ID)
        self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_crash_after_remote_cancellation_resumes_without_new_transition(self):
        store = FakeStore(migration.build_document(self.fence()))
        with patch.object(migration, "_legacy_transaction", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                migration.rollback_fence(store, self.path, MIGRATION_ID)
        self.assertTrue(store.document["cancelled"])
        self.assertEqual(json.loads(self.path.read_text())["version"], migration.FENCE_VERSION)
        commits = store.calls.count("commit")
        migration.rollback_fence(store, self.path, MIGRATION_ID)
        self.assertEqual(store.calls.count("commit"), commits)
        self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_rollback_after_activation_or_other_transition_never_restores(self):
        document = migration.build_document(self.fence())
        for change in ({"mode": "active", "revision": 1}, {"revision": 2},
                       {"state": dict(self.original, pending=[])}):
            before = self.path.read_bytes()
            store = FakeStore(dict(copy.deepcopy(document), **change))
            with self.subTest(change=change), self.assertRaises(migration.MigrationError):
                migration.rollback_fence(store, self.path, MIGRATION_ID)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertNotIn("commit", store.calls)

    def test_export_is_private_new_file_and_does_not_touch_cache(self):
        document = migration.build_document(self.fence())
        output = self.folder / "backup.json"
        with patch.dict(os.environ, {"CACHE_PATH": str(self.folder / "cache" / "articles.json")}):
            migration.export_document(document, output)
            with self.assertRaises(FileExistsError):
                migration.export_document(document, output)
            with self.assertRaises(migration.MigrationError):
                migration.export_document(document, self.folder / "cache" / "bad.json")
        self.assertEqual(json.loads(output.read_text()), document)
        if os.name != "nt":
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_local_cli_commands_do_not_load_backend_or_print_guids(self):
        with patch.object(migration, "_store", side_effect=AssertionError("Backend accessed")), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(migration.main(["fence", "--state-path", str(self.path),
                                             "--migration-id", MIGRATION_ID]), 0)
            self.assertEqual(migration.main(["status", "--state-path", str(self.path)]), 0)
        self.assertNotIn("https://example.org", stdout.getvalue())
        self.assertIn(MIGRATION_ID, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
