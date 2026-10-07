"""Probe contract and baseline comparisons; never fetch RSS over the network."""
import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
from uuid import UUID
import xml.etree.ElementTree as ET

import feedparser
import requests
import werkzeug.wrappers.response

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SHA = "a173ec0dbd5838278c6c002b848430d859ac56c6"
HOSTS = ("rss.artbooms.com", "artbooms-rss-x6pc.onrender.com")
PROBE_PATH = "/feedly-probe.xml"
BROKEN_URL = "https://www.artbooms.com/blog/broken-mostra-palazzo-strozzi"
ATOM = "{http://www.w3.org/2005/Atom}"
INSTANT = datetime(2026, 10, 7, 1, tzinfo=timezone.utc)
HTTP_DATE = "Wed, 07 Oct 2026 01:00:00 GMT"

# Importing app on Windows must not accidentally exercise its Linux leader.
if os.name == "nt" and "fcntl" not in sys.modules:
    shim = types.ModuleType("fcntl")
    shim.LOCK_EX, shim.LOCK_NB, shim.LOCK_UN = 2, 4, 8
    def unsupported_leader(*args, **kwargs):
        raise AssertionError("Linux leadership invoked by a Flask-only test")
    shim.flock = unsupported_leader
    sys.modules["fcntl"] = shim


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return INSTANT.astimezone(tz) if tz is not None else INSTANT.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return INSTANT.replace(tzinfo=None)


@lru_cache(maxsize=2)
def load_app(baseline=False):
    """Execute pinned baseline/current app in memory with separate local imports."""
    if baseline:
        result = subprocess.run(
            ["git", "show", BASELINE_SHA + ":app.py"], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", check=False,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        if result.returncode:
            raise AssertionError("Pinned baseline commit must be fetched before tests: " + result.stderr)
        source = result.stdout
        name = "_feedly_probe_baseline_app"
    else:
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        name = "_feedly_probe_revised_app"
    local_names = {p.stem for p in ROOT.glob("*.py")}
    previous = {key: sys.modules[key] for key in local_names if key in sys.modules}
    previous_path = list(sys.path)
    for key in local_names:
        sys.modules.pop(key, None)
    sys.path.insert(0, str(ROOT))
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / "app.py")
    sys.modules[name] = module
    try:
        with patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}), \
             patch.object(requests.Session, "request", side_effect=AssertionError("Network during import")):
            exec(compile(source, str(ROOT / "app.py"), "exec"), module.__dict__)
        dependencies = {key: sys.modules[key] for key in local_names if key in sys.modules}
        return types.SimpleNamespace(module=module, dependencies=dependencies)
    finally:
        for key in local_names:
            sys.modules.pop(key, None)
        sys.modules.update(previous)
        sys.path[:] = previous_path


def response_signature(response):
    return response.status_code, response.data, sorted(response.headers.to_wsgi_list())


class ProbeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_app()
        cls.application = cls.bundle.module

    def setUp(self):
        self.client = self.application.app.test_client(use_cookies=False)
        disabled = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, disabled)
        self.logger = logging.getLogger("artbooms.feedly_probe")
        level, propagate = self.logger.level, self.logger.propagate
        self.logger.setLevel(logging.CRITICAL)
        self.logger.propagate = False
        self.addCleanup(self.logger.setLevel, level)
        self.addCleanup(setattr, self.logger, "propagate", propagate)
        self.network = patch.object(requests.Session, "request", side_effect=AssertionError("Network in probe test"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def call(self, method="GET", host=HOSTS[0], headers=None, path=PROBE_PATH, **kwargs):
        values = dict(headers or {})
        values["Host"] = host
        return self.client.open(path, method=method, base_url="https://" + HOSTS[0], headers=values, **kwargs)

    def test_minimal_rss_contains_one_real_article_and_separate_identity(self):
        identities = []
        for host in HOSTS:
            with self.subTest(host=host):
                response = self.call(host=host)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "application/rss+xml")
                self.assertEqual(feedparser.parse(response.data).bozo, 0)
                root = ET.fromstring(response.data)
                self.assertEqual((root.tag, root.get("version")), ("rss", "2.0"))
                channel = root.find("channel")
                for field in ("title", "link", "description"):
                    self.assertTrue(channel.findtext(field))
                items = channel.findall("item")
                self.assertEqual(len(items), 1)
                item = items[0]
                self.assertEqual(item.findtext("link"), BROKEN_URL)
                self.assertIn("broken", item.findtext("title").lower())
                self.assertTrue(item.findtext("description"))
                guid = item.find("guid")
                self.assertEqual(guid.get("isPermaLink"), "false")
                self.assertTrue(guid.text.startswith("urn:uuid:"))
                UUID(guid.text[len("urn:uuid:"):])
                self.assertNotEqual(guid.text, BROKEN_URL)
                identities.append(guid.text)
                self.assertEqual(parsedate_to_datetime(item.findtext("pubDate")),
                                 datetime(2026, 10, 7, tzinfo=timezone.utc))
        self.assertEqual(identities[0], identities[1])

    def test_self_uses_only_authorized_request_host(self):
        for host in HOSTS:
            for raw_host in (host, host.upper(), host + ":443", host.upper() + ":443"):
                with self.subTest(host=raw_host):
                    response = self.call(host=raw_host)
                    self.assertEqual(response.status_code, 200)
                    node = ET.fromstring(response.data).find("./channel/" + ATOM + "link")
                    self.assertEqual(node.attrib, {"href": "https://" + host + PROBE_PATH,
                                                  "rel": "self", "type": "application/rss+xml"})

    def test_forwarding_headers_cannot_change_self_or_authorize_host(self):
        headers = {"X-Forwarded-Host": HOSTS[1], "Forwarded": "host=" + HOSTS[1] + ";proto=http",
                   "X-Forwarded-Proto": "http"}
        response = self.call(headers=headers)
        node = ET.fromstring(response.data).find("./channel/" + ATOM + "link")
        self.assertEqual(node.get("href"), "https://" + HOSTS[0] + PROBE_PATH)
        self.assertEqual(self.call(host="untrusted.example", headers=headers).status_code, 400)

    def test_unknown_or_non_https_port_hosts_are_rejected(self):
        for host in ("untrusted.example", HOSTS[0] + ".untrusted.example", HOSTS[0] + ":80",
                     HOSTS[1] + ":444", HOSTS[0] + ".", HOSTS[0] + ":443:443"):
            with self.subTest(host=host):
                response = self.call(host=host)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertNotIn(b"<rss", response.data)

    def test_head_has_no_body_and_same_headers_as_get(self):
        for host in HOSTS:
            with self.subTest(host=host):
                get = self.call(host=host)
                head = self.call("HEAD", host=host)
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.data, b"")
                self.assertEqual(dict(head.headers), dict(get.headers))
                self.assertEqual(int(head.headers["Content-Length"]), len(get.data))

    def test_no_store_noindex_and_no_conditional_304(self):
        for host in HOSTS:
            first = self.call(host=host)
            self.assertEqual(first.headers["Cache-Control"], "no-store")
            self.assertEqual(first.headers["X-Robots-Tag"], "noindex, nofollow")
            for field in ("ETag", "Last-Modified", "Set-Cookie", "Location"):
                self.assertNotIn(field, first.headers)
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    response = self.call(method, host=host, headers={"If-None-Match": "*",
                                         "If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data, first.data if method == "GET" else b"")

    def test_stable_bytes_and_query_does_not_change_xml(self):
        for host in HOSTS:
            first = self.call(host=host)
            for path in (PROBE_PATH, PROBE_PATH + "?diagnostic=second-attempt"):
                with self.subTest(host=host, path=path):
                    self.assertEqual(self.call(host=host, path=path).data, first.data)

    def test_probe_is_independent_of_production_cache_and_workers(self):
        state = tuple(copy.deepcopy(getattr(self.application, name)) for name in
                      ("_snapshot", "_source_token", "_source_digest"))
        with ExitStack() as stack:
            for name in ("rebuild_feed", "_load_snapshot", "generate_items", "bootstrap_cache",
                         "start_background", "news_sitemap_view", "_read_file"):
                stack.enter_context(patch.object(self.application, name,
                    side_effect=AssertionError("Production helper used by probe: " + name)))
            for method in ("GET", "HEAD"):
                self.assertEqual(self.call(method).status_code, 200)
        self.assertEqual(state, tuple(getattr(self.application, name) for name in
                                     ("_snapshot", "_source_token", "_source_digest")))

    def test_only_get_and_head_are_allowed(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                response = self.call(method)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(set(response.headers["Allow"].split(", ")), {"GET", "HEAD"})

    def test_structured_logs_cover_get_head_and_rejected_host_without_secrets(self):
        expected_keys = {"utc", "method", "host", "path", "user_agent", "accept", "accept_encoding",
                         "x_forwarded_for", "x_forwarded_proto", "remote_addr", "status"}
        headers = {"User-Agent": "Feedly/1.0 (+https://feedly.com/fetcher.html)",
                   "Accept": "application/rss+xml, application/xml;q=0.9", "Accept-Encoding": "gzip, br",
                   "X-Forwarded-For": "198.51.100.7, 203.0.113.8", "X-Forwarded-Proto": "https",
                   "Cookie": "private_session=cookie-secret", "Authorization": "Bearer auth-secret"}
        for method, host, status in (("GET", HOSTS[0], 200), ("HEAD", HOSTS[1], 200),
                                     ("GET", "untrusted.example", 400)):
            with self.subTest(method=method, host=host), self.assertLogs(self.logger, level="INFO") as captured:
                self.call(method, host=host, headers=headers, path=PROBE_PATH + "?secret=query-secret",
                          environ_overrides={"REMOTE_ADDR": "203.0.113.44"})
            self.assertEqual(len(captured.records), 1)
            message = captured.records[0].getMessage()
            self.assertTrue(message.startswith("feedly_probe "))
            values = json.loads(message[len("feedly_probe "):])
            self.assertEqual(set(values), expected_keys)
            self.assertEqual(values["method"], method)
            self.assertEqual(values["host"], host)
            self.assertEqual(values["path"], PROBE_PATH)
            self.assertEqual(values["status"], status)
            self.assertEqual(values["remote_addr"], "203.0.113.44")
            for field, header in (("user_agent", "User-Agent"), ("accept", "Accept"),
                                  ("accept_encoding", "Accept-Encoding"),
                                  ("x_forwarded_for", "X-Forwarded-For"), ("x_forwarded_proto", "X-Forwarded-Proto")):
                self.assertEqual(values[field], headers[header])
            instant = datetime.fromisoformat(values["utc"].replace("Z", "+00:00"))
            self.assertEqual(instant.utcoffset(), timedelta(0))
            self.assertLess(abs((datetime.now(timezone.utc) - instant).total_seconds()), 30)
            for secret in ("cookie-secret", "auth-secret", "query-secret", "private_session"):
                self.assertNotIn(secret, message)


class ProductionBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline, cls.revised = load_app(True), load_app(False)
        cls.bundles = (cls.baseline, cls.revised)
        cls.fixture_xml = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture</title>'
                           b'<link>https://www.artbooms.com</link><description>Fixture</description>'
                           b'<item><title>Fixture article</title><link>https://www.artbooms.com/blog/fixture</link>'
                           b'<guid>https://www.artbooms.com/blog/fixture</guid></item></channel></rss>')
        cls.fixture_etag = hashlib.sha256(cls.fixture_xml).hexdigest()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(requests.Session, "request", side_effect=AssertionError("Network in baseline comparison")))
        self.stack.enter_context(patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}))
        self.stack.enter_context(patch.object(werkzeug.wrappers.response, "http_date", return_value=HTTP_DATE))
        for bundle in self.bundles:
            bundle.module._snapshot = bundle.module._source_token = bundle.module._source_digest = None

    def responses(self, path, method="GET", headers=None, host=HOSTS[0]):
        return [bundle.module.app.test_client().open(path, method=method,
                base_url="https://" + host, headers=headers or {}) for bundle in self.bundles]

    def assert_same(self, responses, status):
        self.assertEqual(responses[0].status_code, status)
        self.assertEqual(response_signature(responses[0]), response_signature(responses[1]))

    def mock_snapshot(self, value):
        for bundle in self.bundles:
            self.stack.enter_context(patch.object(bundle.module, "rebuild_feed", return_value=False))
            self.stack.enter_context(patch.object(bundle.module, "_load_snapshot", return_value=value))

    def test_original_routes_and_methods_are_preserved_with_one_new_route(self):
        def rules(bundle):
            return {(rule.rule, rule.endpoint): frozenset(rule.methods) for rule in bundle.module.app.url_map.iter_rules()}
        before, after = rules(self.baseline), rules(self.revised)
        self.assertNotIn((PROBE_PATH, "feedly_probe_view"), before)
        self.assertEqual(set(after) - set(before), {(PROBE_PATH, "feedly_probe_view")})
        self.assertEqual(after[(PROBE_PATH, "feedly_probe_view")], frozenset({"GET", "HEAD"}))
        self.assertEqual(before, {key: value for key, value in after.items() if key in before})

    def test_rss_alias_get_head_and_conditional_bytes_headers_status_match_baseline(self):
        self.mock_snapshot(("fixture-token", self.fixture_xml, self.fixture_etag))
        tag = '"' + self.fixture_etag + '"'
        validators = ((None, 200), (tag, 304), ("W/" + tag, 304), ("*", 304), ('"different"', 200))
        for host in HOSTS:
            for path in ("/rss", "/rss.xml", "/feed.xml"):
                for method in ("GET", "HEAD"):
                    for validator, status in validators:
                        with self.subTest(host=host, path=path, method=method, validator=validator):
                            responses = self.responses(path, method, {"If-None-Match": validator} if validator else {}, host)
                            self.assert_same(responses, status)
                            self.assertEqual(responses[0].data, b"" if method == "HEAD" or status == 304 else self.fixture_xml)
                            self.assertNotIn("X-Robots-Tag", responses[0].headers)

    def test_rss_unavailable_503_get_head_match_baseline(self):
        self.mock_snapshot(None)
        for host in HOSTS:
            for path in ("/rss", "/rss.xml", "/feed.xml"):
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        responses = self.responses(path, method, {"If-None-Match": "*"}, host)
                        self.assert_same(responses, 503)
                        self.assertEqual(responses[0].headers["Retry-After"], "120")
                        self.assertEqual(responses[0].headers["Cache-Control"], "no-store")
                        self.assertNotIn("ETag", responses[0].headers)

    def test_real_production_feed_from_copied_cache_has_identical_bytes_and_headers(self):
        production = json.loads((ROOT / "cache/articles_cache.json").read_text(encoding="utf-8"))
        usable = self.baseline.dependencies["cache_safety"].cache_entries(production)
        self.assertGreaterEqual(len(usable), 2)
        urls = ([BROKEN_URL] if BROKEN_URL in usable else [])
        urls += [url for url in sorted(usable) if url not in urls][:2 - len(urls)]
        fixture = copy.deepcopy(production)
        fixture["items"] = {url: copy.deepcopy(usable[url]) for url in urls}
        with tempfile.TemporaryDirectory() as temporary:
            all_responses = []
            for index, bundle in enumerate(self.bundles):
                folder = Path(temporary) / str(index)
                folder.mkdir()
                cache_path, feed_path = folder / "cache.json", folder / "feed.xml"
                cache_bytes = json.dumps(fixture, ensure_ascii=True).encode("utf-8")
                cache_path.write_bytes(cache_bytes)
                with patch.object(bundle.module, "CACHE_PATH", str(cache_path)), \
                     patch.object(bundle.module, "FEED_PATH", str(feed_path)), \
                     patch.object(bundle.dependencies["rss_generator"], "datetime", FrozenDateTime):
                    response = bundle.module.app.test_client().get("/rss", base_url="https://" + HOSTS[0])
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(feed_path.read_bytes(), response.data)
                    self.assertEqual(cache_path.read_bytes(), cache_bytes)
                    self.assertEqual(ET.fromstring(response.data).findtext("./channel/lastBuildDate"),
                                     "Wed, 07 Oct 2026 01:00:00 +0000")
                    self.assertEqual({item.findtext("link") for item in ET.fromstring(response.data).findall("./channel/item")}, set(urls))
                    self.assertEqual(feedparser.parse(response.data).bozo, 0)
                    all_responses.append(response)
            self.assert_same(all_responses, 200)

    def test_real_sitemap_200_get_head_bytes_headers_match_baseline(self):
        item = {"url": BROKEN_URL, "title": "Broken & arte — ARTBOOMS", "published": "2026-10-06T12:00:00Z"}
        for bundle in self.bundles:
            news = bundle.dependencies["news_sitemap"]
            self.stack.enter_context(patch.object(news.datetime, "datetime", FrozenDateTime))
            self.stack.enter_context(patch.object(news, "_load_local_items", return_value=[copy.deepcopy(item)]))
            self.stack.enter_context(patch.object(news, "_load_remote_items", side_effect=AssertionError("Remote sitemap read")))
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    self.assert_same(self.responses("/news-sitemap.xml", method, host=host), 200)
        response = self.responses("/news-sitemap.xml")[0]
        self.assertIn(BROKEN_URL.encode(), response.data)
        ET.fromstring(response.data)

    def test_real_sitemap_503_get_head_bytes_headers_match_baseline(self):
        for bundle in self.bundles:
            news = bundle.dependencies["news_sitemap"]
            for loader in ("_load_local_items", "_load_remote_items"):
                self.stack.enter_context(patch.object(news, loader, side_effect=ValueError("Unavailable fixture")))
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    responses = self.responses("/news-sitemap.xml", method, host=host)
                    self.assert_same(responses, 503)
                    self.assertEqual(responses[0].headers["Retry-After"], "300")
                    self.assertEqual(responses[0].headers["Cache-Control"], "no-store")

    def test_home_and_health_bytes_headers_match_baseline_without_probe_advertising(self):
        for path in ("/", "/healthz"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    responses = self.responses(path, method)
                    self.assert_same(responses, 200)
                    self.assertNotIn(PROBE_PATH.encode(), responses[0].data)


if __name__ == "__main__":
    unittest.main(verbosity=2)