"""Targeted scheduler cases missing from the earlier 67-test delivery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import article_processor as processor
import cache_safety as safety


def article(url, title="Original"):
    item = dict(url=url, title=title, author="Artbooms", description="Description",
                published="2026-10-01T08:00:00+00:00", modified=None, image=None,
                _fetched_at="2026-10-01T08:00:00+00:00")
    item["_hash"] = safety.article_hash(item)
    return item


class SchedulerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cache.json"
        self.old = "https://www.artbooms.com/blog/existing"
        self.missing = ["https://www.artbooms.com/blog/new-%s" % index for index in range(4)]
        self.links = [self.old] + self.missing
        initial = dict(items={self.old: article(self.old)}, cursor=0,
                       last_scan="2026-10-01T08:00:00+00:00", links_hash="previous-archive")
        safety.atomic_json(self.path, initial)
        self.patches = [patch.object(processor, "CACHE_PATH", str(self.path)),
                        patch.object(processor, "MAX_BATCH", 3),
                        patch.object(processor, "_scan_archive", return_value=self.links),
                        patch.object(processor.time, "sleep")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def data(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def parse(self, url, **kwargs):
        if url in self.missing[1:]:
            return dict(url=url, title=None, author=None, published=None)
        return article(url, title="Updated" if url == self.old else "New article")

    def test_persistent_newest_failures_do_not_starve_earlier_new_article(self):
        with patch.object(processor, "parse_article", side_effect=self.parse):
            processor.generate_items()
            processor.generate_items()
        self.assertIn(self.missing[0], self.data()["items"])
        for url in self.missing[1:]:
            self.assertNotIn(url, self.data()["items"])

    def test_persistent_newest_failures_do_not_block_existing_updates(self):
        with patch.object(processor, "parse_article", side_effect=self.parse):
            processor.generate_items()
        self.assertEqual(self.data()["items"][self.old]["title"], "Updated")

    def test_partial_failure_batch_is_bounded_unique_and_advances_cursor(self):
        with patch.object(processor, "parse_article", side_effect=self.parse) as parser:
            processor.generate_items()
        urls = [call.args[0] for call in parser.call_args_list]
        self.assertEqual(len(urls), 3)
        self.assertEqual(len(set(urls)), 3)
        self.assertIn(self.missing[-1], urls)
        self.assertIn(self.missing[-2], urls)
        self.assertNotEqual(self.data()["cursor"], 0)

    def test_single_slot_prioritizes_changed_archive_once_then_rotates(self):
        with patch.object(processor, "MAX_BATCH", 1), patch.object(processor, "parse_article", side_effect=self.parse) as parser:
            processor.generate_items()
            processor.generate_items()
            processor.generate_items()
        urls = [call.args[0] for call in parser.call_args_list]
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[0], self.missing[-1])
        self.assertIn(self.old, urls[1:])
        self.assertEqual(self.data()["items"][self.old]["title"], "Updated")

    def test_no_missing_keeps_original_batch_budget_and_wraps(self):
        data = self.data()
        data["items"].update({url: article(url) for url in self.missing})
        data["cursor"] = len(self.links) - 1
        safety.atomic_json(self.path, data)
        with patch.object(processor, "parse_article", side_effect=lambda url, **kw: article(url)) as parser:
            processor.generate_items()
        self.assertEqual([call.args[0] for call in parser.call_args_list], [self.links[-1]])
        self.assertEqual(self.data()["cursor"], 0)




class PartialCacheReviewTests(unittest.TestCase):
    def fixture(self):
        url = "https://www.artbooms.com/blog/existing"
        return dict(items={url: article(url), "broken": None}, cursor=0,
                    last_scan="2026-10-01T08:00:00+00:00", links_hash="archive-old")

    def scanned(self, data):
        result = json.loads(json.dumps(data))
        result.update(cursor=1, last_scan="2026-10-02T08:00:00+00:00", links_hash="archive-new")
        return result

    def test_unchanged_invalid_record_does_not_freeze_scan_cursor(self):
        current = self.fixture()
        merged, stats = safety.merge_cache(current, self.scanned(current))
        self.assertEqual(merged["cursor"], 1)
        self.assertEqual(merged["links_hash"], "archive-new")
        self.assertEqual(merged["last_scan"], "2026-10-02T08:00:00+00:00")
        self.assertIsNone(merged["items"]["broken"])
        self.assertEqual(stats["invalid"], 1)

    def test_new_invalid_record_keeps_scan_metadata_untrusted(self):
        current = self.fixture()
        del current["items"]["broken"]
        incoming = self.scanned(current)
        incoming["items"]["broken"] = None
        merged, stats = safety.merge_cache(current, incoming)
        self.assertEqual(merged, current)
        self.assertEqual(stats["invalid"], 1)

    def test_changed_invalid_record_keeps_scan_metadata_untrusted(self):
        current = self.fixture()
        incoming = self.scanned(current)
        incoming["items"]["broken"] = {"url": "broken", "title": "Still incomplete"}
        merged, stats = safety.merge_cache(current, incoming)
        self.assertEqual(merged, current)
        self.assertEqual(stats["invalid"], 1)

    def test_entirely_invalid_unchanged_candidate_still_fails(self):
        current = self.fixture()
        current["items"] = {"broken": None}
        with self.assertRaises(ValueError):
            safety.merge_cache(current, self.scanned(current))

if __name__ == "__main__":
    unittest.main(verbosity=2)
