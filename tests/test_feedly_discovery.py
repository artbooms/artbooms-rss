"""Offline discovery-page contract and comparisons with current production."""
import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

from lxml import html
import requests
import werkzeug.wrappers.response

from test_feedly_probe import FrozenDateTime, load_app as load_probe_app, response_signature

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SHA = "abbd6acadd18a41f20808a721044fe07d6206a25"
HOSTS = ("rss.artbooms.com", "artbooms-rss-x6pc.onrender.com")
DISCOVERY_PATH = "/feedly-discovery-test"
PROBE_PATH = "/feedly-probe.xml"
TARGET = "https://artbooms-rss-x6pc.onrender.com/feedly-probe.xml"
TEST_AGENT = "ARTBOOMS-Discovery-Test/1.0"
HTTP_DATE = "Wed, 07 Oct 2026 01:00:00 GMT"


def pinned_source(filename):
    result = subprocess.run(["git", "show", BASELINE_SHA + ":" + filename], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=False,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if result.returncode:
        raise AssertionError("Fetch pinned production baseline before testing: " + result.stderr)
    return result.stdout


@lru_cache(maxsize=2)
def load_app(baseline=False):
    if not baseline:
        return load_probe_app(False)
    # Keep the actual production probe pinned too: loading its current source
    # on both sides would conceal an accidental change to that existing route.
    source, probe_source = pinned_source("app.py"), pinned_source("feedly_probe.py")
    local_names = {path.stem for path in ROOT.glob("*.py")}
    previous = {key: sys.modules[key] for key in local_names if key in sys.modules}
    previous_path = list(sys.path)
    for key in local_names:
        sys.modules.pop(key, None)
    sys.path.insert(0, str(ROOT))
    module = types.ModuleType("_feedly_discovery_baseline_app")
    module.__file__ = str(ROOT / "app.py")
    sys.modules[module.__name__] = module
    try:
        with patch.dict(os.environ, {"CACHE_BOOTSTRAP_REMOTE": "0", "POPULATOR_ENABLED": "0"}), \
             patch.object(requests.Session, "request", side_effect=AssertionError("Network during import")):
            probe = types.ModuleType("feedly_probe")
            probe.__file__ = str(ROOT / "feedly_probe.py")
            sys.modules["feedly_probe"] = probe
            exec(compile(probe_source, probe.__file__, "exec"), probe.__dict__)
            exec(compile(source, module.__file__, "exec"), module.__dict__)
        return types.SimpleNamespace(module=module, dependencies={
            key: sys.modules[key] for key in local_names if key in sys.modules})
    finally:
        for key in local_names:
            sys.modules.pop(key, None)
        sys.modules.update(previous)
        sys.path[:] = previous_path


class StructureParser(HTMLParser):
    """Track real element ancestry independently of lxml's HTML recovery."""
    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.elements, self.declarations, self.errors = [], [], [], []

    def handle_decl(self, decl):
        self.declarations.append(decl.lower())

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), tuple(self.stack)))
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), tuple(self.stack)))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append((tag, tuple(self.stack)))
        else:
            self.stack.pop()


def tree_from(response):
    return html.document_fromstring(response.data, parser=html.HTMLParser(recover=False, no_network=True))


class DiscoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_app()
        cls.application = cls.bundle.module

    def setUp(self):
        self.client = self.application.app.test_client(use_cookies=False)
        disabled = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, disabled)
        self.logger = logging.getLogger("artbooms.feedly_discovery")
        level, propagate = self.logger.level, self.logger.propagate
        self.logger.setLevel(logging.CRITICAL)
        self.logger.propagate = False
        self.addCleanup(self.logger.setLevel, level)
        self.addCleanup(setattr, self.logger, "propagate", propagate)
        network = patch.object(requests.Session, "request", side_effect=AssertionError("Network from discovery"))
        network.start()
        self.addCleanup(network.stop)

    def call(self, method="GET", host=HOSTS[0], path=DISCOVERY_PATH, extra=None, **kwargs):
        headers = {"Host": host, "User-Agent": TEST_AGENT}
        headers.update(extra or {})
        return self.client.open(path, method=method, base_url="https://" + HOSTS[0], headers=headers, **kwargs)

    def test_minimal_valid_html_has_one_alternate_in_the_actual_head(self):
        for host in HOSTS:
            with self.subTest(host=host):
                response = self.call(host=host)
                self.assertEqual(response.status_code, 200)
                parser = StructureParser()
                parser.feed(response.data.decode("utf-8"))
                parser.close()
                self.assertEqual(parser.declarations, ["doctype html"])
                self.assertEqual(parser.errors, [])
                self.assertEqual(parser.stack, [])
                for tag in ("html", "head", "body"):
                    self.assertEqual(sum(element[0] == tag for element in parser.elements), 1)
                links = [element for element in parser.elements if element[0] == "link"]
                self.assertEqual(len(links), 1)
                tag, attributes, ancestry = links[0]
                self.assertEqual(ancestry, ("html", "head"))
                self.assertEqual(attributes["rel"], "alternate")
                self.assertEqual(attributes["type"], "application/rss+xml")
                self.assertEqual(attributes["href"], TARGET)
                tree = tree_from(response)
                self.assertEqual(len(tree.xpath("/html/head/link[@rel='alternate']")), 1)
                self.assertEqual(tree.xpath("/html/head/link/@href"), [TARGET])
                self.assertTrue(tree.xpath("/html/head/title/text()"))
                self.assertEqual(tree.xpath("/html/head/meta/@charset"), ["utf-8"])
                self.assertEqual(response.data, self.bundle.dependencies["feedly_discovery"].DISCOVERY_HTML.encode("utf-8"))

    def test_get_head_200_have_identical_headers_and_correct_length_on_both_hosts(self):
        for host in HOSTS:
            with self.subTest(host=host):
                get, head = self.call(host=host), self.call("HEAD", host=host)
                self.assertEqual((get.status_code, head.status_code), (200, 200))
                self.assertEqual(head.data, b"")
                self.assertEqual(sorted(get.headers.to_wsgi_list()), sorted(head.headers.to_wsgi_list()))
                self.assertEqual(int(head.headers["Content-Length"]), len(get.data))
                self.assertEqual(get.headers["Content-Type"], "text/html; charset=utf-8")
                self.assertEqual(get.headers["Cache-Control"], "no-store")

    def test_no_validators_cookies_robots_or_redirect_headers(self):
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    response = self.call(method, host=host)
                    self.assertEqual(response.status_code, 200)
                    for name in ("ETag", "Last-Modified", "Set-Cookie", "X-Robots-Tag", "Location"):
                        self.assertNotIn(name, response.headers)

    def test_no_scripts_canonical_refresh_or_active_handlers(self):
        tree = tree_from(self.call())
        self.assertEqual(tree.xpath("//script"), [])
        for node in tree.iter():
            if node.tag == "link":
                self.assertNotIn("canonical", node.get("rel", "").lower().split())
            if node.tag == "meta":
                self.assertNotEqual(node.get("http-equiv", "").lower(), "refresh")
            for attribute, value in node.attrib.items():
                self.assertFalse(attribute.lower().startswith("on"))
                self.assertFalse(value.lstrip().lower().startswith("javascript:"))

    def test_host_forwarding_and_query_do_not_change_static_html_or_target(self):
        first = self.call()
        for host in HOSTS:
            response = self.call(host=host, path=DISCOVERY_PATH + "?attempt=second", extra={
                "X-Forwarded-Host": "untrusted.example", "X-Forwarded-Proto": "http",
                "Forwarded": "host=untrusted.example;proto=http"})
            self.assertEqual(response_signature(response), response_signature(first))
            self.assertEqual(tree_from(response).xpath("/html/head/link/@href"), [TARGET])

    def test_conditional_headers_never_produce_304_or_redirect(self):
        for host in HOSTS:
            first = self.call(host=host)
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    response = self.call(method, host=host, extra={"If-None-Match": "*",
                                         "If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data, first.data if method == "GET" else b"")
                    self.assertNotIn("Location", response.headers)

    def test_page_does_not_access_cache_workers_production_builders_or_http(self):
        state = tuple(copy.deepcopy(getattr(self.application, name)) for name in
                      ("_snapshot", "_source_token", "_source_digest"))
        with ExitStack() as stack:
            for name in ("rebuild_feed", "_load_snapshot", "generate_items", "bootstrap_cache", "start_worker",
                         "start_background", "news_sitemap_view", "_read_file", "cache_transaction", "atomic_write"):
                stack.enter_context(patch.object(self.application, name,
                    side_effect=AssertionError("Production helper used by discovery: " + name)))
            for host in HOSTS:
                for method in ("GET", "HEAD"):
                    self.assertEqual(self.call(method, host=host).status_code, 200)
        self.assertEqual(state, tuple(getattr(self.application, name) for name in
                                     ("_snapshot", "_source_token", "_source_digest")))

    def test_only_get_and_head_are_allowed(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                response = self.call(method)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(set(response.headers["Allow"].split(", ")), {"GET", "HEAD"})

    def test_logs_have_only_requested_fields_and_no_cookies_auth_or_query_secrets(self):
        keys = {"utc", "method", "host", "path", "user_agent", "accept", "accept_encoding",
                "x_forwarded_for", "x_forwarded_proto", "remote_addr", "status"}
        headers = {"User-Agent": TEST_AGENT, "Accept": "text/html, application/xhtml+xml;q=0.9",
                   "Accept-Encoding": "gzip, br", "X-Forwarded-For": "198.51.100.7, 203.0.113.8",
                   "X-Forwarded-Proto": "https", "Cookie": "session=discovery-cookie-secret",
                   "Authorization": "Bearer discovery-auth-secret"}
        for method in ("GET", "HEAD"):
            for host in HOSTS:
                with self.subTest(method=method, host=host), self.assertLogs(self.logger, level="INFO") as captured:
                    self.call(method, host=host, path=DISCOVERY_PATH + "?token=discovery-query-secret", extra=headers,
                              environ_overrides={"REMOTE_ADDR": "203.0.113.44"})
                self.assertEqual(len(captured.records), 1)
                message = captured.records[0].getMessage()
                self.assertTrue(message.startswith("feedly_discovery "))
                data = json.loads(message[len("feedly_discovery "):])
                self.assertEqual(set(data), keys)
                self.assertEqual(data["method"], method)
                self.assertEqual(data["host"], host)
                self.assertEqual(data["path"], DISCOVERY_PATH)
                self.assertEqual(data["remote_addr"], "203.0.113.44")
                self.assertEqual(data["status"], 200)
                for key, header in (("user_agent", "User-Agent"), ("accept", "Accept"),
                                    ("accept_encoding", "Accept-Encoding"), ("x_forwarded_for", "X-Forwarded-For"),
                                    ("x_forwarded_proto", "X-Forwarded-Proto")):
                    self.assertEqual(data[key], headers[header])
                instant = datetime.fromisoformat(data["utc"].replace("Z", "+00:00"))
                self.assertEqual(instant.utcoffset(), timedelta(0))
                self.assertLess(abs((datetime.now(timezone.utc) - instant).total_seconds()), 30)
                for secret in ("discovery-cookie-secret", "discovery-auth-secret", "discovery-query-secret"):
                    self.assertNotIn(secret, message)


class ProductionBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline, cls.revised = load_app(True), load_app(False)
        cls.bundles = (cls.baseline, cls.revised)
        cls.fixture_xml = b'<rss version="2.0"><channel><item><title>Fixture</title><guid>fixture</guid></item></channel></rss>'
        cls.fixture_etag = hashlib.sha256(cls.fixture_xml).hexdigest()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(requests.Session, "request", side_effect=AssertionError("Network during comparison")))
        self.stack.enter_context(patch.object(werkzeug.wrappers.response, "http_date", return_value=HTTP_DATE))
        for bundle in self.bundles:
            bundle.module._snapshot = bundle.module._source_token = bundle.module._source_digest = None

    def responses(self, path, method="GET", host=HOSTS[0], extra=None):
        headers = {"Host": host, "User-Agent": TEST_AGENT}
        headers.update(extra or {})
        return [bundle.module.app.test_client(use_cookies=False).open(path, method=method,
                base_url="https://" + host, headers=headers) for bundle in self.bundles]

    def assert_same(self, responses, status):
        self.assertEqual(responses[0].status_code, status)
        self.assertEqual(response_signature(responses[0]), response_signature(responses[1]))

    def test_all_previous_routes_and_methods_remain_with_only_one_discovery_route(self):
        def rules(bundle):
            return {(rule.rule, rule.endpoint): frozenset(rule.methods) for rule in bundle.module.app.url_map.iter_rules()}
        before, after = rules(self.baseline), rules(self.revised)
        new = (DISCOVERY_PATH, "feedly_discovery_view")
        self.assertEqual(set(after) - set(before), {new})
        self.assertEqual(after[new], frozenset({"GET", "HEAD"}))
        self.assertEqual(before, {key: value for key, value in after.items() if key in before})

    def test_existing_probe_get_head_bytes_all_headers_status_match_pinned_production(self):
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    self.assert_same(self.responses(PROBE_PATH, method, host), 200)

    def test_home_and_health_get_head_bytes_headers_status_are_unchanged(self):
        for host in HOSTS:
            for path in ("/", "/healthz"):
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        responses = self.responses(path, method, host)
                        self.assert_same(responses, 200)
                        self.assertNotIn(DISCOVERY_PATH.encode(), responses[0].data)

    def test_rss_alias_200_304_503_get_head_match_pinned_production(self):
        tag = '"' + self.fixture_etag + '"'
        for available in (True, False):
            with ExitStack() as stack:
                for bundle in self.bundles:
                    stack.enter_context(patch.object(bundle.module, "rebuild_feed", return_value=False))
                    stack.enter_context(patch.object(bundle.module, "_load_snapshot", return_value=
                        ("fixture", self.fixture_xml, self.fixture_etag) if available else None))
                for host in HOSTS:
                    for path in ("/rss", "/rss.xml", "/feed.xml"):
                        for method in ("GET", "HEAD"):
                            for conditional in (False, True):
                                with self.subTest(available=available, host=host, path=path, method=method, conditional=conditional):
                                    status = (304 if conditional else 200) if available else 503
                                    self.assert_same(self.responses(path, method, host,
                                        {"If-None-Match": tag} if conditional else {}), status)

    def test_real_news_sitemap_200_503_get_head_match_pinned_production(self):
        item = {"url": "https://www.artbooms.com/blog/broken-mostra-palazzo-strozzi",
                "title": "Broken & arte — ARTBOOMS", "published": "2026-10-06T12:00:00Z"}
        for available in (True, False):
            with ExitStack() as stack:
                for bundle in self.bundles:
                    news = bundle.dependencies["news_sitemap"]
                    stack.enter_context(patch.object(news.datetime, "datetime", FrozenDateTime))
                    for loader in ("_load_local_items", "_load_remote_items"):
                        stack.enter_context(patch.object(news, loader, return_value=[copy.deepcopy(item)]) if available
                            else patch.object(news, loader, side_effect=ValueError("Unavailable fixture")))
                for host in HOSTS:
                    for method in ("GET", "HEAD"):
                        with self.subTest(available=available, host=host, method=method):
                            self.assert_same(self.responses("/news-sitemap.xml", method, host), 200 if available else 503)


if __name__ == "__main__":
    unittest.main(verbosity=2)