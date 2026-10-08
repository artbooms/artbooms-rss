"""Offline Linux/Gunicorn proof for the legacy RSS hostname migration."""

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
from urllib.parse import urlsplit

import requests


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "14a1ec7b5a4a8962bbf77c130055de40391002c0"
CUSTOM_HOST = "rss.artbooms.com"
LEGACY_HOST = "artbooms-rss-x6pc.onrender.com"
CANONICAL_FEED = "https://rss.artbooms.com/rss"
RSS_PATHS = ("/rss", "/rss.xml", "/feed.xml")
DIAGNOSTIC_PATHS = ("/feedly-probe.xml", "/feedly-discovery-test")
TEST_UA = "ARTBOOMS-Migration-Test/1.0"


def local_request(method, url, **kwargs):
    if not url.startswith("http://127.0.0.1:"):
        raise AssertionError("Non-local test destination: " + url)
    with requests.Session() as transport:
        transport.trust_env = False
        return transport.request(method, url, **kwargs)


def signature(response):
    # Gunicorn supplies Date from wall-clock time, outside application behavior.
    return response.status_code, response.content, {
        name.lower(): value for name, value in response.headers.items()
        if name.lower() != "date"
    }


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires native Linux/Gunicorn")
class FeedMigrationGunicornTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.servers = []
        baseline_files = {
            name: subprocess.check_output(
                ["git", "show", BASELINE + ":" + name], cwd=ROOT,
                env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
            )
            for name in ("app.py", "feedly_probe.py", "feedly_discovery.py")
        }
        # Sharing the exact absolute cache path keeps send_file's path-dependent
        # ETag and Last-Modified identical. Production source files remain read-only.
        cls.cache_path = cls.root / "articles_cache.json"
        cache_bytes = (ROOT / "cache/articles_cache.json").read_bytes()
        if not json.loads(cache_bytes).get("items"):
            raise AssertionError("The complete production cache must contain articles")
        cls.cache_path.write_bytes(cache_bytes)
        for name in ("baseline", "migration"):
            work = cls.root / name
            work.mkdir()
            if name == "baseline":
                for filename, content in baseline_files.items():
                    (work / filename).write_bytes(content)
            shutil.copyfile(ROOT / "gunicorn.conf.py", work / "gunicorn.conf.py")
            (work / "sitecustomize.py").write_text(
                "import requests\n"
                "def blocked(*args, **kwargs):\n"
                "    raise RuntimeError('Outbound HTTP forbidden in migration tests')\n"
                "requests.sessions.Session.request = blocked\n",
                encoding="utf-8",
            )
            (work / "migration_test_wsgi.py").write_text(
                "from datetime import datetime, timedelta, timezone\n"
                "from types import SimpleNamespace\n"
                "from functools import wraps\n"
                "import logging, os\n"
                "from flask import has_request_context, request\n"
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
                "def guard_helper(original, name):\n"
                "    @wraps(original)\n"
                "    def guarded(*args, **kwargs):\n"
                "        if has_request_context() and request.headers.get('X-ARTBOOMS-Test-No-Helpers') == '1':\n"
                "            raise AssertionError('Redirect invoked production helper: ' + name)\n"
                "        return original(*args, **kwargs)\n"
                "    return guarded\n"
                "for name in ('rebuild_feed', '_load_snapshot', '_read_file', 'generate_items', 'bootstrap_cache', 'start_background'):\n"
                "    setattr(app, name, guard_helper(getattr(app, name), name))\n"
                "_original_start_worker = app.start_worker\n"
                "def ready_start_worker():\n"
                "    _original_start_worker()\n"
                "    logging.getLogger('artbooms.migration_test').info('migration_test_worker_ready pid=%s', os.getpid())\n"
                "app.start_worker = ready_start_worker\n"
                "application = app.app\n",
                encoding="utf-8",
            )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            env = os.environ.copy()
            env.update(
                CACHE_PATH=str(cls.cache_path), FEED_PATH=str(work / "feed.xml"),
                PORT=str(port), CACHE_BOOTSTRAP_REMOTE="0", POPULATOR_ENABLED="0",
                PYTHONPATH=str(work) + os.pathsep + str(ROOT),
                PYTHONDONTWRITEBYTECODE="1",
            )
            logfile = open(work / "gunicorn.log", "w", encoding="utf-8")
            cls.addClassCleanup(logfile.close)
            process = subprocess.Popen(
                [sys.executable, "-m", "gunicorn", "--bind", f"127.0.0.1:{port}",
                 "--workers", "2", "--threads", "4", "migration_test_wsgi:application"],
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
                workers = set(re.findall(r"migration_test_worker_ready pid=(\d+)", text))
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

    def call(self, method, path="/rss", host=CUSTOM_HOST, extra=None, base=None):
        headers = {"Host": host, "User-Agent": TEST_UA}
        headers.update(extra or {})
        return local_request(
            method, (base or self.url) + path, headers=headers,
            timeout=5, allow_redirects=False,
        )

    def assert_same(self, baseline, revised):
        self.assertEqual(signature(revised), signature(baseline))

    def fingerprints(self):
        paths = (self.cache_path, self.baseline_work / "feed.xml", self.work / "feed.xml")
        return {
            str(path): (hashlib.sha256(path.read_bytes()).hexdigest(),
                        path.stat().st_mtime_ns, path.stat().st_size)
            for path in paths
        }

    def test_custom_rss_alias_bytes_all_headers_and_conditionals_match_baseline(self):
        hashes = {}
        for host in (CUSTOM_HOST, CUSTOM_HOST.upper(), CUSTOM_HOST + ":443"):
            for path in RSS_PATHS:
                with self.subTest(host=host, path=path):
                    baseline = self.call("GET", path, host, base=self.baseline_url)
                    revised = self.call("GET", path, host)
                    self.assertEqual(revised.status_code, 200)
                    self.assert_same(baseline, revised)
                    self.assertNotIn("Location", revised.headers)
                    hashes[host + path] = hashlib.sha256(revised.content).hexdigest()
                    tag = baseline.headers["ETag"]
                    validators = (
                        ({}, 200),
                        ({"If-None-Match": tag}, 304),
                        ({"If-None-Match": "W/" + tag}, 304),
                        ({"If-None-Match": "*"}, 304),
                        ({"If-None-Match": '"different"'}, 200),
                    )
                    for method in ("GET", "HEAD"):
                        for extra, expected in validators:
                            b = self.call(method, path, host, extra, self.baseline_url)
                            r = self.call(method, path, host, extra)
                            self.assert_same(b, r)
                            self.assertEqual(r.status_code, expected)
                            if method == "HEAD" or expected == 304:
                                self.assertEqual(r.content, b"")
        print("Migration custom RSS SHA256 equals baseline:", json.dumps(hashes, sort_keys=True))

    def test_legacy_all_aliases_get_head_redirect_before_production_helpers(self):
        guard = {"X-ARTBOOMS-Test-No-Helpers": "1"}
        hosts = (LEGACY_HOST, LEGACY_HOST.upper(), LEGACY_HOST + ":443", LEGACY_HOST + ":80")
        for host in hosts:
            for path in RSS_PATHS:
                with self.subTest(host=host, path=path):
                    get = self.call("GET", path, host, guard)
                    head = self.call("HEAD", path, host, guard)
                    for response in (get, head):
                        self.assertEqual(response.status_code, 301)
                        self.assertEqual(response.headers["Location"], CANONICAL_FEED)
                        self.assertNotIn("ETag", response.headers)
                    self.assertEqual(head.content, b"")
                    self.assertEqual(signature(head)[2], signature(get)[2])
                    self.assertEqual(int(head.headers["Content-Length"]), len(get.content))

    def test_manual_follow_on_same_local_server_has_one_redirect_and_no_loop(self):
        canonical = urlsplit(CANONICAL_FEED)
        self.assertEqual((canonical.scheme, canonical.netloc, canonical.path),
                         ("https", CUSTOM_HOST, "/rss"))
        self.assertEqual((canonical.query, canonical.fragment), ("", ""))
        for path in RSS_PATHS:
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    old = self.call(method, path, LEGACY_HOST)
                    self.assertEqual(old.status_code, 301)
                    self.assertEqual(old.headers["Location"], CANONICAL_FEED)
                    # Follow only by changing Host on the same loopback server.
                    followed = self.call(method, canonical.path, canonical.netloc)
                    expected = self.call(method, canonical.path, CUSTOM_HOST, base=self.baseline_url)
                    self.assertEqual(followed.status_code, 200)
                    self.assertNotIn("Location", followed.headers)
                    self.assert_same(expected, followed)

    def test_actual_host_alone_controls_redirect_forwarding_cannot_spoof_it(self):
        cases = (
            (CUSTOM_HOST, LEGACY_HOST, 200),
            (LEGACY_HOST, CUSTOM_HOST, 301),
            ("untrusted.example", LEGACY_HOST, 200),
            (LEGACY_HOST + ".untrusted.example", LEGACY_HOST, 200),
            (LEGACY_HOST + ".", LEGACY_HOST, 200),
        )
        for actual, forwarded, expected in cases:
            extra = {
                "X-Forwarded-Host": forwarded,
                "Forwarded": "host=" + forwarded + ";proto=https",
                "X-Forwarded-Proto": "https",
            }
            if expected == 301:
                extra["X-ARTBOOMS-Test-No-Helpers"] = "1"
            for path in RSS_PATHS:
                for method in ("GET", "HEAD"):
                    with self.subTest(host=actual, path=path, method=method):
                        response = self.call(method, path, actual, extra)
                        self.assertEqual(response.status_code, expected)
                        if expected == 301:
                            self.assertEqual(response.headers["Location"], CANONICAL_FEED)
                        else:
                            baseline = self.call(method, path, actual, extra, self.baseline_url)
                            self.assert_same(baseline, response)
                            self.assertNotIn("Location", response.headers)

    def test_other_production_routes_get_head_bytes_all_headers_match_baseline(self):
        paths = ("/news-sitemap.xml", "/", "/healthz", "/debug/cache", "/cache/download")
        for host in (CUSTOM_HOST, LEGACY_HOST):
            for path in paths:
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        baseline = self.call(method, path, host, base=self.baseline_url)
                        revised = self.call(method, path, host)
                        self.assertEqual(revised.status_code, 200)
                        self.assert_same(baseline, revised)
                        self.assertNotIn("Location", revised.headers)
                        if method == "HEAD":
                            self.assertEqual(revised.content, b"")
                if path == "/cache/download":
                    baseline = self.call("GET", path, host, base=self.baseline_url)
                    for validator in (baseline.headers["ETag"], "*"):
                        extra = {"If-None-Match": validator}
                        b = self.call("GET", path, host, extra, self.baseline_url)
                        r = self.call("GET", path, host, extra)
                        self.assert_same(b, r)
                        self.assertEqual(r.status_code, 200)

    def test_removed_diagnostic_routes_are_404_on_both_hosts_get_and_head(self):
        for host in (CUSTOM_HOST, LEGACY_HOST):
            for path in DIAGNOSTIC_PATHS:
                for method in ("GET", "HEAD"):
                    with self.subTest(host=host, path=path, method=method):
                        before = self.call(method, path, host, base=self.baseline_url)
                        after = self.call(method, path, host)
                        self.assertEqual(before.status_code, 200)
                        self.assertEqual(after.status_code, 404)
                        self.assertNotIn("Location", after.headers)
                        if method == "HEAD":
                            self.assertEqual(after.content, b"")

    def test_legacy_redirects_and_removed_diagnostics_do_not_mutate_cache_or_feed(self):
        before = self.fingerprints()
        guard = {"X-ARTBOOMS-Test-No-Helpers": "1"}
        for path in RSS_PATHS:
            for method in ("GET", "HEAD"):
                self.assertEqual(self.call(method, path, LEGACY_HOST, guard).status_code, 301)
        for host in (CUSTOM_HOST, LEGACY_HOST):
            for path in DIAGNOSTIC_PATHS:
                for method in ("GET", "HEAD"):
                    self.assertEqual(self.call(method, path, host).status_code, 404)
        self.assertEqual(self.fingerprints(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
