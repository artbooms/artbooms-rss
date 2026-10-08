"""Offline hostname migration checks against the pinned production application."""
import copy
from contextlib import ExitStack
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import feedparser
import requests
import werkzeug.wrappers.response

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SHA = "14a1ec7b5a4a8962bbf77c130055de40391002c0"
OLD_HOST = "artbooms-rss-x6pc.onrender.com"
CUSTOM_HOST = "rss.artbooms.com"
CANONICAL = "https://rss.artbooms.com/rss"
ALIASES = ("/rss", "/rss.xml", "/feed.xml")
DIAGNOSTICS = (("/feedly-probe.xml", "feedly_probe_view"),
               ("/feedly-discovery-test", "feedly_discovery_view"))
TEST_AGENT = "ARTBOOMS-Migration-Test/1.0"
INSTANT = datetime(2026, 10, 8, tzinfo=timezone.utc)
HTTP_DATE = "Thu, 08 Oct 2026 00:00:00 GMT"

if os.name == "nt" and "fcntl" not in sys.modules:
    shim = types.ModuleType("fcntl")
    shim.LOCK_EX, shim.LOCK_NB, shim.LOCK_UN = 2, 4, 8
    def unsupported_leader(*args, **kwargs):
        raise AssertionError("Native Linux leadership invoked in a Flask-only test")
    shim.flock = unsupported_leader
    sys.modules["fcntl"] = shim


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return INSTANT.astimezone(tz) if tz is not None else INSTANT.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return INSTANT.replace(tzinfo=None)


def pinned_source(filename):
    result = subprocess.run(["git", "show", BASELINE_SHA + ":" + filename], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=False,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if result.returncode:
        raise AssertionError("Fetch pinned production baseline before testing: " + result.stderr)
    return result.stdout


@lru_cache(maxsize=2)
def load_app(baseline=False):
    source = pinned_source("app.py") if baseline else (ROOT / "app.py").read_text(encoding="utf-8")
    diagnostic_sources = {name: pinned_source(name + ".py") for name in
                          ("feedly_probe", "feedly_discovery")} if baseline else {}
    # The diagnostic modules have been deleted from the revised checkout.
    # Compile the pinned versions only in memory for the baseline import.
    local_names = {path.stem for path in ROOT.glob("*.py")} | {"feedly_probe", "feedly_discovery"}
    previous = {key: sys.modules[key] for key in local_names if key in sys.modules}
    previous_path = list(sys.path)
    for key in local_names:
        sys.modules.pop(key, None)
    sys.path.insert(0, str(ROOT))
    module = types.ModuleType("_feed_migration_baseline_app" if baseline else "_feed_migration_revised_app")
    module.__file__ = str(ROOT / "app.py")
    sys.modules[module.__name__] = module
    try:
        with patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}), \
             patch.object(requests.Session, "request", side_effect=AssertionError("Network during import")):
            for name, text in diagnostic_sources.items():
                diagnostic = types.ModuleType(name)
                diagnostic.__file__ = str(ROOT / (name + ".py"))
                sys.modules[name] = diagnostic
                exec(compile(text, diagnostic.__file__, "exec"), diagnostic.__dict__)
            exec(compile(source, module.__file__, "exec"), module.__dict__)
        return types.SimpleNamespace(module=module, dependencies={
            key: sys.modules[key] for key in local_names if key in sys.modules})
    finally:
        for key in local_names:
            sys.modules.pop(key, None)
        sys.modules.update(previous)
        sys.path[:] = previous_path


def signature(response):
    return response.status_code, response.data, sorted(response.headers.to_wsgi_list())


class FlaskMigrationCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline, cls.revised = load_app(True), load_app(False)
        cls.bundles = (cls.baseline, cls.revised)
        cls.xml = (b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
                   b'xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>Fixture</title>'
                   b'<link>https://www.artbooms.com</link><description>Fixture feed</description>'
                   b'<atom:link href="https://rss.artbooms.com/rss" rel="self" type="application/rss+xml" />'
                   b'<item><title>Fixture article</title><link>https://www.artbooms.com/blog/fixture</link>'
                   b'<guid>https://www.artbooms.com/blog/fixture</guid></item></channel></rss>')
        cls.etag = hashlib.sha256(cls.xml).hexdigest()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        disabled = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, disabled)
        self.stack.enter_context(patch.object(requests.Session, "request", side_effect=AssertionError("Network from unit tests")))
        self.stack.enter_context(patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}))
        self.stack.enter_context(patch.object(werkzeug.wrappers.response, "http_date", return_value=HTTP_DATE))
        for bundle in self.bundles:
            bundle.module._snapshot = bundle.module._source_token = bundle.module._source_digest = None

    def call(self, bundle, path="/rss", method="GET", host=CUSTOM_HOST, extra=None, **kwargs):
        headers = {"Host": host, "User-Agent": TEST_AGENT}
        headers.update(extra or {})
        return bundle.module.app.test_client(use_cookies=False).open(
            path, method=method, base_url="https://" + CUSTOM_HOST, headers=headers, **kwargs)

    def responses(self, path, method="GET", host=CUSTOM_HOST, extra=None):
        return [self.call(bundle, path, method, host, extra) for bundle in self.bundles]

    def assert_same(self, responses, status):
        self.assertEqual(responses[0].status_code, status)
        self.assertEqual(signature(responses[0]), signature(responses[1]))

    def mock_snapshot(self, available=True):
        value = ("fixture-token", self.xml, self.etag) if available else None
        for bundle in self.bundles:
            self.stack.enter_context(patch.object(bundle.module, "rebuild_feed", return_value=False))
            self.stack.enter_context(patch.object(bundle.module, "_load_snapshot", return_value=value))


