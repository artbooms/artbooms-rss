"""Offline WebSub state, readiness, retry and restart guarantees."""
import importlib.util
import json
import logging
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


def response(status=200, content=b""):
    return Mock(status_code=status, content=content)


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
        self.now += 5
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

    def test_hub_failure_has_three_persisted_attempts_then_stops(self):
        self.queue_new()
        self.post.return_value = response(503)
        for delay in (0, 5, 30):
            self.now += delay
            self.assertFalse(websub.publish_pending(self.path))
        self.now += 500
        self.assertFalse(websub.publish_pending(self.path))
        self.assertFalse(websub.observe_feed(feed(OLD, NEW), self.path))
        self.assertEqual(self.get.call_count, 3)
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(set(self.state()["observed_guids"]), {OLD, NEW})

    def test_network_timeout_does_not_escape_or_repeat_before_retry_time(self):
        self.queue_new()
        self.post.side_effect = requests.Timeout("Hub unavailable")
        self.assertFalse(websub.publish_pending(self.path))
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"][0]["attempts"], 1)
        self.post.assert_called_once()
        self.now += 5
        self.get.side_effect = requests.Timeout("Public RSS unavailable")
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"][0]["attempts"], 2)
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
                for delay in (0, 5, 30):
                    self.now += delay
                    self.assertFalse(websub.publish_pending(self.path))
                self.assertEqual(self.state()["pending"], [])
        self.post.assert_not_called()

    def test_hub_redirect_is_failure_not_success(self):
        self.queue_new()
        self.post.return_value = response(302)
        self.assertFalse(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"][0]["attempts"], 1)

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
        self.assertEqual([event["attempts"] for event in self.state()["pending"]], [1, 0])

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
        def accepted(*args, **kwargs):
            pending = self.state()["pending"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["attempts"], 1)
            self.assertEqual(pending[0]["next_attempt_at"], 1005.0)
            return response(204)
        self.post.side_effect = accepted
        self.assertTrue(websub.publish_pending(self.path))
        self.assertEqual(self.state()["pending"], [])

    def test_crash_at_last_reserved_attempt_never_resets_budget(self):
        self.queue_new()
        state = self.state()
        state["pending"][0].update(attempts=3, next_attempt_at=0)
        Path(self.path).write_text(json.dumps(state), encoding="utf-8")
        restarted = restart_module()
        self.assertFalse(restarted.publish_pending(self.path))
        self.assertFalse(restarted.observe_feed(feed(OLD, NEW), self.path))
        self.assertEqual(self.state()["pending"], [])
        self.get.assert_not_called()
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
