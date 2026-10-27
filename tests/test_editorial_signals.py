"""Regressions for editorial/SEO separation and contradictory article markup."""
import unittest
import tempfile
from pathlib import Path

from article_parser import parse_article, _parse_date
from scripts.audit_editorial_signals import extract_signals, compare, load_rss


URL = "https://www.artbooms.com/blog/art-basel-paris-sicurezza"
HEADLINE = "Oggi apre ai super-ricchi Art Basel Paris con misure di sicurezza rafforzate. Ma la tensione in Francia è ancora alta"
SEO = "Apre oggi Art Basel Paris 2026, ma la tensione in Francia resta alta — ARTBOOMS"
HTML = f'''<html lang="it"><head><title>{SEO}</title>
<link rel="canonical" href="{URL}">
<meta itemprop="name" content="{SEO}">
<meta itemprop="headline" content="{HEADLINE}">
<meta itemprop="author" content="Natasha Barbieri">
<meta itemprop="datePublished" content="2026-10-21T10:50:00+0200">
<meta itemprop="dateModified" content="2026-10-22T22:48:50+0200">
</head><body><h1 class="logo">ARTBOOMS</h1>
<article><h1 class="entry-title">{HEADLINE}</h1></article></body></html>'''


class ParserSignals(unittest.TestCase):
    def test_editorial_title_does_not_rewrite_seo(self):
        item = parse_article(URL, html=HTML)
        self.assertEqual(item["title"], HEADLINE)
        self.assertEqual(extract_signals(HTML, URL)["title"], SEO)

    def test_actual_published_and_modified_remain_distinct(self):
        item = parse_article(URL, html=HTML)
        self.assertEqual(item["published"], "2026-10-21T10:50:00+02:00")
        self.assertEqual(item["modified"], "2026-10-22T22:48:50+02:00")

    def test_fallback_uses_native_headline_then_legacy_name(self):
        without_h1 = HTML.replace(f'<h1 class="entry-title">{HEADLINE}</h1>', "")
        self.assertEqual(parse_article(URL, html=without_h1)["title"], HEADLINE)
        without_both = without_h1.replace(f'<meta itemprop="headline" content="{HEADLINE}">', "")
        self.assertEqual(parse_article(URL, html=without_both)["title"], SEO)

    def test_explicit_offsets_are_semantically_identical(self):
        self.assertEqual(_parse_date("2026-10-23T09:02:00+0200"),
                         _parse_date("2026-10-23T07:02:00Z"))

    def test_naive_editorial_time_uses_rome_summer_and_winter(self):
        self.assertEqual(_parse_date("2026-10-23T09:02:00").isoformat(), "2026-10-23T09:02:00+02:00")
        self.assertEqual(_parse_date("2026-11-23T09:02:00").isoformat(), "2026-11-23T09:02:00+01:00")

    def test_missing_modified_uses_publication_without_inventing_now(self):
        html = HTML.replace('<meta itemprop="dateModified" content="2026-10-22T22:48:50+0200">', "")
        item = parse_article(URL, html=html)
        self.assertEqual(item["modified"], item["published"])


class AuditSignals(unittest.TestCase):
    def setUp(self):
        self.page = extract_signals(HTML, URL)
        self.item = parse_article(URL, html=HTML)

    def test_legitimate_seo_title_is_not_a_schema_error(self):
        self.assertEqual(compare(self.page, self.item, sitemap={"loc": URL}), [])

    def test_wrong_article_and_wrong_author_are_detected(self):
        self.page["article_schemas"] = [{"location": "body", "data": {
            "@type": "NewsArticle", "mainEntityOfPage": {"@id": URL + "-old"},
            "headline": HEADLINE, "author": {"@type": "Person", "name": "Someone else"}}}]
        codes = {i["code"] for i in compare(self.page, self.item, sitemap={})}
        self.assertIn("schema_wrong_article", codes)
        self.assertIn("author_mismatch", codes)

    def test_timezone_equivalence_does_not_create_false_conflict(self):
        item = dict(self.item, published="2026-10-21T08:50:00Z")
        self.assertEqual(compare(self.page, item, sitemap={}), [])

    def test_cache_date_clamp_and_rss_guid_conflict_are_detected(self):
        item = dict(self.item, modified=self.item["published"])
        rss = dict(self.item, link=URL, guid=URL + "-wrong")
        codes = {i["code"] for i in compare(self.page, item, rss, sitemap={})}
        self.assertIn("date_mismatch", codes)
        self.assertIn("guid_mismatch", codes)

    def test_invalid_custom_json_does_not_hide_native_metadata(self):
        html = HTML.replace('</body>', '<script type="application/ld+json">{"bad":}</script></body>')
        page = extract_signals(html, URL)
        self.assertEqual(page["h1"], HEADLINE)
        self.assertEqual(compare(page, self.item, sitemap={})[0]["code"], "invalid_json_ld")

    def test_image_renditions_match_but_foreign_paths_require_review(self):
        self.page["og_image"] = "https://images.squarespace-cdn.com/art/photo.jpg?format=1500w"
        rss = {"image": "http://images.squarespace-cdn.com/art/photo.jpg?format=2500w"}
        self.assertEqual(compare(self.page, rss=rss, sitemap={}), [])
        rss["image"] = "https://www.artbooms.com/images/invented.jpg"
        issues = compare(self.page, rss=rss, sitemap={})
        self.assertEqual(issues[0]["code"], "image_requires_review")
        self.assertEqual(issues[0]["severity"], "review")

    def test_news_publisher_language_and_sitemap_identity_are_checked(self):
        news = {"publication": "Another magazine", "language": "en"}
        codes = {i["code"] for i in compare(self.page, news=news, sitemap={"loc": URL + "-wrong"})}
        self.assertEqual(codes, {"publisher_mismatch", "language_mismatch", "canonical_mismatch"})

    def test_rss_modification_is_compared_as_an_instant(self):
        xml = f'''<rss xmlns:dcterms="http://purl.org/dc/terms/"><channel><item>
        <link>{URL}</link><dcterms:modified>2026-10-22T20:48:50Z</dcterms:modified>
        <category>arte</category></item></channel></rss>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rss.xml"
            path.write_text(xml, encoding="utf-8")
            rss = next(iter(load_rss(path).values()))
        self.assertEqual(compare(self.page, rss=rss, sitemap={}), [])
        rss["modified"] = self.item["published"]
        self.assertIn("date_mismatch", {i["code"] for i in compare(self.page, rss=rss, sitemap={})})


if __name__ == "__main__":
    unittest.main()