class HostRedirectTests(FlaskMigrationCase):
    def test_old_get_head_three_aliases_have_exact_permanent_destination(self):
        self.assertEqual(self.revised.module.FEED_SELF_URL, CANONICAL)
        for path in ALIASES:
            get = self.call(self.revised, path, host=OLD_HOST)
            head = self.call(self.revised, path, "HEAD", OLD_HOST)
            with self.subTest(path=path):
                self.assertEqual((get.status_code, head.status_code), (301, 301))
                self.assertEqual(get.headers["Location"], CANONICAL)
                self.assertEqual(head.headers["Location"], CANONICAL)
                self.assertEqual(head.data, b"")
                self.assertEqual(sorted(get.headers.to_wsgi_list()), sorted(head.headers.to_wsgi_list()))
                self.assertEqual(int(head.headers["Content-Length"]), len(get.data))

    def test_case_and_ports_preserve_old_hostname_redirect(self):
        for host in (OLD_HOST.upper(), OLD_HOST + ":443", OLD_HOST + ":80", OLD_HOST.upper() + ":443"):
            for path in ALIASES:
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        response = self.call(self.revised, path, method, host)
                        self.assertEqual(response.status_code, 301)
                        self.assertEqual(response.headers["Location"], CANONICAL)

    def test_alias_queries_still_redirect_to_exact_canonical_rss(self):
        for path in ALIASES:
            response = self.call(self.revised, path + "?source=old&attempt=2", host=OLD_HOST)
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers["Location"], CANONICAL)

    def test_old_conditional_requests_remain_301_without_validators(self):
        for path in ALIASES:
            for method in ("GET", "HEAD"):
                for validator in ("*", '"' + self.etag + '"'):
                    with self.subTest(path=path, method=method, validator=validator):
                        response = self.call(self.revised, path, method, OLD_HOST, {
                            "If-None-Match": validator, "If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"})
                        self.assertEqual(response.status_code, 301)
                        self.assertEqual(response.headers["Location"], CANONICAL)
                        self.assertNotIn("ETag", response.headers)
                        self.assertNotIn("Last-Modified", response.headers)

    def test_redirect_happens_before_cache_workers_or_production_helpers(self):
        application = self.revised.module
        state = tuple(copy.deepcopy(getattr(application, name)) for name in
                      ("_snapshot", "_source_token", "_source_digest"))
        with ExitStack() as stack:
            for name in ("rebuild_feed", "_load_snapshot", "generate_items", "bootstrap_cache", "start_worker",
                         "start_background", "build_rss", "news_sitemap_view", "_read_file", "_file_token",
                         "cache_transaction", "atomic_write", "atomic_json"):
                stack.enter_context(patch.object(application, name,
                    side_effect=AssertionError("Production helper used before redirect: " + name)))
            for path in ALIASES:
                for method in ("GET", "HEAD"):
                    self.assertEqual(self.call(self.revised, path, method, OLD_HOST).status_code, 301)
        self.assertEqual(state, tuple(getattr(application, name) for name in
                                     ("_snapshot", "_source_token", "_source_digest")))

    def test_unknown_suffix_spoofs_and_trailing_dot_do_not_redirect(self):
        self.mock_snapshot()
        for host in ("untrusted.example", OLD_HOST + ".untrusted.example", "prefix." + OLD_HOST,
                     OLD_HOST + ".", OLD_HOST + ".:443"):
            for path in ALIASES:
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        response = self.call(self.revised, path, method, host)
                        self.assertEqual(response.status_code, 200)
                        self.assertNotIn("Location", response.headers)
                        self.assertEqual(response.data, self.xml if method == "GET" else b"")

    def test_forwarding_headers_cannot_change_redirect_decision(self):
        self.mock_snapshot()
        for host, forwarded_host, status in ((CUSTOM_HOST, OLD_HOST, 200), (OLD_HOST, CUSTOM_HOST, 301),
                                              ("untrusted.example", OLD_HOST, 200)):
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, forwarded_host=forwarded_host, method=method):
                    response = self.call(self.revised, "/rss", method, host, {
                        "X-Forwarded-Host": forwarded_host, "Forwarded": "host=" + forwarded_host + ";proto=https",
                        "X-Forwarded-Proto": "http", "X-Forwarded-For": "192.0.2.11"})
                    self.assertEqual(response.status_code, status)
                    if status == 301:
                        self.assertEqual(response.headers["Location"], CANONICAL)
                    else:
                        self.assertNotIn("Location", response.headers)

    def test_manual_follow_same_application_and_ip_ends_on_custom_xml_without_loop(self):
        self.mock_snapshot()
        for path in ALIASES:
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    environment = {"REMOTE_ADDR": "127.0.0.1", "SERVER_ADDR": "127.0.0.1"}
                    first = self.call(self.revised, path, method, OLD_HOST, environ_overrides=environment)
                    self.assertEqual(first.status_code, 301)
                    target = urlsplit(first.headers["Location"])
                    self.assertEqual((target.scheme, target.hostname, target.path), ("https", CUSTOM_HOST, "/rss"))
                    second = self.call(self.revised, target.path, method, target.hostname, environ_overrides=environment)
                    self.assertEqual(second.status_code, 200)
                    self.assertEqual(second.mimetype, "application/rss+xml")
                    self.assertNotIn("Location", second.headers)
                    self.assertEqual(second.data, self.xml if method == "GET" else b"")


