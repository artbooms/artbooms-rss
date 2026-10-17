"""Offline fixed-deadline, receipt retention and state-capacity regressions."""
import copy
from contextlib import ExitStack
import json
import logging
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_websub import OLD, NEW, feed
from test_websub_github import BRANCH, REPO, STATE_PATH, TOKEN, FakeGitHubSession, initial_document
import websub
import websub_durable as publisher
from websub_store import GitHubStore, MAX_DOCUMENT_BYTES

LATER = "https://www.artbooms.com/blog/later-offline-fixture"
PENDING = "https://www.artbooms.com/blog/pending-offline-fixture"


def encoded(document):
    return json.dumps(document, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def receipt(guid, at, expires_at, status="accepted"):
    return {"status": status, "at": at, "guids": [guid], "hub_attempts": 1,
            "feed_checks": 1, "expires_at": expires_at}


class WebSubHistoryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 2_000_000.0
        self.stack.enter_context(patch("time.time", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(publisher, "_capacity_warning", None))
        self.network = self.stack.enter_context(patch.object(
            requests.Session, "request", side_effect=AssertionError("Real HTTP forbidden")))
        self.socket = self.stack.enter_context(patch.object(
            socket.socket, "connect", side_effect=AssertionError("Real sockets forbidden")))
        self.get = self.stack.enter_context(patch.object(requests, "get", return_value=Mock(
            status_code=200, content=feed(OLD, NEW, LATER), headers={})))
        self.post = self.stack.enter_context(patch.object(requests, "post", return_value=Mock(
            status_code=204, headers={})))
        disabled = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, disabled)

    def store(self, fake):
        return GitHubStore(REPO, BRANCH, STATE_PATH, TOKEN, session=fake)

    def pending(self, guid=PENDING):
        return {"id": "existing-pending", "guids": [guid], "hub_attempts": 1,
                "feed_checks": 2, "created_at": self.now - 3600,
                "expires_at": self.now - 3600 + websub.EVENT_TTL,
                "next_attempt_at": self.now + 900, "lease_token": "existing-owner",
                "lease_until": self.now + publisher.LEASE_SECONDS}

    def publish(self, fake, guids=(OLD, NEW)):
        return publisher.run(feed(*guids), store=self.store(fake), now=lambda: self.now)

    def old_document(self):
        document = initial_document()
        document["state"]["observed_guids"] = sorted((OLD, NEW, PENDING))
        document["completed"] = {"old-accepted": receipt(
            NEW, self.now - publisher.RECEIPT_RETENTION - 3600,
            self.now - publisher.RECEIPT_RETENTION - 1)}
        return document

    def test_retention_uses_both_completion_and_original_expiry_with_exact_cutoff(self):
        document = self.old_document()
        retention = publisher.RECEIPT_RETENTION
        document["state"]["pending"] = [self.pending()]
        document["completed"].update({
            "cutoff-exact": receipt(OLD, self.now - retention - 100, self.now - retention, "expired"),
            "expiry-one-second-later": receipt(OLD, self.now - retention - 100, self.now - retention + 1),
            "ack-one-second-later": receipt(OLD, self.now - retention + 1, self.now - retention - 100, "terminal"),
            "recent-ack": receipt(NEW, self.now - 1, self.now + 3600)})
        pending_before = encoded(document["state"]["pending"])
        observed_before = encoded(document["state"]["observed_guids"])
        self.assertEqual(publisher._prune_completed(document, self.now), 2)
        self.assertEqual(set(document["completed"]),
                         {"expiry-one-second-later", "ack-one-second-later", "recent-ack"})
        self.assertEqual(publisher._prune_completed(document, self.now + 1), 2)
        self.assertEqual(set(document["completed"]), {"recent-ack"})
        self.assertEqual(encoded(document["state"]["pending"]), pending_before)
        self.assertEqual(encoded(document["state"]["observed_guids"]), observed_before)

    def test_old_receipts_are_not_an_idle_write_but_compact_on_a_required_transition(self):
        document = self.old_document()
        document["state"]["pending"] = [self.pending()]
        fake = FakeGitHubSession(document)
        untouched = publisher._transaction(self.store(fake), lambda candidate: None, now=lambda: self.now)
        self.assertEqual(untouched, document)
        self.assertEqual(fake.writes, [])
        changed = publisher._transaction(self.store(fake), lambda candidate: candidate.update(mode="paused"), now=self.now)
        self.assertEqual(changed["completed"], {})
        self.assertEqual(changed["state"], document["state"])
        self.assertEqual(changed["revision"], document["revision"] + 1)

    def large_document(self, target_bytes):
        document = initial_document()
        old_at = self.now - publisher.RECEIPT_RETENTION - websub.EVENT_TTL - 100
        def add(candidate, index):
            guid = "https://www.artbooms.com/blog/archive-%05d" % index
            candidate["state"]["observed_guids"].append(guid)
            candidate["completed"]["%032x" % index] = receipt(guid, old_at, old_at + websub.EVENT_TTL)
        sample = copy.deepcopy(document)
        add(sample, 0)
        per_event = len(encoded(sample)) - len(encoded(document)) + 1
        count = (target_bytes - len(encoded(document))) // per_event
        for index in range(count):
            add(document, index)
        return document

    def test_compaction_occurs_before_hard_cap_check_on_needed_state_commit(self):
        document = self.large_document(895 * 1024)
        self.assertLessEqual(len(encoded(document)), MAX_DOCUMENT_BYTES)
        self.assertGreater(len(encoded(document)), 880 * 1024)
        new_guids = {"https://www.artbooms.com/blog/new-%05d" % index for index in range(300)}
        canonical_guids = set(document["state"]["observed_guids"]) | new_guids
        uncompacted = copy.deepcopy(document)
        publisher._reconcile(uncompacted, canonical_guids, self.now)
        self.assertGreater(len(encoded(uncompacted)), MAX_DOCUMENT_BYTES)
        fake = FakeGitHubSession(document)
        result = publisher._transaction(self.store(fake), lambda candidate:
            publisher._reconcile(candidate, canonical_guids, self.now), now=self.now)
        self.assertLess(len(encoded(result)), MAX_DOCUMENT_BYTES)
        self.assertEqual(result["completed"], {})
        self.assertEqual(set(result["state"]["observed_guids"]), canonical_guids)
        self.assertEqual(set(result["state"]["pending"][0]["guids"]), new_guids)
        self.assertEqual(result["state"]["pending"][0]["expires_at"], self.now + websub.EVENT_TTL)
        self.assertEqual(fake.document(), result)

    def test_concurrent_compaction_and_ack_preserve_new_pending_and_recent_receipt(self):
        document = self.old_document()
        pending = self.pending()
        document["state"]["pending"] = [pending]
        fake = FakeGitHubSession(document)
        barrier = threading.Barrier(2)
        class ConcurrentStore(GitHubStore):
            first_load = True
            def load(instance):
                snapshot = super().load()
                if instance.first_load:
                    instance.first_load = False
                    barrier.wait(timeout=10)
                return snapshot
        changes = (
            lambda candidate: publisher._reconcile(candidate, {OLD, NEW, PENDING, LATER}, self.now),
            lambda candidate: publisher._finish(candidate, pending["id"], pending["lease_token"], self.now, accepted=True))
        results, errors = [], []
        def worker(change):
            try:
                store = ConcurrentStore(REPO, BRANCH, STATE_PATH, TOKEN, session=fake)
                results.append(publisher._transaction(store, change, now=lambda: self.now))
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=worker, args=(change,)) for change in changes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        result = fake.document()
        self.assertEqual(set(result["state"]["observed_guids"]), {OLD, NEW, PENDING, LATER})
        self.assertEqual([event["guids"] for event in result["state"]["pending"]], [[LATER]])
        self.assertNotIn("old-accepted", result["completed"])
        self.assertEqual(result["completed"][pending["id"]]["status"], "accepted")
        self.assertEqual(result["completed"][pending["id"]]["expires_at"], pending["expires_at"])

    def test_compacted_ack_does_not_requeue_guid_on_canonical_feed_or_restart(self):
        document = self.old_document()
        fake = FakeGitHubSession(document)
        self.assertTrue(self.publish(fake, (OLD, NEW, PENDING, LATER)))
        self.assertNotIn("old-accepted", fake.document()["completed"])
        self.assertEqual(fake.document()["state"]["pending"], [])
        for _ in range(3):
            self.assertFalse(self.publish(fake, (OLD, NEW, PENDING, LATER)))
        self.post.assert_called_once()
        self.assertEqual(set(fake.document()["state"]["observed_guids"]), {OLD, NEW, PENDING, LATER})

    def sized_document(self, size):
        document = initial_document()
        document["capacity_fixture_padding"] = ""
        document["capacity_fixture_padding"] = "x" * (size - len(encoded(document)))
        self.assertEqual(len(encoded(document)), size)
        return document

    def test_capacity_warning_thresholds_escalation_hourly_throttle_and_private_logs(self):
        self.now = 1000.0  # The first warning must work before the first hour too.
        with patch.object(publisher.logger, "warning") as warning:
            publisher._warn_capacity(self.sized_document(600 * 1024 - 1), self.now)
            warning.assert_not_called()
            publisher._warn_capacity(self.sized_document(600 * 1024), self.now)
            self.assertEqual(warning.call_count, 1)
            publisher._warn_capacity(self.sized_document(700 * 1024), self.now + 10)
            self.assertEqual(warning.call_count, 1)
            publisher._warn_capacity(self.sized_document(800 * 1024), self.now + 11)
            self.assertEqual(warning.call_count, 2)
            publisher._warn_capacity(self.sized_document(850 * 1024), self.now + 3610)
            self.assertEqual(warning.call_count, 2)
            publisher._warn_capacity(self.sized_document(850 * 1024), self.now + 3611)
            self.assertEqual(warning.call_count, 3)
            messages = " ".join(str(call) for call in warning.call_args_list)
            self.assertNotIn(OLD, messages)
            self.assertNotIn(TOKEN, messages)
            self.assertNotIn("https://", messages)

    def test_existing_pending_expires_after_backend_outage_but_unseen_guid_gets_its_own_deadline(self):
        fake = FakeGitHubSession(initial_document())
        self.post.return_value = Mock(status_code=503, headers={})
        self.assertFalse(self.publish(fake))
        pending = copy.deepcopy(fake.document()["state"]["pending"][0])
        self.now = pending["expires_at"] + 1
        before = copy.deepcopy(fake.document())
        fake.queue_fault("GET", "/git/ref/heads/" + BRANCH, status=503)
        self.assertFalse(self.publish(fake, (OLD, NEW, LATER)))
        self.assertEqual(fake.document(), before)
        self.post.assert_called_once()
        self.get.return_value = Mock(status_code=200, content=feed(OLD, NEW), headers={})
        self.assertFalse(self.publish(fake, (OLD, NEW, LATER)))
        result = fake.document()
        self.assertEqual(result["completed"][pending["id"]]["status"], "expired")
        self.assertEqual(result["completed"][pending["id"]]["expires_at"], pending["expires_at"])
        newly_seen = result["state"]["pending"][0]
        self.assertEqual(newly_seen["guids"], [LATER])
        self.assertEqual(newly_seen["created_at"], self.now)
        self.assertEqual(newly_seen["expires_at"], self.now + 72 * 3600)
        self.assertEqual(newly_seen["hub_attempts"], 0)
        self.now = newly_seen["next_attempt_at"]
        self.get.return_value = Mock(status_code=200, content=feed(OLD, NEW, LATER), headers={})
        self.post.return_value = Mock(status_code=204, headers={})
        self.assertTrue(self.publish(fake, (OLD, NEW, LATER)))
        self.assertEqual(self.post.call_count, 2)
        self.assertFalse(self.publish(fake, (OLD, NEW, LATER)))

    def near_deadline_document(self):
        document = initial_document()
        document["state"]["observed_guids"].append(NEW)
        document["state"]["pending"] = [{"id": "near-deadline", "guids": [NEW],
            "hub_attempts": 0, "feed_checks": 0, "created_at": self.now - websub.EVENT_TTL + 1,
            "expires_at": self.now + 1, "next_attempt_at": 0}]
        return document

    def test_readiness_finishing_at_deadline_never_starts_post_and_live_lease_is_not_pruned(self):
        document = self.near_deadline_document()
        fake = FakeGitHubSession(document)
        deadline = document["state"]["pending"][0]["expires_at"]
        def late_get(*args, **kwargs):
            self.now = deadline
            return Mock(status_code=200, content=feed(OLD, NEW), headers={})
        self.get.side_effect = late_get
        self.assertFalse(self.publish(fake))
        event = fake.document()["state"]["pending"][0]
        self.assertEqual(event["expires_at"], deadline)
        self.assertEqual((event["feed_checks"], event["hub_attempts"]), (1, 0))
        self.assertFalse(self.publish(fake))
        self.assertEqual(fake.document()["completed"], {})
        self.post.assert_not_called()
        self.now = event["lease_until"]
        self.assertFalse(self.publish(fake))
        self.assertEqual(fake.document()["state"]["pending"], [])
        self.assertEqual(fake.document()["completed"][event["id"]]["status"], "expired")
        self.get.assert_called_once()

    def test_post_started_before_deadline_can_persist_accepted_ack_after_deadline(self):
        document = self.near_deadline_document()
        fake = FakeGitHubSession(document)
        deadline = document["state"]["pending"][0]["expires_at"]
        def accepted_after_deadline(*args, **kwargs):
            self.now = deadline + 10
            return Mock(status_code=204, headers={})
        self.post.side_effect = accepted_after_deadline
        self.assertTrue(self.publish(fake))
        result = fake.document()
        self.assertEqual(result["state"]["pending"], [])
        completed = result["completed"]["near-deadline"]
        self.assertEqual((completed["status"], completed["at"], completed["expires_at"]),
                         ("accepted", deadline + 10, deadline))
        self.assertFalse(self.publish(fake))
        self.post.assert_called_once()

    def test_over_limit_remote_document_stays_closed_without_truncation_or_hub_io(self):
        document = self.sized_document(MAX_DOCUMENT_BYTES + 1)
        fake = FakeGitHubSession(document)
        self.assertFalse(self.publish(fake))
        self.assertEqual(fake.document(), document)
        self.assertEqual(fake.writes, [])
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.network.assert_not_called()
        self.socket.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
