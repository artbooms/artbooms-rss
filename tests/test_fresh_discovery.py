"""Source disagreement and failover; no live HTTP or notification calls."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

import article_parser as parser
import article_processor as processor
from cache_safety import article_hash, atomic_json

BASE = "https://www.artbooms.com"
OLD = BASE + "/blog/padiglione-india-biennale-2026"
NEW = BASE + "/blog/record-van-gogh"
ARCHIVE = ('<li class="archive-item"><a href="%s">India</a>'
           '<span class="archive-item-date-after">Nov 17, 2026</span></li>') % OLD
RECENT = ('<article class="hentry"><h1 class="entry-title">'
          '<a href="/blog/record-van-gogh" data-content-field="title">Van Gogh</a>'
          '</h1></article><article><h1 class="entry-title">'
          '<a href="%s">India</a></h1></article>') % OLD


class RecentLinkTests(unittest.TestCase):
    def test_native_headings_keep_order_without_reading_body_or_sidebar_links(self):
        html = RECENT + ('<aside><a href="/blog/sidebar">Sidebar</a></aside>'
                         '<article><p><a href="/blog/body">Body</a></p></article>')
        self.assertEqual(parser.extract_recent_article_links_from_blog_html(html, BASE),
                         [NEW, OLD])

    def test_reject_external_hosts_credentials_queries_and_non_article_headings(self):
        urls = ["https://example.org/blog/other", "https://www.artbooms.com.evil/blog/x",
                "https://name@www.artbooms.com/blog/x", "/blog/tag/arte",
                "/blog/category/arte", "/blog/x?offset=1", "/blog/x#section",
                "javascript:alert(1)", "https://www.artbooms.com:invalid/blog/x"]
        html = "".join('<article><h1 class="entry-title"><a href="%s">X</a></h1></article>'
                       % url for url in urls)
        self.assertEqual(parser.extract_recent_article_links_from_blog_html(html, BASE), [])

    def test_deduplicate_normalized_headings(self):
        html = RECENT + '<article><h1 class="entry-title"><a href="%s/">Copy</a></h1></article>' % NEW
        self.assertEqual(parser.extract_recent_article_links_from_blog_html(html, BASE), [NEW, OLD])


class DiscoverySourcesTests(unittest.TestCase):
    def setUp(self):
        self.block_network = patch.object(requests.Session, "request",
                                          side_effect=AssertionError("Unexpected live HTTP"))
        self.block_network.start()
        self.addCleanup(self.block_network.stop)

    def test_stale_archive_and_fresh_collection_are_united_oldest_first(self):
        def fetch(url, **kwargs):
            return ARCHIVE if url == processor.ARCHIVE_URL else RECENT
        with patch.object(processor, "fetch_html", side_effect=fetch) as source:
            self.assertEqual(processor._scan_archive(), [OLD, NEW])
        self.assertEqual(source.call_count, 2)

    def test_collection_timeout_keeps_archive(self):
        def fetch(url, **kwargs):
            if url != processor.ARCHIVE_URL:
                raise requests.Timeout("Collection timeout")
            return ARCHIVE
        with patch.object(processor, "fetch_html", side_effect=fetch):
            self.assertEqual(processor._scan_archive(), [OLD])

    def test_archive_timeout_still_discovers_recent_articles(self):
        def fetch(url, **kwargs):
            if url == processor.ARCHIVE_URL:
                raise requests.Timeout("Archive timeout")
            return RECENT
        with patch.object(processor, "fetch_html", side_effect=fetch):
            self.assertEqual(processor._scan_archive(), [OLD, NEW])

    def test_two_failed_sources_do_not_invent_an_empty_success(self):
        with patch.object(processor, "fetch_html", side_effect=requests.Timeout("Offline")):
            with self.assertRaises(requests.Timeout):
                processor._scan_archive()

    def test_archive_already_has_latest_does_not_duplicate_or_reorder_it(self):
        with patch.object(processor, "fetch_html", return_value=RECENT), \
             patch.object(processor, "extract_article_links_from_archive_html", return_value=[OLD, NEW]):
            self.assertEqual(processor._scan_archive(), [OLD, NEW])

    def test_recent_discovery_can_be_disabled_for_rollback(self):
        with patch.object(processor, "RECENT_URL", ""), \
             patch.object(processor, "fetch_html", return_value=ARCHIVE) as source:
            self.assertEqual(processor._scan_archive(), [OLD])
            source.assert_called_once()

    def test_archive_regression_never_drops_a_discovered_post_or_changes_dates(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "cache.json")
            old = dict(url=OLD, title="India", author="natasha barbieri",
                       published="2026-11-17T10:54:00+01:00", modified=None,
                       description="India", image=None, _fetched_at="2026-11-17T09:57:00Z")
            new = dict(old, url=NEW, title="Van Gogh", published="2026-11-19T04:25:14+01:00")
            old["_hash"] = article_hash(old)
            atomic_json(path, dict(items={OLD: old}, cursor=0, links_hash="old",
                                   last_scan="2026-11-19T02:00:00Z"))
            def fetch(url, **kwargs):
                return ARCHIVE if url == processor.ARCHIVE_URL else RECENT
            def parse(url, **kwargs):
                return copy.deepcopy(new if url == NEW else old)
            with patch.object(processor, "CACHE_PATH", path), \
                 patch.object(processor, "fetch_html", side_effect=fetch), \
                 patch.object(processor, "parse_article", side_effect=parse), \
                 patch.object(processor.time, "sleep"):
                changed, _ = processor.generate_items()
                self.assertEqual([x["url"] for x in changed], [NEW])
                self.assertEqual(changed[0]["published"], new["published"])
                with patch.object(processor, "RECENT_URL", ""):
                    processor.generate_items()
                import json
                saved = json.loads(Path(path).read_text())
                self.assertEqual(set(saved["items"]), {OLD, NEW})
                self.assertEqual(saved["items"][NEW]["published"], new["published"])

