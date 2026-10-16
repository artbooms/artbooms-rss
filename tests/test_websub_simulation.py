"""Offline publication/deploy rehearsal against a temporary copy of the archive.

The checked-in production archive (at least 1,139 articles) is read only. Every generated feed, acquired
fixture and WebSub state file lives in TemporaryDirectory; HTTP is mocked and
socket connections are forbidden.  Re-created app/publisher modules and erased
runtime directories simulate process/deploy loss while a separate temporary
directory represents a persistent state mount, without configuring Render.
"""
import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import escape, unescape
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
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
        raise AssertionError("Background leadership is exercised by the Linux suite")
    shim.flock = no_native_leader
    sys.modules["fcntl"] = shim

with patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}), \
     patch.object(requests.Session, "request", side_effect=AssertionError("Network during import")):
    import article_processor as processor
    import news_sitemap
    import rss_generator
    import websub

SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
              "n": "http://www.google.com/schemas/sitemap-news/0.9"}
CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


class SimulatedProcessExit(BaseException):
    """Bypass normal network-error handlers, as a terminated process would."""


class WebSubArchiveSimulationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        disabled = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, disabled)
        self.source_path = ROOT / "cache" / "articles_cache.json"
        self.source_bytes = self.source_path.read_bytes()
        self.source_digest = hashlib.sha256(self.source_bytes).digest()
        self.addCleanup(self.assert_source_untouched)
        self.archive = json.loads(self.source_bytes)
        self.archive_count = len(self.archive["items"])
        self.assertGreaterEqual(self.archive_count, 1139)
        self.cursor = self.archive["cursor"]
        original = self.archive["items"]["https://www.artbooms.com/blog/ruth-asawa-beyeler"]
        self.fixture = copy.deepcopy(original)
        self.fixture["url"] = "https://www.artbooms.com/blog/__offline-websub-ruth-asawa-fixture__"
        self.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc).timestamp()
        self.fixture["published"] = datetime.fromtimestamp(self.now - 300, timezone.utc).isoformat()
        for field in ("_hash", "_fetched_at"):
            self.fixture.pop(field, None)
        self.guid = self.fixture["url"]
        self.assertNotIn(self.guid, self.archive["items"])
        self.temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.state_path = Path(self.temporary) / "durable-state" / "websub.json"
        self.stack.enter_context(patch.dict(os.environ, {
            "CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0",
            "WEBSUB_BACKEND": "file", "WEBSUB_STATE_PATH": str(self.state_path)}))
        self.network_guard = self.stack.enter_context(patch.object(
            requests.Session, "request", side_effect=AssertionError("Unexpected public HTTP")))
        self.socket_guard = self.stack.enter_context(patch.object(
            socket.socket, "connect", side_effect=AssertionError("Public socket forbidden")))
        self.stack.enter_context(patch.object(websub.time, "time", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(processor, "MAX_BATCH", 1))
        self.stack.enter_context(patch.object(processor.time, "sleep"))
        self.stack.enter_context(patch.object(processor, "_now_iso", side_effect=self.iso_now))
        clock = self
        class SimulationDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                instant = datetime.fromtimestamp(clock.now, timezone.utc)
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
            @classmethod
            def utcnow(cls):
                return cls.now(timezone.utc).replace(tzinfo=None)
        self.stack.enter_context(patch.object(rss_generator, "datetime", SimulationDateTime))
        self.stack.enter_context(patch.object(news_sitemap, "datetime", types.SimpleNamespace(
            datetime=SimulationDateTime, timezone=timezone, timedelta=timedelta)))
        self.public_content = None
        self.public_error = None
        self.hub_outcome = 204
        self.trace = []
        self.public = self.stack.enter_context(patch.object(websub.requests, "get", side_effect=self.public_get))
        self.hub = self.stack.enter_context(patch.object(websub.requests, "post", side_effect=self.hub_post))
        self.generation = 0
        self.runtime = None
        self.module_names = []
        self.addCleanup(self.forget_modules)
        self.deploy(self.source_bytes)
        self.baseline_feed = self.client.get("/rss").data
        self.assertEqual(len(ET.fromstring(self.baseline_feed).findall("./channel/item")), self.archive_count)
        self.assertEqual(self.cache_path.read_bytes(), self.source_bytes)
        self.assertEqual(set(self.state()["observed_guids"]), set(self.archive["items"]))
        self.assertEqual(self.state()["pending"], [])
        self.public.assert_not_called()
        self.hub.assert_not_called()

    def iso_now(self):
        return datetime.fromtimestamp(self.now, timezone.utc).isoformat()

    def assert_source_untouched(self):
        self.assertEqual(hashlib.sha256(self.source_path.read_bytes()).digest(), self.source_digest)

    def forget_modules(self):
        for name in self.module_names:
            sys.modules.pop(name, None)

    def fresh_module(self, filename):
        name = "_websub_simulation_%s_%d_%s" % (self._testMethodName, self.generation, filename[:-3])
        spec = importlib.util.spec_from_file_location(name, ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        self.module_names.append(name)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def deploy(self, cache_bytes=None):
        """Restore only article-cache bytes; retain state outside erased runtime."""
        if cache_bytes is None:
            cache_bytes = self.cache_path.read_bytes()
        previous_runtime = self.runtime
        previous_directory = Path(previous_runtime.name) if previous_runtime else None
        self.generation += 1
        self.runtime = tempfile.TemporaryDirectory(
            prefix="ephemeral-%d-" % self.generation, dir=self.temporary)
        self.stack.enter_context(self.runtime)
        self.cache_path = Path(self.runtime.name) / "articles.json"
        self.feed_path = Path(self.runtime.name) / "feed.xml"
        self.cache_path.write_bytes(cache_bytes)
        self.stack.enter_context(patch.object(processor, "CACHE_PATH", str(self.cache_path)))
        self.stack.enter_context(patch.object(news_sitemap, "LOCAL_CACHE_PATH", str(self.cache_path)))
        self.stack.enter_context(patch.object(news_sitemap, "LOCAL_FIRST", True))
        self.publisher = self.fresh_module("websub.py")
        self.application = self.fresh_module("app.py")
        for attribute, value in (("CACHE_PATH", str(self.cache_path)), ("FEED_PATH", str(self.feed_path)),
                                 ("observe_feed", self.publisher.observe_feed),
                                 ("publish_pending", self.publisher.publish_pending),
                                 ("resolve_state_path", self.publisher.resolve_state_path)):
            self.stack.enter_context(patch.object(self.application, attribute, value))
        self.application.start_worker()
        self.client = self.application.app.test_client()
        self.assertEqual(self.client.get("/rss").status_code, 200)
        self.assertEqual(self.cache_path.read_bytes(), cache_bytes)
        if previous_runtime:
            previous_runtime.cleanup()
            self.assertFalse(previous_directory.exists())
        self.assertFalse(Path(str(self.cache_path) + ".websub.json").exists())

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def acquire(self, real_source=False, pending_state=True):
        links = list(self.archive["items"]) + [self.guid]
        if real_source:
            articles = [*self.archive["items"].values(), self.fixture]
            archive_html = "<ul>" + "".join(
                '<li class="archive-item"><a href="%s">Articolo</a>'
                '<span class="archive-item-date">%s</span></li>' % (
                    escape(item["url"], quote=True), escape(item["published"]))
                for item in articles) + "</ul>"
            fields = {"name": "title", "url": "url", "description": "description",
                      "author": "author", "datePublished": "published",
                      "dateModified": "modified", "thumbnailUrl": "image"}
            article_html = "<html><head>" + "".join(
                '<meta itemprop="%s" content="%s">' % (prop, escape(self.fixture[field], quote=True))
                for prop, field in fields.items()) + "</head><body></body></html>"
            responses = {processor.ARCHIVE_URL: archive_html, self.guid: article_html}
            visited = []
            def offline_http(method, url, **kwargs):
                self.assertEqual(method.upper(), "GET")
                self.assertIn(url, responses, "Unconfigured public HTTP is forbidden")
                visited.append(url)
                response = requests.Response()
                response.status_code, response.url, response.encoding = 200, url, "utf-8"
                response._content = responses[url].encode("utf-8")
                return response
            # The normal archive scanner and article parser execute unchanged;
            # only their transport returns locally constructed HTML documents.
            with patch.object(self.network_guard, "side_effect", offline_http):
                self.assertEqual(self.application._update_once(), (True, True))
            self.assertEqual(visited, [processor.ARCHIVE_URL, self.guid])
        else:
            with patch.object(processor, "_scan_archive", return_value=links) as scan, \
                 patch.object(processor, "parse_article", return_value=copy.deepcopy(self.fixture)) as parse:
                self.assertEqual(self.application._update_once(), (True, True))
            scan.assert_called_once()
            parse.assert_called_once()
            self.assertEqual(parse.call_args.args[0], self.guid)
        data = json.loads(self.cache_path.read_bytes())
        self.assertEqual(len(data["items"]), self.archive_count + 1)
        self.assertEqual(data["cursor"], self.cursor)
        self.assertEqual({url: item for url, item in data["items"].items() if url != self.guid},
                         self.archive["items"])
        for field, value in self.fixture.items():
            self.assertEqual(data["items"][self.guid][field], value)
        pending = self.state()["pending"]
        if pending_state:
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["guids"], [self.guid])
            self.assertEqual(pending[0]["hub_attempts"], 0)
            self.assertEqual(pending[0]["feed_checks"], 0)
            self.assertEqual(pending[0]["expires_at"] - pending[0]["created_at"], 72 * 3600)
        else:
            self.assertEqual(pending, [])
        self.public.assert_not_called()
        self.hub.assert_not_called()
        self.assert_editorial_outputs()
        return pending[0].copy() if pending else None

    def assert_editorial_outputs(self):
        result = self.client.get("/rss")
        self.assertEqual(result.status_code, 200)
        root = ET.fromstring(result.data)
        items = {item.findtext("guid"): item for item in root.findall("./channel/item")}
        self.assertEqual(len(items), self.archive_count + 1)
        self.assertEqual(set(items), set(self.archive["items"]) | {self.guid})
        self.assertTrue(all([node.text for node in item.findall("category")] == ["arte"]
                            for item in items.values()))
        for guid, source in dict(self.archive["items"], **{self.guid: self.fixture}).items():
            item = items[guid]
            self.assertEqual(item.findtext("link"), guid)
            self.assertEqual(item.findtext("title"), unescape(source["title"]).strip())
            self.assertEqual(item.findtext(CREATOR), source["author"])
            self.assertEqual(item.find("enclosure").get("url"), source["image"])
            self.assertEqual(parsedate_to_datetime(item.findtext("pubDate")),
                             datetime.fromisoformat(source["published"].replace("Z", "+00:00")))
        sitemap = self.client.get("/news-sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        sitemap_root = ET.fromstring(sitemap.data)
        recent = {node.findtext("s:loc", namespaces=SITEMAP_NS): node
                  for node in sitemap_root.findall("s:url", SITEMAP_NS)}
        fixture_age = self.now - datetime.fromisoformat(self.fixture["published"]).timestamp()
        if fixture_age < 48 * 3600:
            news = recent[self.guid].find("n:news", SITEMAP_NS)
            self.assertIsNotNone(news)
            self.assertEqual(news.findtext("n:publication/n:name", namespaces=SITEMAP_NS), "ARTBOOMS")
            self.assertEqual(news.findtext("n:publication/n:language", namespaces=SITEMAP_NS), "it")
            self.assertTrue(news.findtext("n:title", namespaces=SITEMAP_NS))
            self.assertEqual(news.findtext("n:keywords", namespaces=SITEMAP_NS), "arte")
            self.assertEqual(datetime.fromisoformat(news.findtext("n:publication_date", namespaces=SITEMAP_NS)),
                             datetime.fromisoformat(self.fixture["published"]))
        for node in recent.values():
            published = node.findtext("n:news/n:publication_date", namespaces=SITEMAP_NS)
            if published:
                self.assertLess(self.now - datetime.fromisoformat(published).timestamp(), 48 * 3600)

    def public_get(self, url, **kwargs):
        self.assertEqual(url, websub.FEED_SELF_URL)
        self.assertEqual(kwargs["timeout"], websub.HTTP_TIMEOUT)
        self.assertFalse(kwargs["allow_redirects"])
        event = self.state()["pending"][0]
        self.assertGreater(event["feed_checks"], 0)
        self.trace.append(("GET", event["feed_checks"], event["hub_attempts"]))
        if self.public_error:
            raise self.public_error
        content = self.public_content if self.public_content is not None else self.client.get("/rss").data
        return Mock(status_code=200, content=content, headers={})

    def hub_post(self, url, **kwargs):
        self.assertEqual(url, websub.WEBSUB_HUB_URL)
        self.assertEqual(kwargs["data"], {"hub.mode": "publish", "hub.url": websub.FEED_SELF_URL})
        self.assertEqual(kwargs["timeout"], websub.HTTP_TIMEOUT)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(self.trace[-1][0], "GET")
        public = self.public_content if self.public_content is not None else self.client.get("/rss").data
        self.assertIn(self.guid, {node.findtext("guid") for node in ET.fromstring(public).findall("./channel/item")})
        event = self.state()["pending"][0]
        self.assertGreater(event["hub_attempts"], 0)
        self.assertGreater(event["next_attempt_at"], self.now)
        self.trace.append(("POST", event["feed_checks"], event["hub_attempts"]))
        if isinstance(self.hub_outcome, BaseException):
            raise self.hub_outcome
        headers = {"Retry-After": str(30 * 3600)} if self.hub_outcome == 429 else {}
        return Mock(status_code=self.hub_outcome, headers=headers)

    def notify(self):
        self.application._notify_websub()

    def advance_to_retry(self):
        self.now = self.state()["pending"][0]["next_attempt_at"]

    def assert_quiet(self):
        before = self.public.call_count, self.hub.call_count
        self.notify()
        self.assertEqual((self.public.call_count, self.hub.call_count), before)

    def test_full_archive_one_acquisition_ack_and_deploy_before_and_after(self):
        self.deploy()  # Deployment before the new article establishes no new event.
        self.assert_quiet()
        self.acquire(real_source=True)
        cache_bytes = self.cache_path.read_bytes()
        self.notify()
        self.assertEqual(self.trace, [("GET", 1, 0), ("POST", 1, 1)])
        self.assertEqual(self.state()["pending"], [])
        self.hub.assert_called_once()
        self.assert_quiet()
        self.deploy()
        self.assert_quiet()
        self.assertEqual(self.cache_path.read_bytes(), cache_bytes)
        self.assertEqual(set(self.state()["observed_guids"]), set(self.archive["items"]) | {self.guid})
        self.assert_editorial_outputs()
        self.assertEqual(self.network_guard.call_count, 2)
        self.socket_guard.assert_not_called()

    def test_stale_feed_then_timeout_503_429_recovers_after_deploy_beyond_one_day(self):
        original = self.acquire()
        cache_bytes = self.cache_path.read_bytes()
        feed_bytes = self.feed_path.read_bytes()
        self.public_error = requests.Timeout("Offline public-feed timeout")
        self.notify()
        self.advance_to_retry()
        self.public_error = None
        self.public_content = self.baseline_feed
        self.notify()
        self.assertEqual(self.state()["pending"][0]["hub_attempts"], 0)
        self.hub.assert_not_called()
        waiting = self.state()["pending"][0].copy()
        self.deploy()
        self.assertEqual(self.state()["pending"][0], waiting)
        self.assert_quiet()
        self.now = original["created_at"] + 10 * 3600
        self.public_content = None
        for outcome in (requests.Timeout("Offline ambiguous hub timeout"), 503, 429):
            self.hub_outcome = outcome
            self.notify()
            event = self.state()["pending"][0].copy()
            self.assertEqual(event["id"], original["id"])
            self.assertEqual(event["created_at"], original["created_at"])
            self.assertEqual(event["expires_at"], original["expires_at"])
            self.assertEqual(event["hub_attempts"], self.hub.call_count)
            self.assertEqual(self.client.get("/rss").status_code, 200)
            self.assertEqual(self.cache_path.read_bytes(), cache_bytes)
            self.deploy()
            self.assertEqual(self.state()["pending"][0], event)
            self.assert_quiet()
            self.advance_to_retry()
        self.assertGreater(self.now - original["created_at"], 24 * 3600)
        self.assertLess(self.now, original["expires_at"])
        self.hub_outcome = 204
        self.notify()
        self.assertEqual(self.hub.call_count, 4)
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(self.cache_path.read_bytes(), cache_bytes)
        # lastBuildDate may change on a fresh process, editorial XML may not.
        self.assertEqual(self.application._editorial_xml(self.feed_path.read_bytes()),
                         self.application._editorial_xml(feed_bytes))
        self.deploy()
        self.assert_quiet()

    def test_crashes_during_readiness_and_reserved_post_keep_pending_after_restore(self):
        original = self.acquire()
        self.public_error = SimulatedProcessExit("Exit during public GET")
        with self.assertRaises(SimulatedProcessExit):
            self.notify()
        before_restore = self.state()["pending"][0].copy()
        self.assertEqual((before_restore["feed_checks"], before_restore["hub_attempts"]), (1, 0))
        self.deploy()
        self.assertEqual(self.state()["pending"][0], before_restore)
        self.assert_quiet()
        self.advance_to_retry()
        self.public_error = None
        self.hub_outcome = SimulatedProcessExit("Exit after POST reservation, before acknowledgement")
        with self.assertRaises(SimulatedProcessExit):
            self.notify()
        before_restore = self.state()["pending"][0].copy()
        self.assertEqual((before_restore["feed_checks"], before_restore["hub_attempts"]), (2, 1))
        self.assertEqual(before_restore["expires_at"], original["expires_at"])
        self.deploy()
        self.assertEqual(self.state()["pending"][0], before_restore)
        self.assert_quiet()
        self.advance_to_retry()
        self.hub_outcome = 204
        self.notify()
        self.assertEqual(self.hub.call_count, 2)  # One bounded retry of the ambiguous POST.
        self.assertEqual(self.state()["pending"], [])
        self.deploy()
        self.assert_quiet()
        self.assert_editorial_outputs()

    def test_acknowledgement_lost_after_204_has_one_bounded_retry_after_deploy(self):
        original = self.acquire()
        cache_bytes = self.cache_path.read_bytes()
        write_state = self.publisher.atomic_json
        self.hub_outcome = 204
        def exit_before_ack_write(path, state):
            if not state["pending"]:
                # The hub response was accepted, but its acknowledgement has
                # not reached durable storage. WebSub has no idempotency key.
                self.assertEqual(self.hub.call_count, 1)
                self.assertEqual(self.trace[-1][0], "POST")
                raise SimulatedProcessExit("Exit after hub 204, before persisting acknowledgement")
            return write_state(path, state)
        with patch.object(self.publisher, "atomic_json", side_effect=exit_before_ack_write):
            with self.assertRaises(SimulatedProcessExit):
                self.notify()
        pending = self.state()["pending"][0].copy()
        self.assertEqual((pending["feed_checks"], pending["hub_attempts"]), (1, 1))
        for field in ("id", "created_at", "expires_at"):
            self.assertEqual(pending[field], original[field])
        self.assertEqual(self.cache_path.read_bytes(), cache_bytes)
        self.deploy()
        self.assertEqual(self.state()["pending"][0], pending)
        self.assert_quiet()
        self.advance_to_retry()
        self.notify()
        self.assertEqual(self.trace, [("GET", 1, 0), ("POST", 1, 1),
                                     ("GET", 2, 1), ("POST", 2, 2)])
        self.assertEqual(self.hub.call_count, 2)
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(self.cache_path.read_bytes(), cache_bytes)
        self.deploy()
        self.assert_quiet()
        self.assertEqual(self.hub.call_count, 2)
        self.assertEqual(set(self.state()["observed_guids"]), set(self.archive["items"]) | {self.guid})

    def test_72_hour_deadline_survives_deploy_and_expires_without_archive_replay(self):
        original = self.acquire()
        cache_bytes = self.cache_path.read_bytes()
        self.hub_outcome = 503
        self.notify()
        pending = self.state()["pending"][0].copy()
        self.deploy()
        self.assertEqual(self.state()["pending"][0], pending)
        self.now = original["expires_at"]
        self.assert_quiet()
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(self.hub.call_count, 1)
        self.deploy()
        self.assert_quiet()
        self.assertEqual(set(self.state()["observed_guids"]), set(self.archive["items"]) | {self.guid})
        self.assertEqual(self.cache_path.read_bytes(), cache_bytes)
        self.assert_editorial_outputs()
        sitemap = ET.fromstring(self.client.get("/news-sitemap.xml").data)
        self.assertEqual(sitemap.findall(".//n:news", SITEMAP_NS), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
