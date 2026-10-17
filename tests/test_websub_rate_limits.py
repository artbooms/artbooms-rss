"""GitHub's cooldown deadlines must survive the publisher's retry policy."""
from datetime import datetime, timezone
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import websub_durable
import websub_store


class GitHubRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc)
        self.guard = patch.object(socket.socket, "connect", side_effect=AssertionError("Real network forbidden"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def failure_delay(self, status, headers):
        response = Mock(status_code=status, headers=headers)
        session = Mock()
        session.request.return_value = response
        store = websub_store.GitHubStore("fixture-owner/fixture-repository", "websub-state",
                                        "websub-state.json", "offline-only-token", session=session)
        with patch.object(websub_store, "datetime") as clock:
            clock.now.return_value = self.now
            with self.assertRaises(websub_store.Unavailable) as raised:
                store.load()
        self.assertEqual(raised.exception.status_code, status)
        self.assertEqual(session.request.call_count, 1)
        response.close.assert_called_once()
        return raised.exception.retry_after

    def test_retry_after_longer_than_a_day_is_preserved_without_cap(self):
        self.assertEqual(self.failure_delay(429, {"Retry-After": "172800"}), 172800)

    def test_primary_limit_uses_reset_when_retry_after_is_absent(self):
        reset = str(int(self.now.timestamp()) + 5400)
        self.assertEqual(self.failure_delay(403, {"x-ratelimit-remaining": "0",
                                                  "x-ratelimit-reset": reset}), 5400)

    def test_primary_reset_and_retry_after_obey_the_later_deadline(self):
        reset = str(int(self.now.timestamp()) + 7200)
        self.assertEqual(self.failure_delay(429, {"Retry-After": "60",
            "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}), 7200)

    def test_retry_after_http_date_is_preserved(self):
        self.assertEqual(self.failure_delay(429,
            {"Retry-After": "Sun, 18 Oct 2026 20:00:00 GMT"}), 172800)

    def test_reset_is_not_used_without_an_exhausted_primary_quota(self):
        reset = str(int(self.now.timestamp()) + 7200)
        self.assertIsNone(self.failure_delay(503, {"X-RateLimit-Remaining": "1",
                                                   "X-RateLimit-Reset": reset}))

    def test_nonfinite_and_invalid_deadlines_do_not_create_an_infinite_cooldown(self):
        for value in ("NaN", "Infinity", "invalid", "9" * 1000):
            with self.subTest(value=value[:20]):
                self.assertIsNone(self.failure_delay(429, {"Retry-After": value,
                    "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": value}))

    def test_background_cooldown_does_not_retry_github_before_long_deadline(self):
        current = [1000000.0]
        store = Mock()
        store.load.side_effect = websub_store.Unavailable("Quota exhausted", retry_after=7200)
        with patch.object(websub_durable, "_retry_not_before", 0), \
                patch.object(websub_durable, "_run_lock", threading.Lock()), \
                patch.object(websub_durable, "_store_from_env", return_value=store), \
                patch.object(websub_durable.legacy, "_feed_guids", return_value={"existing-guid"}), \
                patch.object(websub_durable.requests, "post") as hub:
            self.assertFalse(websub_durable.run(b"unused", now=lambda: current[0]))
            self.assertEqual(store.load.call_count, 1)
            for advance in (3601, 7199):
                current[0] = 1000000.0 + advance
                self.assertFalse(websub_durable.run(b"unused", now=lambda: current[0]))
                self.assertEqual(store.load.call_count, 1)
            current[0] = 1000000.0 + 7200
            self.assertFalse(websub_durable.run(b"unused", now=lambda: current[0]))
            self.assertEqual(store.load.call_count, 2)
            hub.assert_not_called()


if __name__ == "__main__":
    unittest.main()
