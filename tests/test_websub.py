"""Offline WebSub state, readiness, retry and restart guarantees."""
import importlib.util
import json
import logging
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

import requests
import websub

OLD = "https://www.artbooms.com/blog/old"
NEW = "https://www.artbooms.com/blog/new"


def feed(*guids, hub=True, self_link=True, title="Artbooms"):
    links = ('<atom:link rel="self" href="%s" />' % websub.FEED_SELF_URL) if self_link else ""
    if hub:
        links += '<atom:link rel="hub" href="%s" />' % websub.WEBSUB_HUB_URL
    return ('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
            '<channel><title>%s</title><link>https://www.artbooms.com</link>'
            '<description>Articoli</description>%s%s</channel></rss>' % (
                escape(title), links, "".join('<item><title>Arte</title><guid>%s</guid></item>'
                                             % escape(guid) for guid in guids))).encode()


def response(status=200, content=b"", headers=None):
    return Mock(status_code=status, content=content, headers=headers or {})


def restart_module():
    spec = importlib.util.spec_from_file_location("_websub_restarted", websub.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WebSubTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "state.json")
        self.now = 1000.0
        self.get = self._patch("websub.requests.get", return_value=response(content=feed(OLD, NEW)))
        self.post = self._patch("websub.requests.post", return_value=response(204))
        self._patch("websub.time.time", side_effect=lambda: self.now)
        disabled = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, disabled)

    def _patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def state(self):
        return json.loads(Path(self.path).read_text(encoding="utf-8"))

    def queue_new(self):
        self.assertFalse(websub.observe_feed(feed(OLD), self.path))
        self.assertTrue(websub.observe_feed(feed(OLD, NEW), self.path))

    def next_retry(self):
        self.now = self.state()["pending"][0]["next_attempt_at"]

    def test_first_run_baselines_entire_archive_without_network(self):
        guids = ["https://www.artbooms.com/blog/article-%d" % index for index in range(1105)]
        self.assertFalse(websub.observe_feed(feed(*guids), self.path))
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(set(self.state()["observed_guids"]), set(guids))
        self.assertEqual(self.state()["pending"], [])
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_new_article_one_notification_only_after_public_availability(self):
        self.queue_new()
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.assertTrue(websub.publish_pending(self.path))
        self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(websub.publish_pending(self.path))
        self.get.assert_called_once()
        self.post.assert_called_once_with(
            websub.WEBSUB_HUB_URL,
            data={"hub.mode": "publish", "hub.url": websub.FEED_SELF_URL},
            headers=websub.HEADERS, timeout=(3, 10), allow_redirects=False)
        self.assertEqual(self.get.call_args.args, (websub.FEED_SELF_URL,))
        self.assertEqual(self.get.call_args.kwargs["timeout"], (3, 10))
        self.assertFalse(self.get.call_args.kwargs["allow_redirects"])
        self.assertEqual(self.state()["pending"], [])

    def test_unchanged_feed_and_existing_article_edits_do_not_notify(self):
        websub.observe_feed(feed(OLD), self.path)
        for content in (feed(OLD), feed(OLD, title="Nuovo titolo")):
            self.assertFalse(websub.observe_feed(content, self.path))
            self.assertFalse(websub.publish_pending(self.path))
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_restart_does_not_repeat_acknowledged_events_or_archive(self):
        self.queue_new()
        self.assertTrue(websub.publish_pending(self.path))
        restarted = restart_module()
        self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(restarted.publish_pending(self.path))
        self.post.assert_called_once()
        self.assertEqual(self.state()["observed_guids"], sorted((OLD, NEW)))

    def test_restart_preserves_pending_event_and_retry_budget(self):
        self.queue_new()
        self.post.return_value = response(503)
        self.assertFalse(websub.publish_pending(self.path))
        restarted = restart_module()
        self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(restarted.publish_pending(self.path))
        self.assertEqual(self.post.call_count, 1)
        self.next_retry()
        self.post.return_value = response(202)
        self.assertTrue(restarted.publish_pending(self.path))
        self.assertFalse(restarted.publish_pending(self.path))
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.state()["pending"], [])

    def test_missing_state_after_deploy_baselines_without_rebroadcast(self):
        self.queue_new()
        self.assertTrue(websub.publish_pending(self.path))
        Path(self.path).unlink()
        restarted = restart_module()
        self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(restarted.publish_pending(self.path))
        self.post.assert_called_once()

    def test_hub_failure_has_eight_persisted_attempts_then_stops(self):
        self.queue_new()
        self.post.return_value = response(503)
        for attempt in range(websub.MAX_HUB_ATTEMPTS):
            if attempt:
                self.next_retry()
            self.assertFalse(websub.publish_pending(self.path))
        self.now += 500
        self.assertFalse(websub.publish_pending(self.path))
        self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
        self.assertEqual(self.get.call_count, websub.MAX_HUB_ATTEMPTS)
        self.assertEqual(self.post.call_count, websub.MAX_HUB_ATTEMPTS)
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(set(self.state()["observed_guids"]), {OLD, NEW})

    def test_network_timeout_does_not_escape_or_repeat_before_retry_time(self):
        self.queue_new()
        self.post.side_effect = requests.Timeout("Hub unavailable")
        self.assertFalse(websub.publish_pending(self.path))
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"][0]["hub_attempts"], 1)
        self.assertEqual(self.state()["pending"][0]["feed_checks"], 1)
        self.post.assert_called_once()
        self.next_retry()
        self.get.side_effect = requests.Timeout("Public RSS unavailable")
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"][0]["hub_attempts"], 1)
        self.assertEqual(self.state()["pending"][0]["feed_checks"], 2)
        self.post.assert_called_once()

    def test_invalid_stale_or_redirected_public_feed_never_contacts_hub(self):
        invalid = (response(content=b"not XML"), response(content=feed(OLD)),
                   response(content=feed(OLD, NEW, hub=False)),
                   response(content=feed(OLD, NEW, self_link=False)),
                   response(302, feed(OLD, NEW)), response(503, feed(OLD, NEW)))
        for index, candidate in enumerate(invalid):
            with self.subTest(index=index):
                self.path = str(Path(self.path).parent / ("invalid-%d.json" % index))
                self.queue_new()
                self.get.return_value = candidate
                for check in range(3):
                    if check:
                        self.next_retry()
                    self.assertFalse(websub.publish_pending(self.path))
                self.assertEqual(self.state()["pending"][0]["feed_checks"], 3)
                self.assertEqual(self.state()["pending"][0]["hub_attempts"], 0)
        self.post.assert_not_called()

    def test_hub_redirect_is_terminal_failure_not_success(self):
        self.queue_new()
        self.post.return_value = response(302)
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"], [])
        self.assertFalse(websub.publish_pending(self.path))
        self.post.assert_called_once()

    def test_state_write_failure_is_isolated_and_prevents_network(self):
        with patch.object(websub, "atomic_json", side_effect=PermissionError("Read-only state")):
            self.assertFalse(websub.observe_feed(feed(OLD), self.path))
        self.queue_new()
        before = Path(self.path).read_bytes()
        with patch.object(websub, "atomic_json", side_effect=PermissionError("Read-only state")):
            self.assertFalse(websub.publish_pending(self.path))
            self.assertFalse(websub.observe_feed(feed(OLD, NEW, NEW + "-2"), self.path))
        self.assertEqual(Path(self.path).read_bytes(), before)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_corrupt_state_is_preserved_and_never_rebroadcast(self):
        Path(self.path).write_bytes(b"corrupt state")
        self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(Path(self.path).read_bytes(), b"corrupt state")
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_invalid_observed_xml_does_not_create_state(self):
        self.assertFalse(websub.observe_feed(b"not XML", self.path))
        self.assertFalse(Path(self.path).exists())
        self.assertFalse(websub.observe_feed(feed(OLD, hub=False), self.path))
        self.assertFalse(Path(self.path).exists())
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_new_articles_before_first_attempt_are_coalesced(self):
        self.queue_new()
        second = NEW + "-2"
        self.assertTrue(websub.observe_feed(feed(OLD, NEW, second), self.path))
        self.assertEqual(len(self.state()["pending"]), 1)
        self.assertEqual(set(self.state()["pending"][0]["guids"]), {NEW, second})
        self.get.return_value = response(content=feed(OLD, NEW, second))
        self.assertTrue(websub.publish_pending(self.path))
        self.post.assert_called_once()

    def test_new_article_does_not_reset_existing_event_retry_budget(self):
        self.queue_new()
        self.post.return_value = response(503)
        self.assertFalse(websub.publish_pending(self.path))
        self.assertTrue(websub.observe_feed(feed(OLD, NEW, NEW + "-2"), self.path))
        self.assertEqual([event["hub_attempts"] for event in self.state()["pending"]], [1, 0])
        self.assertEqual([event["feed_checks"] for event in self.state()["pending"]], [1, 0])

    def test_public_get_can_observe_feed_without_state_lock_deadlock(self):
        self.queue_new()
        def read_feed(*args, **kwargs):
            self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
            # Another publisher must skip while this request is in progress.
            self.assertFalse(websub.publish_pending(self.path))
            self.assertFalse(restart_module().publish_pending(self.path))
            return response(content=feed(OLD, NEW))
        self.get.side_effect = read_feed
        self.assertTrue(websub.publish_pending(self.path))
        self.post.assert_called_once()

    def test_attempt_is_persisted_before_network_and_success_ack(self):
        self.queue_new()
        def ready(*args, **kwargs):
            event = self.state()["pending"][0]
            self.assertEqual(event["feed_checks"], 1)
            self.assertEqual(event["hub_attempts"], 0)
            self.assertEqual(event["next_attempt_at"], self.now + websub.FEED_RETRY_DELAYS[0])
            return response(content=feed(OLD, NEW))
        def accepted(*args, **kwargs):
            pending = self.state()["pending"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["hub_attempts"], 1)
            self.assertEqual(pending[0]["feed_checks"], 1)
            self.assertEqual(pending[0]["next_attempt_at"], self.now + websub.HUB_RETRY_DELAYS[0])
            return response(204)
        self.get.side_effect = ready
        self.post.side_effect = accepted
        self.assertTrue(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"], [])

    def test_crash_at_last_reserved_attempt_never_resets_budget(self):
        self.queue_new()
        state = self.state()
        state["pending"][0].update(hub_attempts=websub.MAX_HUB_ATTEMPTS, next_attempt_at=0)
        Path(self.path).write_text(json.dumps(state), encoding="utf-8")
        restarted = restart_module()
        self.assertFalse(restarted.publish_pending(self.path))
        self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertEqual(self.state()["pending"], [])
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_state_path_override_and_cache_fallback_are_resolved_at_call_time(self):
        cache_path = str(Path(self.path).parent / "articles.json")
        with patch.dict(os.environ, {"WEBSUB_STATE_PATH": self.path}):
            self.assertEqual(websub.resolve_state_path(cache_path), self.path)
            self.assertFalse(websub.observe_feed(feed(OLD)))
            self.assertTrue(websub.observe_feed(feed(OLD, NEW)))
            self.assertTrue(websub.publish_pending())
        self.assertFalse(Path(cache_path + ".websub.json").exists())
        self.assertEqual(self.state()["observed_guids"], sorted((OLD, NEW)))
        for value in ("", "   "):
            with self.subTest(value=value), patch.dict(os.environ, {"WEBSUB_STATE_PATH": value}), \
                 patch.object(websub, "CACHE_PATH", cache_path):
                self.assertEqual(websub.resolve_state_path(), cache_path + ".websub.json")
                self.assertEqual(websub.resolve_state_path(cache_path), cache_path + ".websub.json")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(websub.resolve_state_path(cache_path), cache_path + ".websub.json")

    def test_public_feed_unavailable_for_hours_preserves_entire_hub_budget(self):
        self.queue_new()
        initial = self.state()["pending"][0].copy()
        self.get.return_value = response(content=feed(OLD))
        while self.now - initial["created_at"] < 8 * 3600:
            self.assertFalse(websub.publish_pending(self.path))
            event = self.state()["pending"][0]
            self.assertEqual(event["hub_attempts"], 0)
            self.assertEqual(event["id"], initial["id"])
            self.assertEqual(event["created_at"], initial["created_at"])
            self.assertEqual(event["expires_at"], initial["expires_at"])
            self.next_retry()
        self.assertGreater(self.state()["pending"][0]["feed_checks"], 3)
        self.post.assert_not_called()
        self.get.return_value = response(content=feed(OLD, NEW))
        self.assertTrue(restart_module().publish_pending(self.path))
        self.assertEqual(self.state()["pending"], [])
        self.assertFalse(restart_module().publish_pending(self.path))
        self.post.assert_called_once()

    def test_hub_outage_longer_than_one_day_recovers_without_unbounded_retries(self):
        self.queue_new()
        started = self.now
        self.post.return_value = response(503)
        while self.now - started < 24 * 3600:
            self.assertFalse(websub.publish_pending(self.path))
            attempts = self.post.call_count
            self.assertFalse(restart_module().publish_pending(self.path))
            self.assertEqual(self.post.call_count, attempts)
            self.next_retry()
        self.assertGreaterEqual(self.now - started, 24 * 3600)
        self.assertLess(self.post.call_count, websub.MAX_HUB_ATTEMPTS)
        self.post.return_value = response(202)
        self.assertTrue(restart_module().publish_pending(self.path))
        self.assertLessEqual(self.post.call_count, websub.MAX_HUB_ATTEMPTS)
        self.assertEqual(self.state()["pending"], [])
        self.assertFalse(restart_module().publish_pending(self.path))

    def test_feed_check_limit_survives_restart_and_never_requeues_guid(self):
        self.queue_new()
        self.get.side_effect = requests.Timeout("Feed temporarily unavailable")
        for check in range(websub.MAX_FEED_CHECKS):
            if check:
                self.next_retry()
            self.assertFalse(restart_module().publish_pending(self.path))
        self.now += websub.EVENT_TTL
        restarted = restart_module()
        for _ in range(3):
            self.assertFalse(restarted.publish_pending(self.path))
            self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertEqual(self.get.call_count, websub.MAX_FEED_CHECKS)
        self.post.assert_not_called()
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(set(self.state()["observed_guids"]), {OLD, NEW})

    def test_pending_event_expires_at_fixed_deadline_without_network_after_restart(self):
        self.queue_new()
        event = self.state()["pending"][0]
        self.assertEqual(event["expires_at"] - event["created_at"], websub.EVENT_TTL)
        self.now = event["expires_at"]
        restarted = restart_module()
        for _ in range(3):
            self.assertFalse(restarted.publish_pending(self.path))
            self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(set(self.state()["observed_guids"]), {OLD, NEW})
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_feed_check_reserved_before_crash_survives_restart(self):
        self.queue_new()
        def crash(*args, **kwargs):
            event = self.state()["pending"][0]
            self.assertEqual(event["feed_checks"], 1)
            self.assertEqual(event["hub_attempts"], 0)
            raise KeyboardInterrupt("Simulated process termination")
        self.get.side_effect = crash
        with self.assertRaises(KeyboardInterrupt):
            websub.publish_pending(self.path)
        self.assertFalse(restart_module().publish_pending(self.path))
        self.get.assert_called_once()
        self.post.assert_not_called()
        self.next_retry()
        self.get.side_effect = None
        self.get.return_value = response(content=feed(OLD, NEW))
        self.assertTrue(restart_module().publish_pending(self.path))
        self.post.assert_called_once()

    def test_hub_attempt_reserved_before_crash_survives_restart(self):
        self.queue_new()
        def crash(*args, **kwargs):
            self.assertEqual(self.state()["pending"][0]["hub_attempts"], 1)
            raise KeyboardInterrupt("Simulated process termination")
        self.post.side_effect = crash
        with self.assertRaises(KeyboardInterrupt):
            websub.publish_pending(self.path)
        self.assertFalse(restart_module().publish_pending(self.path))
        self.post.assert_called_once()
        self.next_retry()
        self.post.side_effect = None
        self.post.return_value = response(204)
        self.assertTrue(restart_module().publish_pending(self.path))
        self.assertEqual(self.post.call_count, 2)

    def test_old_untouched_event_does_not_absorb_new_guid_or_extend_deadline(self):
        self.queue_new()
        original = self.state()["pending"][0].copy()
        self.now += 301
        self.assertTrue(websub.observe_feed(feed(OLD, NEW, NEW + "-later"), self.path))
        pending = self.state()["pending"]
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[0], original)
        self.assertEqual(pending[1]["guids"], [NEW + "-later"])
        self.assertEqual(pending[1]["expires_at"], self.now + websub.EVENT_TTL)

    def test_coalescing_recent_events_keeps_original_creation_and_expiry(self):
        self.queue_new()
        original = self.state()["pending"][0].copy()
        self.now += 60
        self.assertTrue(websub.observe_feed(feed(OLD, NEW, NEW + "-soon"), self.path))
        pending = self.state()["pending"]
        self.assertEqual(len(pending), 1)
        for field in ("id", "created_at", "expires_at"):
            self.assertEqual(pending[0][field], original[field])

    def test_schema_one_pending_event_migrates_once_without_reset_or_rebroadcast(self):
        legacy = {"version": 1, "observed_guids": sorted((OLD, NEW)), "pending": [
            {"id": "existing-event", "guids": [NEW], "attempts": 2, "next_attempt_at": 0}]}
        Path(self.path).write_text(json.dumps(legacy), encoding="utf-8")
        self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
        migrated = self.state()
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["observed_guids"], legacy["observed_guids"])
        event = migrated["pending"][0]
        self.assertEqual(event["id"], "existing-event")
        self.assertEqual(event["guids"], [NEW])
        self.assertEqual(event["hub_attempts"], 2)
        self.assertEqual(event["feed_checks"], 0)
        self.assertEqual(event["created_at"], self.now)
        self.assertEqual(event["expires_at"], self.now + websub.EVENT_TTL)
        self.assertGreaterEqual(event["next_attempt_at"], self.now + websub.HUB_RETRY_DELAYS[1])
        before = Path(self.path).read_bytes()
        self.now += 30
        restarted = restart_module()
        self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(restarted.publish_pending(self.path))
        self.assertEqual(Path(self.path).read_bytes(), before)
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.next_retry()
        self.assertTrue(restarted.publish_pending(self.path))
        self.assertEqual(self.state()["pending"], [])
        self.post.assert_called_once()

    def test_schema_one_removed_events_are_not_resurrected(self):
        legacy = {"version": 1, "observed_guids": sorted((OLD, NEW)), "pending": []}
        Path(self.path).write_text(json.dumps(legacy), encoding="utf-8")
        restarted = restart_module()
        self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(restarted.publish_pending(self.path))
        self.assertEqual(self.state()["version"], 2)
        self.assertEqual(self.state()["pending"], [])
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_legacy_last_reserved_attempt_keeps_consumed_budget_under_new_limit(self):
        legacy = {"version": 1, "observed_guids": sorted((OLD, NEW)), "pending": [
            {"id": "unconfirmed-last-post", "guids": [NEW], "attempts": 3,
             "next_attempt_at": 0}]}
        Path(self.path).write_text(json.dumps(legacy), encoding="utf-8")
        self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
        self.assertEqual(self.state()["pending"][0]["hub_attempts"], 3)
        self.post.return_value = response(503)
        for _ in range(websub.MAX_HUB_ATTEMPTS - 3):
            self.next_retry()
            self.assertFalse(restart_module().publish_pending(self.path))
        self.assertEqual(self.post.call_count, websub.MAX_HUB_ATTEMPTS - 3)
        self.assertEqual(self.state()["pending"], [])
        self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
        self.assertFalse(restart_module().publish_pending(self.path))
        self.assertEqual(self.post.call_count, websub.MAX_HUB_ATTEMPTS - 3)

    def test_schema_one_migration_write_failure_preserves_original_and_prevents_network(self):
        legacy = {"version": 1, "observed_guids": sorted((OLD, NEW)), "pending": [
            {"id": "existing-event", "guids": [NEW], "attempts": 1, "next_attempt_at": 0}]}
        Path(self.path).write_text(json.dumps(legacy), encoding="utf-8")
        before = Path(self.path).read_bytes()
        with patch.object(websub, "atomic_json", side_effect=PermissionError("Read-only state")):
            self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
            self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(Path(self.path).read_bytes(), before)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_nonretryable_hub_responses_are_terminal_and_remain_observed(self):
        for status in (301, 302, 400, 401, 403, 404, 422):
            with self.subTest(status=status):
                self.path = str(Path(self.path).parent / ("terminal-%d.json" % status))
                self.get.reset_mock()
                self.post.reset_mock()
                self.post.return_value = response(status)
                self.queue_new()
                self.assertFalse(websub.publish_pending(self.path))
                self.assertEqual(self.state()["pending"], [])
                self.now += 3600
                self.assertFalse(restart_module().publish_pending(self.path))
                self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
                self.post.assert_called_once()
                self.assertEqual(set(self.state()["observed_guids"]), {OLD, NEW})

    def test_retryable_hub_responses_preserve_pending_budget(self):
        for status in (408, 425, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.path = str(Path(self.path).parent / ("retryable-%d.json" % status))
                self.post.reset_mock()
                self.post.return_value = response(status)
                self.queue_new()
                self.assertFalse(websub.publish_pending(self.path))
                self.assertEqual(self.state()["pending"][0]["hub_attempts"], 1)
                self.assertFalse(restart_module().publish_pending(self.path))
                self.post.assert_called_once()

    def test_retry_after_seconds_and_http_date_extend_but_never_reduce_backoff(self):
        cases = ("1", "7200", format_datetime(datetime.fromtimestamp(self.now + 10800, timezone.utc), usegmt=True),
                 "not-a-delay")
        expected_waits = (websub.HUB_RETRY_DELAYS[0], 7200, 10800, websub.HUB_RETRY_DELAYS[0])
        initial_now = self.now
        for index, (retry_after, wait) in enumerate(zip(cases, expected_waits)):
            with self.subTest(retry_after=retry_after):
                self.now = initial_now
                self.path = str(Path(self.path).parent / ("retry-after-%d.json" % index))
                self.post.reset_mock()
                self.post.return_value = response(429, headers={"Retry-After": retry_after})
                self.queue_new()
                self.assertFalse(websub.publish_pending(self.path))
                self.assertEqual(self.state()["pending"][0]["next_attempt_at"], self.now + wait)
                self.now += wait - 1
                self.assertFalse(restart_module().publish_pending(self.path))
                self.post.assert_called_once()
                self.now += 1
                self.post.return_value = response(204)
                self.assertTrue(restart_module().publish_pending(self.path))
                self.assertEqual(self.post.call_count, 2)

    def test_retry_after_is_clamped_to_event_deadline_without_late_post(self):
        self.queue_new()
        deadline = self.state()["pending"][0]["expires_at"]
        self.post.return_value = response(429, headers={"Retry-After": str(30 * 24 * 3600)})
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"][0]["next_attempt_at"], deadline)
        self.now = deadline
        self.assertFalse(restart_module().publish_pending(self.path))
        self.assertEqual(self.state()["pending"], [])
        self.post.assert_called_once()

    def test_feed_retry_after_does_not_spend_hub_attempts(self):
        self.queue_new()
        self.get.return_value = response(503, headers={"Retry-After": "10800"})
        self.assertFalse(websub.publish_pending(self.path))
        event = self.state()["pending"][0]
        self.assertEqual(event["feed_checks"], 1)
        self.assertEqual(event["hub_attempts"], 0)
        self.assertEqual(event["next_attempt_at"], self.now + 10800)
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
