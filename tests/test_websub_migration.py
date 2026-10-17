"""Offline migration proofs: fence, exact transfer, cancellation, no baseline."""
import copy
import hashlib
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


class LegacyReadOnlyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / "legacy.websub.json"
        self.original = legacy_state()
        self.path.write_text(json.dumps(self.original), encoding="utf-8")

    def test_oversized_import_or_empty_guid_archive_stops_before_fence(self):
        for state in (dict(self.original, preserved_extension="x" * migration.MAX_STATE_BYTES),
                      {"version": 2, "observed_guids": [], "pending": []}):
            with self.subTest(empty=not state["observed_guids"]):
                self.path.write_text(json.dumps(state), encoding="utf-8")
                before = self.path.read_bytes()
                with self.assertRaises(migration.MigrationError):
                    migration.check_legacy_state(self.path, now=1000)
                with self.assertRaises(migration.MigrationError):
                    migration.fence_legacy_state(self.path, MIGRATION_ID)
                self.assertEqual(self.path.read_bytes(), before)

    def test_import_size_is_checked_without_loading_remote_backend(self):
        with patch.object(migration, "_store", side_effect=AssertionError("Remote accessed")):
            result = migration.check_legacy_state(self.path, now=1000)
        self.assertLess(result["migration_document_bytes"], migration.MAX_STATE_BYTES)
        self.assertFalse(result["capacity_warning"])

    def test_nonregular_file_and_excessive_read_size_fail_without_opening(self):
        with self.assertRaises(migration.MigrationError):
            migration.check_legacy_state(self.folder, now=1000)
        with self.path.open("wb") as handle:
            handle.truncate(migration.MAX_LEGACY_READ_BYTES + 1)
        before = self.path.stat()
        with self.assertRaises(migration.MigrationError):
            migration.check_legacy_state(self.path, now=1000)
        self.assertEqual(self.path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_preflight_is_strictly_read_only_without_locks_http_export_or_content_leak(self):
        before = self.path.read_bytes()
        names = sorted(path.name for path in self.folder.iterdir())
        original_open = open
        def readonly_open(path, mode="r", *args, **kwargs):
            self.assertFalse(any(flag in mode for flag in "wax+"), "Preflight attempted a write")
            return original_open(path, mode, *args, **kwargs)
        with patch("builtins.open", side_effect=readonly_open), \
                patch.object(migration, "atomic_json", side_effect=AssertionError("State write")), \
                patch.object(migration, "cache_transaction", side_effect=AssertionError("State lock")), \
                patch.object(migration, "_file_lock", side_effect=AssertionError("Publisher lock")), \
                patch.object(migration, "_file_unlock", side_effect=AssertionError("Publisher unlock")), \
                patch.object(migration, "_store", side_effect=AssertionError("GitHub accessed")), \
                patch.object(migration, "export_document", side_effect=AssertionError("Export attempted")), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP attempted")), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(migration.main(["check-legacy", "--state-path", str(self.path)]), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["path"], str(self.path.resolve()))
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["observed_guids"], 2)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["size_bytes"], len(before))
        self.assertEqual(result["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(result["file_identity"]["inode"], self.path.stat().st_ino)
        self.assertNotIn("https://example.org", stdout.getvalue())
        self.assertNotIn("pending-original", stdout.getvalue())
        self.assertNotIn("preserved_extension", stdout.getvalue())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), names)

    def test_summary_keeps_original_budgets_and_distinguishes_due_and_expired(self):
        before = self.path.read_bytes()
        result = migration.check_legacy_state(self.path, now=1000)
        self.assertEqual(result["pending_guids"], 1)
        self.assertEqual(result["feed_checks_total"], 7)
        self.assertEqual(result["hub_attempts_total"], 3)
        self.assertEqual(result["expired_pending"], 0)
        self.assertEqual(result["exhausted_pending"], 0)
        self.assertEqual(result["due_pending"], 1)
        self.assertEqual(result["earliest_expires_at"], 259300)
        self.assertEqual(result["latest_expires_at"], 259300)
        self.assertEqual(result["earliest_next_attempt_at"], 301)
        expired = migration.check_legacy_state(self.path, now=259300)
        self.assertEqual(expired["expired_pending"], 1)
        self.assertEqual(expired["exhausted_pending"], 1)
        self.assertEqual(expired["due_pending"], 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_pending_has_null_deadlines_without_creating_baseline(self):
        self.original["pending"] = []
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        before = self.path.read_bytes()
        result = migration.check_legacy_state(self.path, now=1000)
        self.assertEqual(result["observed_guids"], 2)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["due_pending"], 0)
        self.assertIsNone(result["earliest_expires_at"])
        self.assertIsNone(result["latest_expires_at"])
        self.assertIsNone(result["earliest_next_attempt_at"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_path_policy_matches_configured_default_and_explicit_publisher_paths(self):
        with patch.dict(os.environ, {"WEBSUB_STATE_PATH": "  " + str(self.path) + "  ",
                                     "CACHE_PATH": str(self.folder / "unused.json")}):
            self.assertEqual(migration.check_legacy_state(now=1000)["path"], str(self.path.resolve()))
            self.assertEqual(migration.check_legacy_state(self.path, now=1000)["path"], str(self.path.resolve()))
        cache = self.folder / "cache" / "articles_cache.json"
        cache.parent.mkdir()
        fallback = Path(str(cache) + ".websub.json")
        fallback.write_bytes(self.path.read_bytes())
        with patch.dict(os.environ, {"WEBSUB_STATE_PATH": "   ", "CACHE_PATH": str(cache)}):
            self.assertEqual(migration.check_legacy_state(now=1000)["path"], str(fallback.resolve()))
        previous = Path.cwd()
        try:
            os.chdir(self.folder)
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(migration.check_legacy_state(now=1000)["path"], str(fallback.resolve()))
        finally:
            os.chdir(previous)

    def test_invalid_or_missing_state_fails_without_fence_or_lock_side_effects(self):
        bad_deadline = copy.deepcopy(self.original)
        bad_deadline["pending"][0]["expires_at"] += 1
        nonfinite = copy.deepcopy(self.original)
        nonfinite["pending"][0]["next_attempt_at"] = float("nan")
        cases = (None, b"{broken", b'{"version":1,"observed_guids":[],"pending":[]}',
                 b'{"version":"migration-fence-v1","legacy_state":{}}',
                 b'{"version":2,"version":2,"observed_guids":[],"pending":[]}',
                 json.dumps(bad_deadline).encode(), json.dumps(nonfinite).encode())
        for content in cases:
            with self.subTest(content=content):
                if self.path.exists():
                    self.path.unlink()
                if content is not None:
                    self.path.write_bytes(content)
                names = sorted(path.name for path in self.folder.iterdir())
                with patch.object(migration, "fence_legacy_state", side_effect=AssertionError("Fence attempted")), \
                        patch.object(migration, "_store", side_effect=AssertionError("GitHub accessed")), \
                        patch.object(migration, "atomic_json", side_effect=AssertionError("State write")), \
                        patch.object(migration, "cache_transaction", side_effect=AssertionError("State lock")), \
                        patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                        patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    self.assertEqual(migration.main(["check-legacy", "--state-path", str(self.path)]), 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("https://example.org", stderr.getvalue())
                self.assertEqual(self.path.read_bytes() if self.path.exists() else None, content)
                self.assertEqual(sorted(path.name for path in self.folder.iterdir()), names)

    def test_atomic_replacement_during_read_retries_the_current_file(self):
        real_fstat = os.fstat
        real_stat = Path.stat
        calls = []
        replaced = []
        updated = copy.deepcopy(self.original)
        updated["pending"][0]["hub_attempts"] = 4
        def counting_fstat(descriptor):
            calls.append(descriptor)
            return real_fstat(descriptor)
        def replacing_stat(path, *args, **kwargs):
            if path == self.path and len(calls) == 2 and not replaced:
                replaced.append(True)
                atomic_json(self.path, updated)  # external writer's atomic replacement
            return real_stat(path, *args, **kwargs)
        with patch.object(migration.os, "fstat", side_effect=counting_fstat), \
                patch.object(migration.Path, "stat", autospec=True, side_effect=replacing_stat):
            result = migration.check_legacy_state(self.path, now=1000)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result["hub_attempts_total"], 4)
        self.assertEqual(result["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_continuously_changing_file_fails_after_three_attempts_without_writes(self):
        real_fstat = os.fstat
        calls = []
        before = self.path.read_bytes()
        def changing_fstat(descriptor):
            actual = real_fstat(descriptor)
            calls.append(descriptor)
            values = {name: getattr(actual, name) for name in (
                "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")}
            if len(calls) % 2 == 0:
                values["st_mtime_ns"] += 1
            return types.SimpleNamespace(**values)
        with patch.object(migration.os, "fstat", side_effect=changing_fstat), \
                patch.object(migration, "atomic_json", side_effect=AssertionError("State write")), \
                patch.object(migration, "cache_transaction", side_effect=AssertionError("State lock")):
            with self.assertRaisesRegex(migration.MigrationError, "tre letture"):
                migration.check_legacy_state(self.path, now=1000)
        self.assertEqual(len(calls), 6)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
