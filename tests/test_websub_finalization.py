"""Regression checks for idle reads, transient transport and the XML opt-in.

Production transitions use the existing Git Data fake. No external socket or
production state is accessed, and publication checks retain the real ledger.
"""
import copy
from contextlib import ExitStack
import logging
import os
import socket
import unittest
from unittest.mock import Mock, patch

import requests

import app
import news_sitemap
import news_sitemap_websub as news_notifier
import websub_durable as durable
import websub_store as api
from test_news_sitemap_websub import URL, document
from test_websub import OLD, NEW, feed
from test_websub_github import (BRANCH, REPO, STATE_PATH, TOKEN, FakeGitHubSession,
                               initial_document)


class IdlePublisherTests(unittest.TestCase):
    def setUp(self):
        previous_logging = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, previous_logging)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket.socket, "connect",
            side_effect=AssertionError("External network forbidden")))
        self.fake = FakeGitHubSession(initial_document())
        self.store = api.GitHubStore(REPO, BRANCH, STATE_PATH, TOKEN, session=self.fake)
        self.now = 1000.0
        self.stack.enter_context(patch.object(durable, "_store_from_env", return_value=self.store))
        self.stack.enter_context(patch.object(durable, "_retry_not_before", 0))
        self.stack.enter_context(patch.object(durable, "_idle_checkpoint", None))
        self.public = self.stack.enter_context(patch.object(durable.legacy, "_public_feed_ready"))
        self.post = self.stack.enter_context(patch.object(durable.requests, "post",
            return_value=Mock(status_code=204, headers={})))

    def run_cycle(self, *urls):
        return durable.run(feed(*(urls or (OLD,))), now=lambda: self.now)

    def test_idle_feed_skips_ref_reads_but_refreshes_checkpoint(self):
        self.assertFalse(self.run_cycle())
        initial_calls = len(self.fake.calls)
        for elapsed in (120, 240, 360, 480, 599):
            self.now = 1000 + elapsed
            self.assertFalse(self.run_cycle())
            self.assertEqual(len(self.fake.calls), initial_calls)
        self.now = 1600
        self.assertFalse(self.run_cycle())
        self.assertEqual(len(self.fake.calls), initial_calls + 1)
        self.assertEqual(self.fake.writes, [])
        self.post.assert_not_called()

    def test_new_article_bypasses_idle_and_is_acknowledged_once(self):
        self.run_cycle()
        self.now += 1
        self.assertTrue(self.run_cycle(OLD, NEW))
        state = self.fake.document()
        self.assertEqual(set(state["state"]["observed_guids"]), {OLD, NEW})
        self.assertEqual(state["state"]["pending"], [])
        receipt = next(iter(state["completed"].values()))
        self.assertEqual((receipt["status"], receipt["guids"]), ("accepted", [NEW]))
        self.assertEqual((receipt["hub_attempts"], receipt["feed_checks"]), (1, 1))
        self.run_cycle(OLD, NEW)
        self.now += 120
        self.run_cycle(OLD, NEW)
        self.post.assert_called_once()

    def test_pending_is_not_throttled_and_original_budget_survives(self):
        self.post.return_value = Mock(status_code=503, headers={})
        self.run_cycle(OLD, NEW)
        pending = copy.deepcopy(self.fake.document()["state"]["pending"])
        calls = len(self.fake.calls)
        self.now += 120
        self.run_cycle(OLD, NEW)
        self.assertGreater(len(self.fake.calls), calls)
        self.assertEqual(self.fake.document()["state"]["pending"], pending)
        self.assertIsNone(durable._idle_checkpoint)
        self.post.assert_called_once()
        self.now = pending[0]["next_attempt_at"]
        self.post.return_value = Mock(status_code=204, headers={})
        self.assertTrue(self.run_cycle(OLD, NEW))
        receipt = next(iter(self.fake.document()["completed"].values()))
        self.assertEqual(receipt["expires_at"], pending[0]["expires_at"])
        self.assertEqual(receipt["hub_attempts"], 2)

    def test_remote_pause_blocks_new_article_and_resume_is_immediate(self):
        self.run_cycle()
        remote = self.fake.document()
        remote["mode"] = "paused"
        self.fake.seed(BRANCH, {STATE_PATH: self.fake.canonical(remote)})
        self.now += 1
        self.assertFalse(self.run_cycle(OLD, NEW))
        self.post.assert_not_called()
        self.assertIsNone(durable._idle_checkpoint)
        self.assertEqual(self.fake.document()["state"]["observed_guids"], [OLD])
        remote["mode"] = "active"
        self.fake.seed(BRANCH, {STATE_PATH: self.fake.canonical(remote)})
        self.now += 1
        self.assertTrue(self.run_cycle(OLD, NEW))
        self.post.assert_called_once()

    def test_error_discards_idle_checkpoint_then_recovers_new_article(self):
        self.run_cycle()
        self.now += 600
        self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH, status=503)
        self.assertFalse(self.run_cycle())
        self.assertIsNone(durable._idle_checkpoint)
        self.assertEqual(self.fake.document()["state"]["observed_guids"], [OLD])
        self.now += 120
        self.assertTrue(self.run_cycle(OLD, NEW))
        self.post.assert_called_once()

    def test_restart_reloads_authoritative_accepted_receipt_without_replay(self):
        self.assertTrue(self.run_cycle(OLD, NEW))
        before = self.fake.document()
        durable._idle_checkpoint = None
        replacement = api.GitHubStore(REPO, BRANCH, STATE_PATH, TOKEN, session=self.fake)
        with patch.object(durable, "_store_from_env", return_value=replacement):
            self.run_cycle(OLD, NEW)
        self.assertEqual(self.fake.document(), before)
        self.post.assert_called_once()

    def test_diagnostics_classify_error_without_logging_secret_text(self):
        with patch.object(self.store, "load", side_effect=api.Unavailable(
                "Must not log " + TOKEN, operation="GET:ref", reason="timeout")), \
                self.assertLogs("websub", level="WARNING") as messages:
            self.assertFalse(self.run_cycle())
        text = " ".join(messages.output)
        self.assertIn("GET:ref", text)
        self.assertIn("timeout", text)
        self.assertNotIn(TOKEN, text)
        self.post.assert_not_called()


