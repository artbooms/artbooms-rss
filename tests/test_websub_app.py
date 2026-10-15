"""Offline integration of ingestion, atomic RSS publication and WebSub state."""
import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.name == "nt" and "fcntl" not in sys.modules:
    shim = types.ModuleType("fcntl")
    shim.LOCK_EX, shim.LOCK_NB, shim.LOCK_UN = 2, 4, 8
    def no_native_leader(*args, **kwargs):
        raise AssertionError("Linux background leadership is not used by Flask tests")
    shim.flock = no_native_leader
    sys.modules["fcntl"] = shim

with patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}), \
     patch.object(requests.Session, "request", side_effect=AssertionError("Network during import")):
    import app
    import article_processor as processor
    import cache_safety
    import news_sitemap
    import websub


def fresh_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


class WebSubApplicationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.cache_path = Path(temporary) / "cache.json"
        self.feed_path = Path(temporary) / "feed.xml"
        self.state_path = str(self.cache_path) + ".websub.json"
        self.stack.enter_context(patch.dict(os.environ, {
            "CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}))
        self.stack.enter_context(patch.object(app, "CACHE_PATH", str(self.cache_path)))
        self.stack.enter_context(patch.object(app, "FEED_PATH", str(self.feed_path)))
        self.stack.enter_context(patch.object(processor, "CACHE_PATH", str(self.cache_path)))
        self.stack.enter_context(patch.object(processor, "MAX_BATCH", 3))
        self.stack.enter_context(patch.object(news_sitemap, "LOCAL_CACHE_PATH", str(self.cache_path)))
        self.stack.enter_context(patch.object(news_sitemap, "LOCAL_FIRST", True))
        self.stack.enter_context(patch.object(requests.Session, "request",
                                             side_effect=AssertionError("Unexpected network")))
        self.now = 1000.0
        self.stack.enter_context(patch.object(websub.time, "time", side_effect=lambda: self.now))
        self.public_override = None
        self.public = self.stack.enter_context(patch.object(websub.requests, "get", side_effect=self.public_feed))
        self.hub = self.stack.enter_context(patch.object(websub.requests, "post", return_value=Mock(status_code=204)))
        app._snapshot = app._source_token = app._source_digest = None
        self.addCleanup(self.reset_application)
        instant = datetime.now(timezone.utc)
        self.old = dict(url="https://www.artbooms.com/blog/websub-old", title="Articolo precedente",
                        description="Archivio", author="natasha barbieri", image="https://example.org/old.jpg",
                        published=(instant - timedelta(days=3)).isoformat())
        self.old["_hash"] = cache_safety.article_hash(self.old)
        self.new = dict(url="https://www.artbooms.com/blog/websub-new", title="Nuovo articolo",
                        description="Nuova pubblicazione", author="natasha barbieri",
                        image="https://example.org/new.jpg", published=(instant - timedelta(hours=1)).isoformat())
        cache_safety.atomic_json(str(self.cache_path), {
            "items": {self.old["url"]: copy.deepcopy(self.old)}, "cursor": 7,
            "last_scan": (instant - timedelta(hours=3)).isoformat(), "links_hash": "baseline"})
        self.client = app.app.test_client()
        app.start_worker()
        self.baseline = self.client.get("/rss")
        self.assertEqual(self.baseline.status_code, 200)
        self.assertEqual(self.state()["pending"], [])
        self.public.assert_not_called()
        self.hub.assert_not_called()

    def reset_application(self):
        app._snapshot = app._source_token = app._source_digest = None
        for name in ("_websub_fresh_app", "_websub_fresh_state"):
            sys.modules.pop(name, None)

    def state(self):
        return json.loads(Path(self.state_path).read_text(encoding="utf-8"))

    def public_feed(self, url, **kwargs):
        self.assertEqual(url, websub.FEED_SELF_URL)
        # Exercise the real RSS endpoint inside readiness: it must not deadlock
        # with a state transaction or accidentally publish from a request.
        result = self.client.get("/rss")
        self.assertEqual(result.status_code, 200)
        content = self.public_override if self.public_override is not None else result.data
        return Mock(status_code=200, content=content)

    def ingest(self):
        def parse(url, session=None):
            self.assertIn(url, (self.old["url"], self.new["url"]))
            return copy.deepcopy(self.new if url == self.new["url"] else self.old)
        with patch.object(processor, "_scan_archive", return_value=[self.old["url"], self.new["url"]]), \
             patch.object(processor, "parse_article", side_effect=parse), \
             patch.object(processor.time, "sleep"):
            return app._update_once()

    def restart(self):
        restarted_websub = fresh_module("websub.py", "_websub_fresh_state")
        restarted_app = fresh_module("app.py", "_websub_fresh_app")
        self.stack.enter_context(patch.object(restarted_app, "CACHE_PATH", str(self.cache_path)))
        self.stack.enter_context(patch.object(restarted_app, "FEED_PATH", str(self.feed_path)))
        self.stack.enter_context(patch.object(restarted_app, "observe_feed", restarted_websub.observe_feed))
        self.stack.enter_context(patch.object(restarted_app, "publish_pending", restarted_websub.publish_pending))
        restarted_app.start_worker()
        self.client = restarted_app.app.test_client()
        return restarted_app

    def test_real_acquisition_publishes_valid_feed_then_one_hub_notification(self):
        self.assertEqual(self.ingest(), (True, True))
        self.assertIn(self.new["url"], json.loads(self.cache_path.read_text(encoding="utf-8"))["items"])
        root = ET.fromstring(self.feed_path.read_bytes())
        self.assertEqual({node.findtext("guid") for node in root.findall("./channel/item")},
                         {self.old["url"], self.new["url"]})
        self.assertTrue(all([node.text for node in item.findall("category")] == ["arte"]
                            for item in root.findall("./channel/item")))
        self.public.assert_not_called()
        self.hub.assert_not_called()
        # The same locally accepted article appears immediately in the news
        # sitemap; no GitHub cache sync is needed.
        sitemap = self.client.get("/news-sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn(self.new["url"].encode(), sitemap.data)
        app._notify_websub()
        self.hub.assert_called_once()
        self.assertEqual(self.state()["pending"], [])
        app._notify_websub()
        self.assertEqual(self.ingest(), (False, False))
        app._notify_websub()
        self.hub.assert_called_once()

    def test_unchanged_periodic_rebuild_and_restart_do_not_notify(self):
        for _ in range(3):
            self.assertFalse(app.rebuild_feed())
            app._notify_websub()
        restarted = self.restart()
        restarted._notify_websub()
        self.assertEqual(self.client.get("/rss").data, self.baseline.data)
        self.public.assert_not_called()
        self.hub.assert_not_called()

    def test_hub_timeout_preserves_rss_200_and_cache_exact_bytes(self):
        self.ingest()
        cache_before, feed_before = self.cache_path.read_bytes(), self.feed_path.read_bytes()
        self.hub.side_effect = requests.Timeout("Hub unavailable")
        app._notify_websub()
        result = self.client.get("/rss")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data, feed_before)
        self.assertEqual(self.cache_path.read_bytes(), cache_before)
        self.assertEqual(self.feed_path.read_bytes(), feed_before)
        self.assertEqual(self.state()["pending"][0]["attempts"], 1)

    def test_restart_does_not_duplicate_acknowledged_new_article(self):
        self.ingest()
        app._notify_websub()
        published = self.feed_path.read_bytes()
        restarted = self.restart()
        restarted._notify_websub()
        self.assertEqual(self.client.get("/rss").data, published)
        self.assertEqual(self.state()["pending"], [])
        self.hub.assert_called_once()

    def test_pending_retry_survives_app_and_websub_module_restart(self):
        self.ingest()
        self.hub.return_value = Mock(status_code=503)
        app._notify_websub()
        event_id = self.state()["pending"][0]["id"]
        restarted = self.restart()
        restarted._notify_websub()
        self.assertEqual(self.state()["pending"][0]["id"], event_id)
        self.assertEqual(self.state()["pending"][0]["attempts"], 1)
        self.hub.assert_called_once()
        self.now += 5
        self.hub.return_value = Mock(status_code=204)
        restarted._notify_websub()
        self.assertEqual(self.hub.call_count, 2)
        self.assertEqual(self.state()["pending"], [])
        restarted._notify_websub()
        self.assertEqual(self.hub.call_count, 2)

    def test_invalid_new_feed_keeps_previous_rss_and_never_notifies(self):
        with patch.object(app, "build_rss", return_value="<broken>"):
            self.assertEqual(self.ingest(), (True, False))
            app._notify_websub()
            result = self.client.get("/rss")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data, self.baseline.data)
        self.assertEqual(self.state()["pending"], [])
        self.public.assert_not_called()
        self.hub.assert_not_called()

    def test_public_old_feed_delays_hub_until_new_guid_is_public(self):
        self.ingest()
        self.public_override = self.baseline.data
        app._notify_websub()
        self.hub.assert_not_called()
        self.assertEqual(self.state()["pending"][0]["attempts"], 1)
        self.public_override = None
        self.now += 5
        app._notify_websub()
        self.hub.assert_called_once()
        self.assertEqual(self.state()["pending"], [])

    def test_leader_hook_recovers_state_write_failure_with_unchanged_feed(self):
        with patch.object(websub, "atomic_json", side_effect=PermissionError("State unavailable")):
            self.assertEqual(self.ingest(), (True, True))
            result = self.client.get("/rss")
            self.assertEqual(result.status_code, 200)
        self.assertEqual(self.state()["observed_guids"], [self.old["url"]])
        self.assertFalse(app.rebuild_feed())
        app._notify_websub()
        self.hub.assert_called_once()
        self.assertEqual(set(self.state()["observed_guids"]), {self.old["url"], self.new["url"]})
        self.assertEqual(self.state()["pending"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
