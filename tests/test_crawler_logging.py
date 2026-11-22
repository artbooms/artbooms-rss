"""Evidence completeness and privacy boundaries for crawler request logs."""
import datetime
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gunicorn.glogging import Logger, SafeAtoms
from crawler_logging import CrawlerLogger, google_context


ROOT = Path(__file__).resolve().parents[1]


class CrawlerLoggingTests(unittest.TestCase):
    def test_browser_and_unrelated_google_agents_do_not_gain_ip_logging(self):
        for agent in ("Mozilla/5.0 Chrome/131", "GoogleOther", "Google-Extended", "", None):
            self.assertEqual(google_context({"a": agent, "h": "192.0.2.10"}), "")

    def test_declared_agent_is_not_treated_as_a_verified_google_ip(self):
        for agent, family in (("Mozilla/5.0 (compatible; Googlebot/2.1)", "googlebot"),
                              ("Googlebot-Image/1.0", "googlebot"),
                              ("FeedFetcher-Google; (+http://www.google.com/feedfetcher.html)", "feedfetcher"),
                              ("Google-InspectionTool/1.0", "inspection")):
            result = google_context({"a": agent, "{cf-connecting-ip}i": "192.0.2.10"})
            self.assertIn(f"google_declared={family}", result)
            self.assertIn("identity=unverified", result)
            self.assertIn("cf_ip=192.0.2.10", result)

    def test_only_allowlisted_validated_fields_are_added(self):
        atoms = {"a": "Googlebot", "h": "10.0.0.1", "B": 103,
                 "{host}i": "rss.artbooms.com", "{rndr-id}i": "request-123",
                 "{cf-ray}i": "abc123-FRA", "{cf-connecting-ip}i": "2001:db8::1",
                 "{x-forwarded-for}i": "192.0.2.10, 10.0.0.2",
                 "q": "token=secret-query", "{authorization}i": "secret-auth",
                 "{cookie}i": "secret-cookie", "{referer}i": "secret-referrer"}
        result = google_context(atoms)
        self.assertIn("rndr_id=request-123", result)
        self.assertIn("cf_ray=abc123-FRA", result)
        self.assertIn("cf_ip=2001:db8::1", result)
        self.assertIn("xff=192.0.2.10,10.0.0.2", result)
        self.assertIn("app_bytes=103", result)
        self.assertNotIn("secret", result)

    def test_invalid_headers_cannot_inject_records_and_context_is_bounded(self):
        result = google_context({"a": "Googlebot", "{host}i": "evil\nstatus=200",
                                 "{rndr-id}i": "secret?token=123", "{cf-ray}i": "x" * 10000,
                                 "{cf-connecting-ip}i": "192.0.2.1\r\nforged",
                                 "{x-forwarded-for}i": "x" * 2000})
        self.assertNotIn("\n", result)
        self.assertNotIn("\r", result)
        self.assertNotIn("secret", result)
        self.assertLess(len(result), 250)
        self.assertIn("cf_ip=-", result)
        scoped = google_context({"a": "Googlebot", "{cf-connecting-ip}i": "fe80::1%zone\nforged"})
        self.assertNotIn("forged", scoped)
        self.assertNotIn("\n", scoped)

    def test_real_gunicorn_atoms_preserve_status_path_duration_and_fields(self):
        logger = CrawlerLogger.__new__(CrawlerLogger)
        resp = SimpleNamespace(status="304 Not Modified", sent=0, headers=[])
        req = SimpleNamespace(headers=[("host", "rss.artbooms.com"),
                                       ("rndr-id", "request-123"), ("cf-ray", "abc-FRA")])
        environ = {"REQUEST_METHOD": "GET", "RAW_URI": "/rss?key=secret-query",
                   "SERVER_PROTOCOL": "HTTP/1.1", "PATH_INFO": "/rss",
                   "QUERY_STRING": "key=secret-query", "HTTP_USER_AGENT": "Googlebot/2.1",
                   "REMOTE_ADDR": "10.0.0.1"}
        elapsed = datetime.timedelta(microseconds=1200)
        with patch.object(logger, "now", return_value="[22/Nov/2026:23:00:00 +0000]"):
            baseline = Logger.atoms(logger, resp, req, environ, elapsed)
            actual = logger.atoms(resp, req, environ, elapsed)
        self.assertEqual({k: v for k, v in actual.items() if k != "google_context"}, baseline)
        spec = importlib.util.spec_from_file_location("crawler_config_test", ROOT / "gunicorn.conf.py")
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        line = config.access_log_format % SafeAtoms(actual)
        self.assertIn('path="/rss" status=304 duration=0.001200', line)
        self.assertIn("rndr_id=request-123", line)
        self.assertNotIn("secret-query", line)
        self.assertEqual(config.logger_class, "crawler_logging.CrawlerLogger")

    def test_context_failure_keeps_original_record_and_does_not_raise(self):
        logger = CrawlerLogger.__new__(CrawlerLogger)
        with patch.object(Logger, "atoms", return_value={"s": "200", "U": "/rss"}), \
                patch("crawler_logging.google_context", side_effect=ValueError("unexpected")):
            actual = logger.atoms(None, None, None, None)
        self.assertEqual(actual["s"], "200")
        self.assertEqual(actual["U"], "/rss")
        self.assertEqual(actual["google_context"], " google_trace_error=1")


if __name__ == "__main__":
    unittest.main()
