"""Local Linux/Gunicorn checks for discovery, with current production as baseline."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from lxml import etree
import requests


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "abbd6acadd18a41f20808a721044fe07d6206a25"
HOSTS = ("rss.artbooms.com", "artbooms-rss-x6pc.onrender.com")
DISCOVERY_PATH = "/feedly-discovery-test"
PROBE_PATH = "/feedly-probe.xml"
RSS_HREF = "https://artbooms-rss-x6pc.onrender.com/feedly-probe.xml"
TEST_UA = "ARTBOOMS-Discovery-Test/1.0"
LOG_KEYS = {
    "utc", "method", "host", "path", "user_agent", "accept",
    "accept_encoding", "x_forwarded_for", "x_forwarded_proto",
    "remote_addr", "status",
}


def local_request(method, url, **kwargs):
    """The test client never uses an environment-configured external proxy."""
    if not url.startswith("http://127.0.0.1:"):
        raise AssertionError("Non-local test destination: " + url)
    with requests.Session() as transport:
        transport.trust_env = False
        return transport.request(method, url, **kwargs)


def signature(response):
    # Gunicorn's wall-clock Date header is independent of application behavior.
    return response.status_code, response.content, {
        key.lower(): value for key, value in response.headers.items()
        if key.lower() != "date"
    }


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires native Linux/Gunicorn")
class FeedlyDiscoveryGunicornTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.servers = []
        baseline_app = subprocess.check_output(
            ["git", "show", BASELINE + ":app.py"], cwd=ROOT,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
        )
        # Both servers receive the full, identical production cache snapshot.
        cache_bytes = (ROOT / "cache/articles_cache.json").read_bytes()
        cache_data = json.loads(cache_bytes)
        if not cache_data.get("items"):
            raise AssertionError("The complete source cache must contain articles")
        for name, source in (("baseline", baseline_app), ("discovery", None)):
            work = Path(temporary.name) / name
            work.mkdir()
            (work / "articles_cache.json").write_bytes(cache_bytes)
            if source is not None:
                (work / "app.py").write_bytes(source)
            shutil.copyfile(ROOT / "gunicorn.conf.py", work / "gunicorn.conf.py")
            (work / "sitecustomize.py").write_text(
                "import requests\n"
                "def blocked(*args, **kwargs):\n"
                "    raise RuntimeError('Outbound HTTP forbidden in discovery tests')\n"
                "requests.sessions.Session.request = blocked\n",
                encoding="utf-8",
            )
            (work / "discovery_test_wsgi.py").write_text(
                "from datetime import datetime, timedelta, timezone\n"
                "from types import SimpleNamespace\n"
                "import logging, os\n"
                "import app, rss_generator, news_sitemap\n"
                "class Clock(datetime):\n"
                "    @classmethod\n"
                "    def now(cls, tz=None):\n"
                "        value = cls(2026, 10, 8, 12, tzinfo=timezone.utc)\n"
                "        return value.astimezone(tz) if tz else value.replace(tzinfo=None)\n"
                "    @classmethod\n"
                "    def utcnow(cls):\n"
                "        return cls(2026, 10, 8, 12)\n"
                "rss_generator.datetime = Clock\n"
                "news_sitemap.datetime = SimpleNamespace(datetime=Clock, timedelta=timedelta, timezone=timezone)\n"
                "_original_start_worker = app.start_worker\n"
                "def ready_start_worker():\n"
                "    _original_start_worker()\n"
                "    logging.getLogger('artbooms.discovery_test').info('discovery_test_worker_ready pid=%s', os.getpid())\n"
                "app.start_worker = ready_start_worker\n"
                "application = app.app\n",
                encoding="utf-8",
            )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            env = os.environ.copy()
            env.update(
                CACHE_PATH=str(work / "articles_cache.json"),
                FEED_PATH=str(work / "feed.xml"),
                PORT=str(port),
                CACHE_BOOTSTRAP_REMOTE="0",
                POPULATOR_ENABLED="0",
                PYTHONPATH=str(work) + os.pathsep + str(ROOT),
                PYTHONDONTWRITEBYTECODE="1",
            )
            logfile = open(work / "gunicorn.log", "w", encoding="utf-8")
            cls.addClassCleanup(logfile.close)
            process = subprocess.Popen(
                [sys.executable, "-m", "gunicorn", "--bind", f"127.0.0.1:{port}",
                 "--workers", "2", "--threads", "4", "discovery_test_wsgi:application"],
                cwd=work, env=env, stdout=logfile, stderr=subprocess.STDOUT,
            )

            def stop(child=process):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)

            cls.addClassCleanup(stop)
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                text = (work / "gunicorn.log").read_text(encoding="utf-8")
                if process.poll() is not None:
                    raise AssertionError(text)
                # Wait for both real startup hooks before recording file metadata.
                workers = set(re.findall(r"discovery_test_worker_ready pid=(\d+)", text))
                try:
                    ready = local_request(
                        "GET", url + "/healthz",
                        headers={"User-Agent": TEST_UA}, timeout=1,
                    ).status_code == 200
                    if len(workers) == 2 and ready:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Gunicorn not ready:\n" + text)
            cls.servers.append((url, work, process))
        cls.baseline_url, cls.baseline_work, _ = cls.servers[0]
        cls.url, cls.work, _ = cls.servers[1]

    def call(self, method, path=DISCOVERY_PATH, host=HOSTS[0], extra=None, base=None):
        headers = {"Host": host, "User-Agent": TEST_UA}
        headers.update(extra or {})
        return local_request(
            method, (base or self.url) + path, headers=headers,
            timeout=5, allow_redirects=False,
        )

    def assert_same(self, baseline, revised):
        self.assertEqual(signature(revised), signature(baseline))

    def test_html_get_head_both_hosts_have_one_real_head_alternate(self):
        documents = []
        for host in HOSTS:
            with self.subTest(host=host):
                get = self.call("GET", host=host)
                head = self.call("HEAD", host=host)
                self.assertEqual(get.status_code, 200)
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertEqual(signature(head)[2], signature(get)[2])
                self.assertLess(len(get.content), 4096)
                self.assertTrue(get.content.lstrip().lower().startswith(b"<!doctype html>"))
                for response in (get, head):
                    self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(int(response.headers["Content-Length"]), len(get.content))
                    for header in ("ETag", "Last-Modified", "Set-Cookie", "X-Robots-Tag", "Location"):
                        self.assertNotIn(header, response.headers)
                # Explicit raw head is required; an HTML parser must not create it.
                raw_heads = re.findall(
                    rb"<head\b[^>]*>(.*?)</head\s*>", get.content,
                    re.IGNORECASE | re.DOTALL,
                )
                self.assertEqual(len(raw_heads), 1)
                parser = etree.HTMLParser(recover=False, no_network=True)
                document = etree.fromstring(get.content, parser)
                self.assertEqual(len(document.xpath("/html/head")), 1)
                alternates = document.xpath(
                    "//link[contains(concat(' ', normalize-space(@rel), ' '), ' alternate ')"
                    " and @type='application/rss+xml']"
                )
                self.assertEqual(len(alternates), 1)
                alternate = alternates[0]
                self.assertEqual(alternate.getparent().tag, "head")
                self.assertEqual(alternate.get("href"), RSS_HREF)
                self.assertEqual(alternate.get("title"), "ARTBOOMS Feed Discovery Test")
                raw_head = etree.fromstring(b"<html><head>" + raw_heads[0] + b"</head></html>", parser)
                self.assertEqual(raw_head.xpath("/html/head/link[@href=$href]/@href", href=RSS_HREF), [RSS_HREF])
                self.assertEqual(document.xpath("/html/head/title/text()"), ["ARTBOOMS Feed Discovery Test"])
                self.assertIn("ARTBOOMS feed discovery test", "".join(document.xpath("/html/body//text()")))
                documents.append(get.content)
        self.assertEqual(documents[0], documents[1])

    def test_static_html_ignores_forwarding_query_and_conditionals(self):
        original = self.call("GET").content
        extra = {
            "X-Forwarded-Host": "untrusted.example",
            "Forwarded": "host=untrusted.example;proto=http",
            "X-Forwarded-Proto": "http",
            "If-None-Match": "*",
            "If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT",
        }
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    response = self.call(method, DISCOVERY_PATH + "?attempt=second", host, extra)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, original if method == "GET" else b"")
                    self.assertNotIn("ETag", response.headers)
                    self.assertNotIn("Last-Modified", response.headers)

    def test_only_get_and_head_are_registered(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                response = self.call(method)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(
                    {value.strip() for value in response.headers["Allow"].split(",")},
                    {"GET", "HEAD"},
                )

    def test_dedicated_utc_logs_are_exclusive_and_omit_sensitive_fields(self):
        marker = "ARTBOOMS-Discovery-Test-Log"
        extra = {
            "User-Agent": marker,
            "Accept": "text/html, application/xhtml+xml",
            "Accept-Encoding": "gzip, identity",
            "X-Forwarded-For": "192.0.2.28",
            "X-Forwarded-Proto": "https",
            "Cookie": "discovery_cookie=DO_NOT_LOG_DISCOVERY_COOKIE",
            "Authorization": "Bearer DO_NOT_LOG_DISCOVERY_AUTH",
            "Referer": "https://example.invalid/?token=DO_NOT_LOG_DISCOVERY_REFERER",
        }
        before = datetime.now(timezone.utc)
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                self.assertEqual(self.call(
                    method, DISCOVERY_PATH + "?token=DO_NOT_LOG_DISCOVERY_QUERY", host, extra,
                ).status_code, 200)
        # The discovery logger must not acquire requests for any other endpoint.
        self.call("GET", "/healthz", extra=extra)
        self.call("GET", PROBE_PATH, extra=extra)
        after = datetime.now(timezone.utc)
        lines = (self.work / "gunicorn.log").read_text(encoding="utf-8").splitlines()
        records = [
            json.loads(line.split("feedly_discovery ", 1)[1])
            for line in lines if "feedly_discovery " in line
        ]
        own = [record for record in records if record["user_agent"] == marker]
        self.assertEqual(len(own), 4)
        self.assertEqual(
            {(record["host"], record["method"]) for record in own},
            {(host, method) for host in HOSTS for method in ("GET", "HEAD")},
        )
        for record in own:
            self.assertEqual(set(record), LOG_KEYS)
            self.assertEqual(record["path"], DISCOVERY_PATH)
            self.assertEqual(record["status"], 200)
            self.assertEqual(record["remote_addr"], "127.0.0.1")
            for field, header in (
                ("accept", "Accept"), ("accept_encoding", "Accept-Encoding"),
                ("x_forwarded_for", "X-Forwarded-For"),
                ("x_forwarded_proto", "X-Forwarded-Proto"),
            ):
                self.assertEqual(record[field], extra[header])
            self.assertTrue(record["utc"].endswith("Z"))
            stamp = datetime.fromisoformat(record["utc"].replace("Z", "+00:00"))
            self.assertEqual(stamp.utcoffset(), timezone.utc.utcoffset(None))
            # Log timestamps have millisecond precision.
            self.assertLessEqual(before.timestamp() - 0.001, stamp.timestamp())
            self.assertLessEqual(stamp, after)
        diagnostic = "\n".join(line for line in lines if "feedly_discovery " in line)
        for secret in (
            "DO_NOT_LOG_DISCOVERY_COOKIE", "DO_NOT_LOG_DISCOVERY_AUTH",
            "DO_NOT_LOG_DISCOVERY_REFERER", "DO_NOT_LOG_DISCOVERY_QUERY",
        ):
            self.assertNotIn(secret, diagnostic)

    def test_existing_probe_bytes_status_and_headers_match_current_baseline(self):
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    baseline = self.call(method, PROBE_PATH, host, base=self.baseline_url)
                    revised = self.call(method, PROBE_PATH, host)
                    self.assertEqual(revised.status_code, 200)
                    self.assert_same(baseline, revised)
                    self.assertNotIn("X-Robots-Tag", revised.headers)
                    if method == "HEAD":
                        self.assertEqual(revised.content, b"")

    def test_production_routes_bytes_status_headers_and_304_match_baseline(self):
        hashes = {}
        for host in HOSTS:
            for path in ("/rss", "/rss.xml", "/feed.xml", "/news-sitemap.xml", "/", "/healthz"):
                with self.subTest(host=host, path=path):
                    baseline = self.call("GET", path, host, base=self.baseline_url)
                    revised = self.call("GET", path, host)
                    self.assertEqual(revised.status_code, 200)
                    self.assert_same(baseline, revised)
                    hashes[host + path] = hashlib.sha256(revised.content).hexdigest()
                    b_head = self.call("HEAD", path, host, base=self.baseline_url)
                    r_head = self.call("HEAD", path, host)
                    self.assert_same(b_head, r_head)
                    self.assertEqual(r_head.content, b"")
                    if "ETag" in baseline.headers:
                        for validator in (baseline.headers["ETag"], "*"):
                            extra = {"If-None-Match": validator}
                            b = self.call("GET", path, host, extra, self.baseline_url)
                            r = self.call("GET", path, host, extra)
                            self.assert_same(b, r)
                            self.assertEqual(r.status_code, 304)
                            self.assertEqual(r.content, b"")
        print("Discovery production SHA256 equals current baseline:", json.dumps(hashes, sort_keys=True))

    def test_discovery_requests_leave_cache_and_feed_fingerprints_unchanged(self):
        def fingerprints(work):
            return {
                name: (hashlib.sha256((work / name).read_bytes()).hexdigest(),
                       (work / name).stat().st_mtime_ns, (work / name).stat().st_size)
                for name in ("articles_cache.json", "feed.xml")
            }
        before = {str(work): fingerprints(work) for work in (self.baseline_work, self.work)}
        for host in HOSTS:
            for method in ("GET", "HEAD"):
                self.assertEqual(self.call(method, host=host).status_code, 200)
        after = {str(work): fingerprints(work) for work in (self.baseline_work, self.work)}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
