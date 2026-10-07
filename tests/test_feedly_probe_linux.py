"""Offline Linux/Gunicorn integration for the temporary diagnostic route."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

import feedparser
from lxml import etree
import requests


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "a173ec0dbd5838278c6c002b848430d859ac56c6"
HOSTS = ("rss.artbooms.com", "artbooms-rss-x6pc.onrender.com")
PATH = "/feedly-probe.xml"


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires native Linux/Gunicorn; run CI")
class FeedlyProbeGunicornTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.servers = []
        baseline = subprocess.check_output(["git", "show", BASELINE + ":app.py"], cwd=ROOT)
        source_cache = json.loads((ROOT / "cache/articles_cache.json").read_text(encoding="utf-8"))
        # Compare production responses using the complete pinned cache snapshot.
        fixture = source_cache
        for name, original in (("baseline", baseline), ("probe", None)):
            work = Path(cls.temp.name) / name
            work.mkdir()
            (work / "articles_cache.json").write_text(json.dumps(fixture), encoding="utf-8")
            if original is not None:
                (work / "app.py").write_bytes(original)
            shutil.copyfile(ROOT / "gunicorn.conf.py", work / "gunicorn.conf.py")
            # The application cannot make external requests in these tests.
            (work / "sitecustomize.py").write_text(
                "import requests\n"
                "def blocked(*args, **kwargs):\n"
                "    raise RuntimeError('Outbound HTTP forbidden in probe integration tests')\n"
                "requests.sessions.Session.request = blocked\n", encoding="utf-8")
            (work / "probe_test_wsgi.py").write_text(
                "from datetime import datetime, timedelta, timezone\n"
                "from types import SimpleNamespace\n"
                "import app, rss_generator, news_sitemap\n"
                "class Clock(datetime):\n"
                "    @classmethod\n"
                "    def now(cls, tz=None):\n"
                "        value = cls(2026, 10, 7, 12, tzinfo=timezone.utc)\n"
                "        return value.astimezone(tz) if tz else value.replace(tzinfo=None)\n"
                "    @classmethod\n"
                "    def utcnow(cls):\n"
                "        return cls(2026, 10, 7, 12)\n"
                "rss_generator.datetime = Clock\n"
                "news_sitemap.datetime = SimpleNamespace(datetime=Clock, timedelta=timedelta, timezone=timezone)\n"
                "application = app.app\n", encoding="utf-8")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = os.environ.copy()
            env.update(
                CACHE_PATH=str(work / "articles_cache.json"),
                FEED_PATH=str(work / "feed.xml"), PORT=str(port),
                CACHE_BOOTSTRAP_REMOTE="0", POPULATOR_ENABLED="0",
                PYTHONPATH=str(work) + os.pathsep + str(ROOT),
                PYTHONDONTWRITEBYTECODE="1",
            )
            logfile = open(work / "gunicorn.log", "w", encoding="utf-8")
            cls.addClassCleanup(logfile.close)
            proc = subprocess.Popen(
                [sys.executable, "-m", "gunicorn", "--bind", f"127.0.0.1:{port}",
                 "--workers", "2", "--threads", "4", "probe_test_wsgi:application"],
                cwd=work, env=env, stdout=logfile, stderr=subprocess.STDOUT,
            )
            def stop(process=proc):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            cls.addClassCleanup(stop)
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise AssertionError((work / "gunicorn.log").read_text())
                try:
                    if requests.get(url + "/healthz", timeout=1).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Gunicorn not ready: " + (work / "gunicorn.log").read_text())
            cls.servers.append((url, work, proc))
        cls.baseline_url, cls.baseline_work, _ = cls.servers[0]
        cls.url, cls.work, _ = cls.servers[1]

    def call(self, method, path=PATH, host=HOSTS[0], extra=None, base=None):
        headers = {"Host": host, "User-Agent": "Feedly-Probe-Test/1.0"}
        headers.update(extra or {})
        return requests.request(method, (base or self.url) + path, headers=headers,
                                timeout=5, allow_redirects=False)

    def test_get_and_head_on_both_hosts_with_two_xml_parsers(self):
        for host in HOSTS:
            with self.subTest(host=host):
                get = self.call("GET", host=host)
                head = self.call("HEAD", host=host)
                self.assertEqual(get.status_code, 200)
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertLess(len(get.content), 4096)
                for r in (get, head):
                    self.assertEqual(r.headers["Content-Type"], "application/rss+xml; charset=utf-8")
                    self.assertEqual(r.headers["Cache-Control"], "no-store")
                    self.assertEqual(r.headers["X-Robots-Tag"], "noindex, nofollow")
                    self.assertNotIn("Location", r.headers)
                    self.assertNotIn("ETag", r.headers)
                    self.assertNotIn("Last-Modified", r.headers)
                    self.assertEqual(int(r.headers["Content-Length"]), len(get.content))
                xml = etree.fromstring(get.content, etree.XMLParser(recover=False, no_network=True))
                parsed = feedparser.parse(get.content)
                self.assertFalse(parsed.bozo)
                self.assertEqual(parsed.version, "rss20")
                self.assertEqual(len(parsed.entries), 1)
                self.assertEqual(xml.tag, "rss")
                self.assertEqual(xml.get("version"), "2.0")
                self.assertEqual(xml.findtext("channel/title"), "ARTBOOMS Feedly diagnostic probe")
                self.assertEqual(xml.findtext("channel/link"), "https://www.artbooms.com/")
                atom = xml.find("channel/{http://www.w3.org/2005/Atom}link")
                self.assertEqual(atom.get("href"), "https://" + host + PATH)

    def test_only_authorized_host_controls_self_not_forwarding_headers(self):
        for host in HOSTS:
            get = self.call("GET", host=host.upper() + ":443", extra={
                "X-Forwarded-Host": "untrusted.example",
                "Forwarded": "host=untrusted.example;proto=http",
                "X-Forwarded-Proto": "https",
            })
            self.assertEqual(get.status_code, 200)
            self.assertIn(("https://" + host + PATH).encode(), get.content)
            self.assertNotIn(b"untrusted.example", get.content)
        for host in ("untrusted.example", HOSTS[0] + ":80", HOSTS[0] + "."):
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    r = self.call(method, host=host, extra={"X-Forwarded-Host": HOSTS[0]})
                    self.assertEqual(r.status_code, 400)
                    self.assertEqual(r.headers["Cache-Control"], "no-store")
                    self.assertEqual(r.headers["X-Robots-Tag"], "noindex, nofollow")
                    self.assertNotIn("Location", r.headers)
                    if method == "HEAD":
                        self.assertEqual(r.content, b"")

    def test_probe_does_not_touch_cache_feed_or_conditional_behavior(self):
        paths = (self.work / "articles_cache.json", self.work / "feed.xml")
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        for host in HOSTS:
            r = self.call("GET", host=host, extra={
                "If-None-Match": "*", "If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"})
            self.assertEqual(r.status_code, 200)
        after = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        self.assertEqual(before, after)
        self.assertEqual(self.call("POST").status_code, 405)
        self.assertEqual(self.call("OPTIONS").status_code, 405)

    def test_dedicated_logging_records_requested_fields_and_no_secrets(self):
        marker = "Feedly-Probe-Log-Test"
        extra = {
            "User-Agent": marker,
            "Accept": "application/rss+xml",
            "Accept-Encoding": "gzip, identity",
            "X-Forwarded-For": "192.0.2.19",
            "X-Forwarded-Proto": "https",
            "Cookie": "probe_cookie=DO_NOT_LOG_COOKIE",
            "Authorization": "Bearer DO_NOT_LOG_AUTHORIZATION",
        }
        for method in ("GET", "HEAD"):
            self.assertEqual(self.call(method, PATH + "?token=DO_NOT_LOG_QUERY", extra=extra).status_code, 200)
        self.call("GET", "/healthz", extra=extra)
        lines = (self.work / "gunicorn.log").read_text(encoding="utf-8").splitlines()
        records = [json.loads(line.split("feedly_probe ", 1)[1])
                   for line in lines if "feedly_probe " in line]
        own = [r for r in records if r["user_agent"] == marker]
        self.assertEqual(len(own), 2)
        self.assertEqual({r["method"] for r in own}, {"GET", "HEAD"})
        keys = {"utc", "method", "host", "path", "user_agent", "accept", "accept_encoding",
                "x_forwarded_for", "x_forwarded_proto", "remote_addr", "status"}
        for r in own:
            self.assertEqual(set(r), keys)
            self.assertEqual(r["host"], HOSTS[0])
            self.assertEqual(r["path"], PATH)
            self.assertEqual(r["accept"], extra["Accept"])
            self.assertEqual(r["accept_encoding"], extra["Accept-Encoding"])
            self.assertEqual(r["x_forwarded_for"], extra["X-Forwarded-For"])
            self.assertEqual(r["x_forwarded_proto"], "https")
            self.assertEqual(r["remote_addr"], "127.0.0.1")
            self.assertEqual(r["status"], 200)
            self.assertEqual(datetime.fromisoformat(r["utc"].replace("Z", "+00:00")).utcoffset(),
                             timezone.utc.utcoffset(None))
        text = "\n".join(line for line in lines if "feedly_probe " in line)
        for secret in ("DO_NOT_LOG_COOKIE", "DO_NOT_LOG_AUTHORIZATION", "DO_NOT_LOG_QUERY"):
            self.assertNotIn(secret, text)

    def test_production_http_bytes_headers_and_conditionals_equal_baseline(self):
        # Date is generated by Gunicorn from wall-clock time, not by either app.
        def signature(r):
            return r.status_code, r.content, {
                k.lower(): v for k, v in r.headers.items() if k.lower() != "date"
            }
        hashes = {}
        for host in HOSTS:
            for path in ("/rss", "/rss.xml", "/feed.xml", "/news-sitemap.xml"):
                with self.subTest(host=host, path=path):
                    baseline = self.call("GET", path, host, base=self.baseline_url)
                    revised = self.call("GET", path, host)
                    self.assertEqual(signature(revised), signature(baseline))
                    self.assertEqual(revised.status_code, 200)
                    hashes[path] = hashlib.sha256(revised.content).hexdigest()
                    for method in ("HEAD", "GET"):
                        extra = {"If-None-Match": baseline.headers["ETag"]} if method == "GET" and "ETag" in baseline.headers else {}
                        b = self.call(method, path, host, extra, self.baseline_url)
                        r = self.call(method, path, host, extra)
                        self.assertEqual(signature(r), signature(b))
                        if method == "GET" and extra:
                            self.assertEqual(r.status_code, 304)
                        if method == "HEAD":
                            self.assertEqual(r.content, b"")
        print("Production HTTP SHA256 matches pinned baseline:", json.dumps(hashes, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
