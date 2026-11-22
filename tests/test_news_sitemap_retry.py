"""Recovery must be bounded, fresh and independent of RSS publication state."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from news_sitemap_retry import PendingNewsNotifications, NEWS_SECONDS

URL = "https://www.artbooms.com/blog/notizia"


class NewsRetryTests(unittest.TestCase):
    def setUp(self):
        self.queue = PendingNewsNotifications()
        self.now = 1795392000.0
        self.tick = 100.0
        self.items = {URL: {"published": datetime.fromtimestamp(self.now - 60, timezone.utc).isoformat()}}

    def observe(self, urls=None, items=None):
        self.queue.observe(urls or [URL], self.items if items is None else items,
                           now=self.now, monotonic=self.tick)

    def dispatch(self, sender, ready=None):
        return self.queue.dispatch(sender, {URL} if ready is None else ready,
                                   now=self.now, monotonic=self.tick)

    def test_failed_notification_recovers_without_another_changed_article(self):
        sender = Mock(side_effect=[False, True])
        self.observe()
        self.assertFalse(self.dispatch(sender))
        self.tick += 119
        self.assertFalse(self.dispatch(sender))
        self.assertEqual(sender.call_count, 1)
        self.tick += 1
        self.assertTrue(self.dispatch(sender))
        self.assertEqual(sender.call_count, 2)
        self.tick += 120
        self.assertFalse(self.dispatch(sender))
        self.assertEqual(sender.call_count, 2)

    def test_repeated_observation_does_not_reset_attempt_budget(self):
        sender = Mock(return_value=False)
        self.observe()
        for _ in range(self.queue.MAX_ATTEMPTS + 1):
            self.observe()
            self.dispatch(sender)
            self.tick += 120
        self.assertEqual(sender.call_count, self.queue.MAX_ATTEMPTS)
        self.assertEqual(self.queue.pending, {})

    def test_expiration_uses_original_publication_and_never_refreshes_date(self):
        self.items[URL]["published"] = datetime.fromtimestamp(self.now - NEWS_SECONDS + 1, timezone.utc).isoformat()
        self.observe()
        before = dict(self.items[URL])
        self.now += 2
        sender = Mock(return_value=True)
        self.dispatch(sender)
        sender.assert_not_called()
        self.assertEqual(self.queue.pending, {})
        self.assertEqual(self.items[URL], before)

    def test_old_future_invalid_and_foreign_articles_do_not_queue(self):
        for published in ("invalid", datetime.fromtimestamp(self.now + 60, timezone.utc).isoformat(),
                          datetime.fromtimestamp(self.now - NEWS_SECONDS, timezone.utc).isoformat()):
            self.observe(items={URL: {"published": published}})
        self.observe(urls=["https://other.example/blog/notizia"])
        self.assertEqual(self.queue.pending, {})

    def test_no_request_before_same_article_is_available_in_local_rss(self):
        self.observe()
        sender = Mock(return_value=True)
        self.assertFalse(self.dispatch(sender, ready=set()))
        sender.assert_not_called()
        self.assertTrue(self.dispatch(sender))

    def test_callback_failure_is_isolated_and_later_attempt_can_succeed(self):
        self.observe()
        sender = Mock(side_effect=[ValueError("unavailable"), True])
        self.assertFalse(self.dispatch(sender))
        self.tick += 120
        self.assertTrue(self.dispatch(sender))

    def test_pending_event_expires_without_spending_more_attempts(self):
        self.observe()
        self.tick += self.queue.MAX_AGE
        sender = Mock(return_value=True)
        self.dispatch(sender)
        sender.assert_not_called()
        self.assertEqual(self.queue.pending, {})

    def test_restart_has_no_implicit_archive_replay(self):
        self.observe()
        restarted = PendingNewsNotifications()
        sender = Mock(return_value=True)
        restarted.dispatch(sender, {URL}, now=self.now, monotonic=self.tick)
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