class ProductionBaselineTests(FlaskMigrationCase):
    def test_route_map_removes_only_two_diagnostic_routes_and_preserves_methods(self):
        def rules(bundle):
            return {(rule.rule, rule.endpoint): frozenset(rule.methods) for rule in bundle.module.app.url_map.iter_rules()}
        before, after = rules(self.baseline), rules(self.revised)
        self.assertEqual(set(before) - set(after), set(DIAGNOSTICS))
        self.assertEqual(set(after) - set(before), set())
        self.assertEqual(after, {key: value for key, value in before.items() if key not in DIAGNOSTICS})

    def test_removed_diagnostic_routes_get_head_are_404_on_both_hosts(self):
        for host in (CUSTOM_HOST, OLD_HOST):
            for path, _ in DIAGNOSTICS:
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        self.assertEqual(self.call(self.baseline, path, method, host).status_code, 200)
                        response = self.call(self.revised, path, method, host)
                        self.assertEqual(response.status_code, 404)
                        self.assertNotIn("Location", response.headers)
                        if method == "HEAD":
                            self.assertEqual(response.data, b"")
        self.assertNotIn("feedly_probe", self.revised.dependencies)
        self.assertNotIn("feedly_discovery", self.revised.dependencies)

    def test_custom_alias_get_head_200_and_conditionals_match_all_baseline_bytes_headers(self):
        self.mock_snapshot()
        tag = '"' + self.etag + '"'
        for host in (CUSTOM_HOST, CUSTOM_HOST.upper(), CUSTOM_HOST + ":443"):
            for path in ALIASES:
                for method in ("GET", "HEAD"):
                    for validator, status in ((None, 200), (tag, 304), ("W/" + tag, 304), ("*", 304), ('"different"', 200)):
                        with self.subTest(host=host, path=path, method=method, validator=validator):
                            responses = self.responses(path, method, host, {"If-None-Match": validator} if validator else {})
                            self.assert_same(responses, status)
                            self.assertNotIn("Location", responses[1].headers)
                            self.assertEqual(responses[1].data, b"" if method == "HEAD" or status == 304 else self.xml)

    def test_custom_alias_unavailable_503_get_head_match_pinned_baseline(self):
        self.mock_snapshot(False)
        for path in ALIASES:
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    responses = self.responses(path, method, extra={"If-None-Match": "*"})
                    self.assert_same(responses, 503)
                    self.assertEqual(responses[1].headers["Retry-After"], "120")
                    self.assertEqual(responses[1].headers["Cache-Control"], "no-store")
                    self.assertNotIn("Location", responses[1].headers)

    def test_real_custom_feed_from_copied_cache_matches_all_aliases_and_baseline(self):
        original = json.loads((ROOT / "cache/articles_cache.json").read_text(encoding="utf-8"))
        usable = self.baseline.dependencies["cache_safety"].cache_entries(original)
        self.assertGreaterEqual(len(usable), 2)
        urls = sorted(usable)[:2]
        fixture = copy.deepcopy(original)
        fixture["items"] = {url: copy.deepcopy(usable[url]) for url in urls}
        expected = {}
        with tempfile.TemporaryDirectory() as temporary:
            for index, bundle in enumerate(self.bundles):
                folder = Path(temporary) / str(index)
                folder.mkdir()
                cache, feed = folder / "cache.json", folder / "feed.xml"
                before = json.dumps(fixture, ensure_ascii=True).encode("utf-8")
                cache.write_bytes(before)
                with patch.object(bundle.module, "CACHE_PATH", str(cache)), \
                     patch.object(bundle.module, "FEED_PATH", str(feed)), \
                     patch.object(bundle.dependencies["rss_generator"], "datetime", FrozenDateTime):
                    if bundle is self.revised:
                        self.assertEqual(self.call(bundle, host=OLD_HOST).status_code, 301)
                        self.assertFalse(feed.exists())
                    for path in ALIASES:
                        for method in ("GET", "HEAD"):
                            response = self.call(bundle, path, method)
                            self.assertEqual(response.status_code, 200)
                            key = path, method
                            if bundle is self.baseline:
                                expected[key] = signature(response)
                            else:
                                self.assertEqual(signature(response), expected[key])
                            if method == "GET":
                                parsed = ET.fromstring(response.data)
                                self.assertEqual({item.findtext("link") for item in parsed.findall("./channel/item")}, set(urls))
                                self.assertEqual(parsed.findtext("./channel/lastBuildDate"), "Thu, 08 Oct 2026 00:00:00 +0000")
                                self.assertEqual(parsed.find("./channel/{http://www.w3.org/2005/Atom}link").get("href"), CANONICAL)
                                self.assertEqual(feedparser.parse(response.data).bozo, 0)
                    self.assertEqual(cache.read_bytes(), before)
                    self.assertEqual(feed.read_bytes(), expected[("/rss", "GET")][1])

    def test_home_health_get_head_bytes_headers_status_unchanged_on_both_hosts(self):
        for host in (CUSTOM_HOST, OLD_HOST):
            for path in ("/", "/healthz"):
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        self.assert_same(self.responses(path, method, host), 200)

    def test_real_sitemap_200_503_get_head_match_baseline_on_both_hosts(self):
        item = {"url": "https://www.artbooms.com/blog/migration-fixture",
                "title": "Arte & cultura — ARTBOOMS", "published": "2026-10-07T12:00:00Z"}
        for available in (True, False):
            with ExitStack() as stack:
                for bundle in self.bundles:
                    news = bundle.dependencies["news_sitemap"]
                    stack.enter_context(patch.object(news.datetime, "datetime", FrozenDateTime))
                    for loader in ("_load_local_items", "_load_remote_items"):
                        stack.enter_context(patch.object(news, loader, return_value=[copy.deepcopy(item)]) if available
                            else patch.object(news, loader, side_effect=ValueError("Unavailable fixture")))
                for host in (CUSTOM_HOST, OLD_HOST):
                    for method in ("GET", "HEAD"):
                        with self.subTest(available=available, host=host, method=method):
                            self.assert_same(self.responses("/news-sitemap.xml", method, host), 200 if available else 503)

    def test_debug_and_cache_download_use_local_fixture_with_equal_bytes_headers_status(self):
        original = json.loads((ROOT / "cache/articles_cache.json").read_text(encoding="utf-8"))
        usable = self.baseline.dependencies["cache_safety"].cache_entries(original)
        urls = sorted(usable)[:2]
        fixture = copy.deepcopy(original)
        fixture["items"] = {url: copy.deepcopy(usable[url]) for url in urls}
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            before = json.dumps(fixture, ensure_ascii=True).encode("utf-8")
            cache.write_bytes(before)
            # The same path is deliberate: send_file's ETag incorporates it.
            with ExitStack() as stack:
                for bundle in self.bundles:
                    stack.enter_context(patch.object(bundle.module, "CACHE_PATH", str(cache)))
                for host in (CUSTOM_HOST, OLD_HOST):
                    for path in ("/debug/cache", "/cache/download"):
                        for method in ("GET", "HEAD"):
                            with self.subTest(host=host, path=path, method=method):
                                responses = self.responses(path, method, host, {"If-None-Match": "*"})
                                try:
                                    self.assert_same(responses, 200)
                                    if method == "GET" and path == "/debug/cache":
                                        self.assertEqual(responses[1].get_json(), {"articles_in_cache": 2, "usable_articles": 2})
                                    if method == "GET" and path == "/cache/download":
                                        self.assertEqual(responses[1].data, before)
                                finally:
                                    for response in responses:
                                        response.close()
                self.assertEqual(cache.read_bytes(), before)

    def test_wake_local_mocked_side_effects_bytes_headers_status_match_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            wake_path = str(Path(temporary) / "wake")
            tick = 1791417600123456789
            for host in (CUSTOM_HOST, OLD_HOST):
                for method in ("GET", "HEAD"):
                    responses = []
                    for bundle in self.bundles:
                        with patch.object(bundle.module, "WAKE_PATH", wake_path), \
                             patch.object(bundle.module.time, "time_ns", return_value=tick), \
                             patch.object(bundle.module, "start_background") as start, \
                             patch.object(bundle.module, "atomic_write") as write:
                            response = self.call(bundle, "/wake", method, host)
                            start.assert_called_once_with()
                            write.assert_called_once_with(wake_path, str(tick).encode())
                            responses.append(response)
                    with self.subTest(host=host, method=method):
                        self.assert_same(responses, 200)
                        if method == "HEAD":
                            self.assertEqual(responses[1].data, b"")
            self.assertFalse(Path(wake_path).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)