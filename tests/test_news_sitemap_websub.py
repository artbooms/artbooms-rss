"""Offline tests: optional XML sitemap topic must never disturb existing RSS WebSub."""
import unittest
import os
from unittest.mock import Mock, patch

import requests

import news_sitemap
import news_sitemap_websub as notifier

URL = "https://www.artbooms.com/blog/notizia-test"
SITEMAP = notifier.NEWS_SITEMAP_URL


def document(*, url=URL, with_news=True):
    news = (
        "<news:news><news:publication>"
        "<news:name>ARTBOOMS</news:name><news:language>it</news:language>"
        "</news:publication><news:publication_date>2026-10-22T09:00:00+02:00</news:publication_date>"
        "<news:title>Notizia</news:title></news:news>"
    ) if with_news else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        f"<url><loc>{url}</loc>{news}</url></urlset>"
    ).encode("utf-8")


class NewsSitemapWebSubTests(unittest.TestCase):
    def test_matching_public_news_xml_sends_second_topic_only(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=200, content=document())) as get, \
             patch.object(notifier.requests, "post", return_value=Mock(
                status_code=204)) as post:
            self.assertTrue(notifier.notify_news_sitemap_topic([URL, URL]))
            get.assert_called_once()
            post.assert_called_once()
            self.assertEqual(post.call_args.args, (notifier.NEWS_SITEMAP_HUB,))
            self.assertEqual(post.call_args.kwargs["data"], {
                "hub.mode": "publish", "hub.url": SITEMAP
            })
            self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_startup_catchup_notifies_existing_recent_article_once(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=200, content=document())) as get, \
             patch.object(notifier.requests, "post", return_value=Mock(
                status_code=204)) as post:
            self.assertTrue(notifier.notify_news_sitemap_topic(None))
            get.assert_called_once()
            post.assert_called_once()

    def test_startup_no_news_entries_does_not_notify(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=200, content=document(with_news=False))), \
             patch.object(notifier.requests, "post") as post:
            self.assertFalse(notifier.notify_news_sitemap_topic(None))
            post.assert_not_called()

    def test_empty_batch_makes_no_requests(self):
        with patch.object(notifier.requests, "get") as get, \
             patch.object(notifier.requests, "post") as post:
            self.assertFalse(notifier.notify_news_sitemap_topic([]))
            get.assert_not_called()
            post.assert_not_called()

    def test_unrelated_url_never_notifies(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=200, content=document())) as get, \
             patch.object(notifier.requests, "post") as post:
            self.assertFalse(notifier.notify_news_sitemap_topic([
                "https://www.artbooms.com/blog/altro"
            ]))
            get.assert_called_once()
            post.assert_not_called()

    def test_missing_news_metadata_never_notifies(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=200, content=document(with_news=False))), \
             patch.object(notifier.requests, "post") as post:
            self.assertFalse(notifier.notify_news_sitemap_topic([URL]))
            post.assert_not_called()

    def test_invalid_xml_never_notifies(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=200, content=b"<invalid")), \
             patch.object(notifier.requests, "post") as post:
            self.assertFalse(notifier.notify_news_sitemap_topic([URL]))
            post.assert_not_called()

    def test_public_sitemap_unavailable_never_notifies(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=503, content=b"")), \
             patch.object(notifier.requests, "post") as post:
            self.assertFalse(notifier.notify_news_sitemap_topic([URL]))
            post.assert_not_called()

    def test_network_timeout_is_isolated(self):
        with patch.object(notifier.requests, "get", side_effect=requests.Timeout()), \
             patch.object(notifier.requests, "post") as post:
            self.assertFalse(notifier.notify_news_sitemap_topic([URL]))
            post.assert_not_called()

    def test_hub_rejection_is_not_a_success(self):
        with patch.object(notifier.requests, "get", return_value=Mock(
                status_code=200, content=document())), \
             patch.object(notifier.requests, "post", return_value=Mock(
                status_code=400)) as post:
            self.assertFalse(notifier.notify_news_sitemap_topic([URL]))
            post.assert_called_once()

    def test_sitemap_advertises_own_topic_and_hub_without_changing_xml(self):
        with patch.dict(os.environ, {"NEWS_SITEMAP_WEBSUB_ENABLED": "1"}):
            response = news_sitemap._xml_response(document().decode("utf-8"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(URL.encode(), response.data)
        links = response.headers.getlist("Link")
        self.assertIn(f'<{SITEMAP}>; rel="self"', links)
        self.assertIn(f'<{notifier.NEWS_SITEMAP_HUB}>; rel="hub"', links)
        self.assertEqual(response.mimetype, "application/xml")


if __name__ == "__main__":
    unittest.main()