class GitHubReadRecoveryTests(unittest.TestCase):
    def setUp(self):
        previous_logging = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, previous_logging)
        self.guard = patch.object(socket.socket, "connect",
            side_effect=AssertionError("External network forbidden"))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.fake = FakeGitHubSession(initial_document())
        self.store = api.GitHubStore(REPO, BRANCH, STATE_PATH, TOKEN, session=self.fake)

    def test_one_connection_error_recovers_without_any_write(self):
        self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH,
                              exception=requests.ConnectionError(TOKEN))
        with self.assertLogs("websub", level="WARNING") as messages:
            self.assertEqual(self.store.load().document, initial_document())
        self.assertNotIn(TOKEN, " ".join(messages.output))
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(len([c for c in self.fake.calls if c[1].startswith("/git/ref/")]), 2)

    def test_two_timeouts_stop_read_without_mutating_remote_state(self):
        for _ in range(2):
            self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH,
                                  exception=requests.Timeout(TOKEN))
        with self.assertRaises(api.Unavailable) as raised:
            self.store.load()
        self.assertEqual(len(self.fake.calls), 2)
        self.assertEqual((raised.exception.operation, raised.exception.reason), ("GET:ref", "timeout"))
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertEqual(self.fake.document(), initial_document())
        self.assertEqual(self.fake.writes, [])

    def test_tls_failure_does_not_retry_or_weaken_validation(self):
        self.fake.queue_fault("GET", "/git/ref/heads/" + BRANCH,
                              exception=requests.exceptions.SSLError(TOKEN))
        with self.assertRaises(api.Unavailable) as raised:
            self.store.load()
        self.assertEqual(raised.exception.reason, "tls")
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.fake.writes, [])


class NewsExperimentTests(unittest.TestCase):
    def test_default_disables_all_experimental_requests_and_hub_advertisement(self):
        with patch.dict(os.environ, {"NEWS_SITEMAP_WEBSUB_ENABLED": "0"}), \
                patch.object(app, "notify_news_sitemap_topic") as notifier:
            app._notify_news_sitemap([URL], True)
            response = news_sitemap._xml_response(document().decode())
        notifier.assert_not_called()
        self.assertNotIn(f'<{news_notifier.NEWS_SITEMAP_HUB}>; rel="hub"',
                         response.headers.getlist("Link"))
        self.assertEqual(response.data, document())

    def test_opt_in_only_notifies_changed_ready_content_never_startup(self):
        with patch.dict(os.environ, {"NEWS_SITEMAP_WEBSUB_ENABLED": "1"}), \
                patch.object(app, "notify_news_sitemap_topic") as notifier:
            app._notify_news_sitemap([], False)
            app._notify_news_sitemap([], True)
            app._notify_news_sitemap([URL], False)
            notifier.assert_not_called()
            app._notify_news_sitemap([URL], True)
            notifier.assert_called_once_with([URL])

    def test_missing_required_metadata_closes_get_and_never_posts(self):
        for tag in ("news:name", "news:language", "news:publication_date", "news:title"):
            with self.subTest(tag=tag):
                import re
                body = re.sub(("<" + tag + ">.*?</" + tag + ">").encode(), b"", document())
                response = Mock(status_code=200, content=body)
                with patch.object(news_notifier.requests, "get", return_value=response), \
                        patch.object(news_notifier.requests, "post") as post:
                    self.assertFalse(news_notifier.notify_news_sitemap_topic([URL]))
                    post.assert_not_called()
                    response.close.assert_called_once()

    def test_malformed_publication_date_never_posts(self):
        for value in (b"not-a-date", b"2026-10-22T09:00:00"):
            response = Mock(status_code=200, content=document().replace(
                b"2026-10-22T09:00:00+02:00", value))
            with patch.object(news_notifier.requests, "get", return_value=response), \
                    patch.object(news_notifier.requests, "post") as post:
                self.assertFalse(news_notifier.notify_news_sitemap_topic([URL]))
                post.assert_not_called()
                response.close.assert_called_once()

    def test_get_and_post_are_closed_after_accepted_experimental_notification(self):
        response = Mock(status_code=200, content=document())
        accepted = Mock(status_code=204)
        with patch.object(news_notifier.requests, "get", return_value=response), \
                patch.object(news_notifier.requests, "post", return_value=accepted):
            self.assertTrue(news_notifier.notify_news_sitemap_topic([URL]))
        response.close.assert_called_once()
        accepted.close.assert_called_once()

    def test_health_exposes_only_valid_render_commit_without_changing_json(self):
        with app.app.test_client() as client:
            for revision in ("6" * 40, "not-a-commit"):
                with patch.dict(os.environ, {"RENDER_GIT_COMMIT": revision}):
                    response = client.get("/healthz")
                self.assertEqual(response.json, {"ok": True, "service": "artbooms-rss"})
                self.assertEqual(response.headers.get("X-Artbooms-Revision"),
                                 revision if len(revision) == 40 else None)


if __name__ == "__main__":
    unittest.main()
