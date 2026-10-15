"""Offline XML checks for the approved category and WebSub discovery links."""
import copy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import unescape
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import feedparser
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import editorial_taxonomy as taxonomy
import news_sitemap as news
import rss_generator as generator

CANONICAL = "https://rss.artbooms.com/rss"
HUB = "https://pubsubhubbub.appspot.com/"
INSTANT = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
      "n": "http://www.google.com/schemas/sitemap-news/0.9",
      "atom": "http://www.w3.org/2005/Atom",
      "dc": "http://purl.org/dc/elements/1.1/"}


class FrozenDateTime(datetime):
    @classmethod
    def utcnow(cls):
        return INSTANT.replace(tzinfo=None)


def article(index=0, **overrides):
    item = {
        "url": "https://www.artbooms.com/blog/editorial-fixture-%04d" % index,
        "title": "Museo &amp; arte %d — ARTBOOMS" % index,
        "description": "Descrizione %d" % index,
        "author": "Autrice ospite",
        "image": "https://example.org/immagine-%d.jpg" % index,
        "published": (INSTANT - timedelta(hours=1, seconds=index)).isoformat(),
        "modified": "2026-10-15T11:30:00+00:00",
        "categories": ["arte contemporanea", "arte e cultura"],
        "tags": ["tag Squarespace"],
    }
    item.update(overrides)
    return item


class RssEditorialTests(unittest.TestCase):
    def test_more_than_1100_items_have_one_arte_and_preserve_metadata(self):
        items = [article(index) for index in range(1107)]
        before = copy.deepcopy(items)
        xml = generator.build_rss(items, {"build_time": INSTANT})
        document = ET.fromstring(xml)
        nodes = document.findall("./channel/item")
        self.assertEqual(len(nodes), len(items))
        self.assertEqual(items, before)
        by_url = {item["url"]: item for item in items}
        self.assertEqual({node.findtext("guid") for node in nodes}, set(by_url))
        for node in nodes:
            item = by_url[node.findtext("guid")]
            self.assertEqual(node.findtext("link"), item["url"])
            self.assertEqual(node.findtext("title"), unescape(item["title"]))
            self.assertEqual(node.findtext("description"), item["description"])
            self.assertEqual(node.findtext("dc:creator", namespaces=NS), item["author"])
            self.assertEqual(node.findtext("pubDate"), format_datetime(datetime.fromisoformat(item["published"])))
            self.assertEqual(node.find("enclosure").get("url"), item["image"])
            self.assertEqual([category.text for category in node.findall("category")], ["arte"])
            self.assertEqual(node.find("guid").get("isPermaLink"), "true")
            self.assertIsNone(node.find("dateModified"))
        parsed = feedparser.parse(xml)
        self.assertFalse(parsed.bozo, getattr(parsed, "bozo_exception", None))
        self.assertEqual(len(parsed.entries), len(items))

    def test_discovery_has_one_canonical_self_and_one_google_hub(self):
        document = ET.fromstring(generator.build_rss([article()], {"build_time": INSTANT}))
        links = document.findall("./channel/atom:link", NS)
        self.assertEqual([link.attrib for link in links if link.get("rel") == "self"],
                         [{"href": CANONICAL, "rel": "self", "type": "application/rss+xml"}])
        self.assertEqual([link.attrib for link in links if link.get("rel") == "hub"],
                         [{"href": HUB, "rel": "hub"}])

    def test_legacy_taxonomy_and_record_categories_cannot_override_arte(self):
        item = article()
        before = copy.deepcopy(item)
        with patch.object(taxonomy, "CATEGORY_VOCABULARY", frozenset({"arte contemporanea", "arte e cultura"})), \
             patch.object(taxonomy, "ARTICLE_CATEGORIES", {item["url"]: ["arte contemporanea", "arte e cultura"]}):
            self.assertEqual(taxonomy.categories_for(item), ("arte",))
            document = ET.fromstring(generator.build_rss([item], {"build_time": INSTANT}))
        self.assertEqual([node.text for node in document.findall("./channel/item/category")], ["arte"])
        self.assertEqual(item, before)


class NewsEditorialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schemas = ROOT / "tests/schemas"
        cls.schema = etree.XMLSchema(etree.fromstring((
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            '<xs:import namespace="http://www.sitemaps.org/schemas/sitemap/0.9" schemaLocation="sitemap.xsd"/>'
            '<xs:import namespace="http://www.google.com/schemas/sitemap-news/0.9" schemaLocation="sitemap-news.xsd"/>'
            '</xs:schema>').encode(), base_url=str(schemas / "combined.xsd")))

    def render(self, items):
        before = copy.deepcopy(items)
        with patch.object(news, "LOCAL_FIRST", True), \
             patch.object(news, "_load_local_items", return_value=items), \
             patch.object(news, "_load_remote_items", side_effect=AssertionError("Unnecessary network request")), \
             patch.object(news.datetime, "datetime", FrozenDateTime):
            response = news.news_sitemap_view()
        self.assertEqual(items, before)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/xml")
        self.assertIn("no-cache", response.headers["Cache-Control"])
        return etree.fromstring(response.data)

    def test_new_article_is_immediately_present_with_required_news_metadata(self):
        item = article(published=INSTANT.isoformat())
        document = self.render([item])
        self.schema.assertValid(document)
        self.assertEqual(document.findtext("s:url/s:loc", namespaces=NS), item["url"])
        self.assertEqual(document.findtext("s:url/n:news/n:publication/n:name", namespaces=NS), "ARTBOOMS")
        self.assertEqual(document.findtext("s:url/n:news/n:publication/n:language", namespaces=NS), "it")
        self.assertEqual(document.findtext("s:url/n:news/n:publication_date", namespaces=NS), item["published"])
        self.assertEqual(document.findtext("s:url/n:news/n:title", namespaces=NS), "Museo & arte 0")
        self.assertEqual(document.findtext("s:url/n:news/n:keywords", namespaces=NS), "arte")
        self.assertEqual(document.findall(".//n:category", NS), [])
        self.assertEqual(document.findall(".//s:lastmod", NS), [])

    def test_48_hours_boundary_and_future_articles_are_excluded(self):
        fresh = article(0, published=(INSTANT - timedelta(hours=48, microseconds=-1)).isoformat())
        boundary = article(1, published=(INSTANT - timedelta(hours=48)).isoformat())
        old = article(2, published=(INSTANT - timedelta(hours=48, microseconds=1)).isoformat())
        future = article(3, published=(INSTANT + timedelta(microseconds=1)).isoformat())
        document = self.render([future, old, boundary, fresh])
        self.schema.assertValid(document)
        self.assertEqual([node.text for node in document.findall("s:url/s:loc", NS)], [fresh["url"]])
        self.assertEqual(len(document.findall("s:url/n:news", NS)), 1)

    def test_archive_without_recent_articles_is_valid_without_news_metadata(self):
        old = article(0, published=(INSTANT - timedelta(days=3)).isoformat())
        older = article(1, published=(INSTANT - timedelta(days=30)).isoformat())
        document = self.render([older, old])
        self.schema.assertValid(document)
        self.assertEqual([node.text for node in document.findall("s:url/s:loc", NS)], [old["url"]])
        self.assertEqual(document.findall(".//n:news", NS), [])

    def test_empty_cache_is_well_formed_empty_urlset(self):
        # Google permits an empty news sitemap. The generic Sitemap XSD
        # requires one URL, so schema validation applies to nonempty outputs.
        document = self.render([])
        self.assertEqual(document.tag, "{" + NS["s"] + "}urlset")
        self.assertEqual(len(document), 0)

    def test_future_only_articles_do_not_create_news_or_placeholder_url(self):
        document = self.render([article(published=(INSTANT + timedelta(hours=1)).isoformat())])
        self.assertEqual(len(document), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
