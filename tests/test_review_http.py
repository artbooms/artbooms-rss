"""Extra HTTP and sitemap checks absent from the original 67 tests.
The Windows fcntl shim fails if used: these do not exercise Linux leadership.
Actual cache transactions use real Windows locking.
"""
import copy
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.name == "nt" and "fcntl" not in sys.modules:
    shim = types.ModuleType("fcntl")
    shim.LOCK_EX, shim.LOCK_NB, shim.LOCK_UN = 2, 4, 8
    def unsupported_leader(*args, **kwargs):
        raise AssertionError("Linux leader invoked in an HTTP-only Windows test")
    shim.flock = unsupported_leader
    sys.modules["fcntl"] = shim
import app
import cache_safety as cs
import news_sitemap as news

logging.disable(logging.CRITICAL)
ITEM = dict(url="https://www.artbooms.com/blog/review-http", title="Arte & museo",
            description="Una descrizione", author="natasha barbieri",
            image="https://example.org/image.jpg", published="2026-10-01T10:00:00+02:00",
            modified="2026-10-01T10:00:00+02:00", _fetched_at="2026-10-02T08:00:00Z")
ITEM["_hash"] = cs.article_hash(ITEM)

def cache(*items):
    return {"items": {i["url"]: copy.deepcopy(i) for i in (items or (ITEM,))},
            "cursor": 0, "last_scan": "2026-10-02T08:00:00Z", "links_hash": "fixture"}

class HttpReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.cache, self.feed = self.folder / "articles_cache.json", self.folder / "feed.xml"
        self.patches = [patch.object(app, "CACHE_PATH", str(self.cache)),
                        patch.object(app, "FEED_PATH", str(self.feed)),
                        patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"})]
        for p in self.patches: p.start()
        self.addCleanup(self.cleanup)
        app._snapshot = app._source_token = app._source_digest = None
        cs.atomic_json(self.cache, cache())
        self.client = app.app.test_client()

    def cleanup(self):
        app._snapshot = app._source_token = app._source_digest = None
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def test_weak_etag_list_and_wildcard_allow_revalidation(self):
        first = self.client.get("/rss")
        tag = first.headers["ETag"]
        for header in ("W/" + tag, '"unrelated", ' + tag, "*"):
            with self.subTest(header=header):
                response = self.client.get("/rss", headers={"If-None-Match": header})
                self.assertEqual(response.status_code, 304)
                self.assertEqual(response.data, b"")
                self.assertEqual(response.headers["ETag"], tag)
                self.assertIn("must-revalidate", response.headers["Cache-Control"])

    def test_changed_article_replaces_etag_and_preserves_guid_and_date(self):
        first = self.client.get("/rss")
        changed = dict(ITEM, title="Titolo corretto", _fetched_at="2026-10-03T10:00:00Z")
        changed["_hash"] = cs.article_hash(changed)
        cs.atomic_json(self.cache, cache(changed))
        response = self.client.get("/rss", headers={"If-None-Match": first.headers["ETag"]})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers["ETag"], first.headers["ETag"])
        self.assertIn(b"Titolo corretto", response.data)
        root, old = ET.fromstring(response.data), ET.fromstring(first.data)
        self.assertEqual(root.findtext("./channel/item/guid"), ITEM["url"])
        self.assertEqual(root.findtext("./channel/item/pubDate"), old.findtext("./channel/item/pubDate"))

    def test_last_modified_cannot_hide_changed_feed_when_etag_misses(self):
        self.client.get("/rss")
        cs.atomic_json(self.cache, cache(dict(ITEM, description="Testo cambiato")))
        response = self.client.get("/rss", headers={"If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT",
                                                    "If-None-Match": '"stale-validator"'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Testo cambiato", response.data)

    def test_corrupt_feed_is_repaired_from_last_valid_snapshot(self):
        first = self.client.get("/rss")
        self.feed.write_bytes(b"<rss><broken>")
        response = self.client.get("/rss")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, first.data)
        self.assertEqual(response.headers["ETag"], first.headers["ETag"])
        self.assertEqual(self.feed.read_bytes(), first.data)

    def test_no_cache_and_no_xml_fail_startup_and_return_retryable_503(self):
        self.cache.unlink()
        with self.assertRaises(RuntimeError): app.start_worker()
        response = self.client.get("/rss")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Retry-After"], "120")
        self.assertNotIn("ETag", response.headers)

    def test_cache_download_serves_latest_complete_json_without_conditional_304(self):
        first = self.client.get("/cache/download")
        tag = first.headers["ETag"]
        first.close()
        cs.atomic_json(self.cache, cache(dict(ITEM, title="Cache corrente")))
        response = self.client.get("/cache/download", headers={"If-None-Match": tag})
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.data)["items"][ITEM["url"]]["title"], "Cache corrente")
            self.assertIn("must-revalidate", response.headers["Cache-Control"])
        finally:
            response.close()

    def test_news_route_uses_fresh_local_cache_before_github_persistence(self):
        fresh = dict(ITEM, published=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
        cs.atomic_json(self.cache, cache(fresh))
        with patch.object(news, "LOCAL_CACHE_PATH", str(self.cache)), \
             patch.object(news.requests, "get", side_effect=AssertionError("Unexpected GitHub read")) as remote:
            response = self.client.get("/news-sitemap.xml")
        self.assertEqual(response.status_code, 200)
        remote.assert_not_called()
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
              "n": "http://www.google.com/schemas/sitemap-news/0.9"}
        root = ET.fromstring(response.data)
        self.assertEqual([n.text for n in root.findall("s:url/s:loc", ns)], [fresh["url"]])
        self.assertEqual(root.findtext("s:url/n:news/n:publication_date", namespaces=ns),
                         datetime.fromisoformat(fresh["published"]).replace(microsecond=0).isoformat())

class SitemapReviewTests(unittest.TestCase):
    def render(self, items, instant):
        class FixedDateTime(datetime):
            @classmethod
            def utcnow(cls): return instant.replace(tzinfo=None)
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)
        with patch.object(news.datetime, "datetime", FixedDateTime),              patch.object(news, "_load_local_items", return_value=items),              patch.object(news, "_load_remote_items", return_value=items):
            return news.news_sitemap_view()

    def test_exact_48h_boundary_is_not_news_but_one_second_inside_is(self):
        instant = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        boundary = dict(ITEM, url=ITEM["url"] + "-old", published=(instant - timedelta(hours=48)).isoformat())
        inside = dict(ITEM, url=ITEM["url"] + "-recent",
                      published=(instant - timedelta(hours=48) + timedelta(seconds=1)).isoformat())
        root = ET.fromstring(self.render([boundary, inside], instant).data)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
              "n": "http://www.google.com/schemas/sitemap-news/0.9"}
        self.assertEqual([n.text for n in root.findall("s:url/s:loc", ns)], [inside["url"]])
        self.assertEqual(len(root.findall("s:url/n:news", ns)), 1)

    def test_sitemap_limits_news_to_newest_1000_with_deterministic_ties(self):
        instant = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        items = [dict(ITEM, url=ITEM["url"] + f"-{i:04d}",
                      published=(instant - timedelta(minutes=i // 2 + 1)).isoformat()) for i in range(1005)]
        root = ET.fromstring(self.render(list(reversed(items)), instant).data)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
              "n": "http://www.google.com/schemas/sitemap-news/0.9"}
        self.assertEqual(len(root.findall("s:url/n:news", ns)), 1000)
        self.assertEqual([n.text for n in root.findall("s:url/s:loc", ns)], [i["url"] for i in items[:1000]])

if __name__ == "__main__":
    unittest.main(verbosity=2)

