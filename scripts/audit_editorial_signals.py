"""Read-only comparison of page, cache, RSS and sitemap editorial signals.

No production imports, writes, notifications or cache refreshes. Downloaded
HTML is reusable, so a second offline comparison consumes no site bandwidth.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests


NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
      "n": "http://www.google.com/schemas/sitemap-news/0.9",
      "i": "http://www.google.com/schemas/sitemap-image/1.1",
      "dc": "http://purl.org/dc/elements/1.1/",
      "dcterms": "http://purl.org/dc/terms/"}
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting"}


def normalized(value):
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def url_key(value):
    return (value or "").split("#")[0].replace("http://", "https://").rstrip("/")


def instant(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    return dt.timestamp() if dt.tzinfo is not None else None


def graph_nodes(value):
    if isinstance(value, list):
        for node in value:
            yield from graph_nodes(node)
    elif isinstance(value, dict):
        yield value
        yield from graph_nodes(value.get("@graph", []))


def types(node):
    value = node.get("@type", [])
    return set(value if isinstance(value, list) else [value])


def author_names(value):
    if isinstance(value, list):
        return sorted(name for item in value for name in author_names(item))
    if isinstance(value, dict):
        return author_names(value.get("name"))
    return [normalized(value).casefold()] if isinstance(value, str) and value else []


def image_urls(value):
    if isinstance(value, list):
        return [url for item in value for url in image_urls(item)]
    if isinstance(value, dict):
        return image_urls(value.get("url") or value.get("contentUrl"))
    return [value] if isinstance(value, str) and value else []


def image_key(value):
    """Ignore CDN rendition size and protocol, without equating other paths."""
    parsed = urlsplit(value)
    return parsed.netloc.lower() + unquote(parsed.path)


def extract_signals(html, requested_url):
    soup = BeautifulSoup(html, "lxml")

    def meta(attribute, name):
        node = soup.find("meta", attrs={attribute: name})
        return node.get("content", "") if node else ""

    canonical = soup.find("link", rel="canonical")
    h1 = soup.select_one("article h1.entry-title, h1.entry-title, article h1")
    articles, other_types, errors = [], [], []
    for index, script in enumerate(soup.select('script[type="application/ld+json"]')):
        try:
            nodes = list(graph_nodes(json.loads(script.string or script.get_text())))
        except (ValueError, TypeError) as exc:
            errors.append({"script": index, "error": str(exc)})
            continue
        for node in nodes:
            if types(node) & ARTICLE_TYPES:
                articles.append({"location": "head" if script.find_parent("head") else "body",
                                 "script": index, "data": node})
            else:
                other_types.extend(sorted(types(node)))
    return {
        "requested_url": requested_url,
        "canonical": canonical.get("href", "") if canonical else "",
        "h1": normalized(h1.get_text(" ", strip=True)) if h1 else "",
        "title": normalized(soup.title.get_text()) if soup.title else "",
        "seo_name": meta("itemprop", "name"),
        "native_headline": meta("itemprop", "headline"),
        "published": meta("itemprop", "datePublished"),
        "modified": meta("itemprop", "dateModified"),
        "author": meta("itemprop", "author"),
        "publisher": meta("itemprop", "publisher"),
        "og_image": meta("property", "og:image"),
        "thumbnail": meta("itemprop", "thumbnailUrl"),
        "og_image_width": meta("property", "og:image:width"),
        "og_image_height": meta("property", "og:image:height"),
        "language": soup.html.get("lang", "") if soup.html else "",
        "robots": [t.get("content", "") for t in soup.find_all("meta", attrs={"name": "robots"})],
        "visible_dates": [t.get_text(" ", strip=True) for t in soup.select("time")],
        "images": [{"src": t.get("src"), "data_src": t.get("data-src"),
                    "width": t.get("width"), "height": t.get("height"),
                    "loading": t.get("loading"), "alt": t.get("alt")}
                   for t in soup.select("article img")],
        "article_schemas": articles,
        "other_schema_types": other_types,
        "json_errors": errors,
    }


def load_rss(path):
    if not path:
        return {}
    return {url_key(n.findtext("link")): {
        "title": n.findtext("title"), "link": n.findtext("link"),
        "guid": n.findtext("guid"), "published": n.findtext("pubDate"),
        "modified": n.findtext("dcterms:modified", namespaces=NS),
        "author": n.findtext("dc:creator", namespaces=NS),
        "image": n.find("enclosure").get("url") if n.find("enclosure") is not None else None,
        "categories": [c.text for c in n.findall("category")],
    } for n in ET.parse(path).findall("./channel/item")}


def load_sitemap(path):
    if not path:
        return {}
    return {url_key(n.findtext("s:loc", namespaces=NS)): {
        "loc": n.findtext("s:loc", namespaces=NS),
        "title": n.findtext("n:news/n:title", namespaces=NS),
        "published": n.findtext("n:news/n:publication_date", namespaces=NS),
        "publication": n.findtext("n:news/n:publication/n:name", namespaces=NS),
        "language": n.findtext("n:news/n:publication/n:language", namespaces=NS),
        "lastmod": n.findtext("s:lastmod", namespaces=NS),
        "images": [i.text for i in n.findall("i:image/i:loc", NS)],
    } for n in ET.parse(path).findall("s:url", NS)}


def compare(page, cache=None, rss=None, news=None, sitemap=None):
    issues = []

    def issue(code, source, observed, expected, severity="error"):
        issues.append(dict(code=code, source=source, observed=observed,
                           expected=expected, severity=severity))

    canonical = url_key(page["canonical"])
    headline = page["h1"]
    primary_images = {image_key(url) for url in
                      (page["og_image"], page.get("thumbnail")) if url}
    page_images = primary_images | {
        image_key(url) for item in page["images"]
        for url in (item.get("src"), item.get("data_src")) if url}

    def compare_image(value, source, reference):
        observed = image_urls(value)
        if observed and reference and not ({image_key(url) for url in observed} & reference):
            # Another editorial image or a CDN alias can be legitimate. A URL
            # difference is a review item, never proof of an unavailable image.
            issue("image_requires_review", source, observed, sorted(reference), "review")
    if not canonical:
        issue("missing_canonical", "HTML", None, page["requested_url"])
    if not headline:
        issue("missing_article_h1", "HTML", None, "article H1")
    for err in page["json_errors"]:
        issue("invalid_json_ld", "HTML", err, "valid JSON")
    if page["native_headline"] and normalized(page["native_headline"]) != headline:
        issue("headline_mismatch", "native meta", page["native_headline"], headline)
    for schema in page["article_schemas"]:
        node = schema["data"]
        label = f"JSON-LD {','.join(sorted(types(node)))} {schema['location']}"
        compare_image(node.get("image"), label + ".image", page_images)
        for key in ("mainEntityOfPage", "url", "@id"):
            identity = node.get(key)
            if isinstance(identity, dict):
                identity = identity.get("@id") or identity.get("url")
            if isinstance(identity, str) and url_key(identity) != canonical:
                issue("schema_wrong_article", label + "." + key, identity, page["canonical"])
        if node.get("headline") and normalized(node["headline"]) != headline:
            # A shorter equivalent headline may be legitimate; text inequality
            # alone cannot establish a semantic contradiction or a News block.
            issue("headline_mismatch", label, node["headline"], headline, "review")
        for key, native in (("datePublished", "published"), ("dateModified", "modified")):
            if instant(node.get(key)) is not None and instant(page[native]) is not None:
                if instant(node[key]) != instant(page[native]):
                    issue("date_mismatch", label + "." + key, node[key], page[native],
                          "review" if key == "dateModified" else "error")
        if node.get("inLanguage") and node["inLanguage"].split("-")[0].lower() != page["language"].split("-")[0].lower():
            issue("language_mismatch", label, node["inLanguage"], page["language"])
        if node.get("publisher") and page["publisher"]:
            publisher = node["publisher"]
            name = publisher.get("name") if isinstance(publisher, dict) else publisher
            if isinstance(name, str) and normalized(name).casefold() != normalized(page["publisher"]).casefold():
                issue("publisher_mismatch", label, name, page["publisher"])
        if node.get("author") and page["author"]:
            if author_names(node["author"]) != author_names(page["author"]):
                issue("author_mismatch", label, node["author"], page["author"])
    for label, record in (("cache", cache), ("RSS", rss), ("news sitemap", news)):
        if not record:
            continue
        compare_image(record.get("image"), label + ".image", primary_images)
        record_url = record.get("url") or record.get("link") or record.get("loc")
        if record_url and url_key(record_url) != canonical:
            issue("canonical_mismatch", label, record_url, page["canonical"])
        if record.get("guid") and url_key(record["guid"]) != canonical:
            issue("guid_mismatch", label, record["guid"], page["canonical"])
        if record.get("title") and normalized(record["title"]) != headline:
            issue("derived_title_differs_from_h1", label, record["title"], headline, "review")
        for key in ("published", "modified"):
            if record.get(key) and instant(record[key]) is None:
                issue("invalid_or_naive_date", label + "." + key, record[key], "date with timezone")
            elif instant(record.get(key)) is not None and instant(page[key]) is not None:
                if instant(record[key]) != instant(page[key]):
                    issue("date_mismatch", label + "." + key, record[key], page[key])
        if record.get("author") and author_names(record["author"]) != author_names(page["author"]):
            issue("author_mismatch", label, record["author"], page["author"])
        if label == "news sitemap":
            if record.get("publication") and normalized(record["publication"]).casefold() != "artbooms":
                issue("publisher_mismatch", label, record["publication"], "ARTBOOMS")
            if record.get("language") and record["language"].split("-")[0].lower() != page["language"].split("-")[0].lower():
                issue("language_mismatch", label, record["language"], page["language"])
        if label == "RSS" and record.get("categories") is not None and "arte" not in record["categories"]:
            issue("category_mismatch", label, record["categories"], "arte", "review")
    if sitemap is None:
        issue("missing_ordinary_sitemap", "Squarespace sitemap", None, page["canonical"], "review")
    elif sitemap.get("loc") and url_key(sitemap["loc"]) != canonical:
        issue("canonical_mismatch", "Squarespace sitemap", sitemap["loc"], page["canonical"])
    return issues


def source_path(folder, url):
    return folder / (sha256(url.encode()).hexdigest() + ".html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--sources", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rss")
    parser.add_argument("--news")
    parser.add_argument("--sitemap")
    parser.add_argument("--url", action="append")
    parser.add_argument("--fetch", action="store_true", help="Fetch missing HTML only")
    parser.add_argument("--refresh", action="store_true", help="With --fetch, replace captured HTML with a fresh response")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    raw_cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    cache = {url_key(k): v for k, v in raw_cache["items"].items()}
    urls = args.url or list(cache)
    # Recent articles first; a stopped run remains useful and resumable.
    urls.sort(key=lambda u: cache.get(url_key(u), {}).get("published", ""), reverse=True)
    folder = Path(args.sources)
    folder.mkdir(parents=True, exist_ok=True)
    output = Path(args.output)
    if output.resolve() == Path(args.cache).resolve():
        parser.error("The report cannot overwrite the article cache")
    if args.refresh and not args.fetch:
        parser.error("--refresh requires --fetch")
    rss, news, sitemap = load_rss(args.rss), load_sitemap(args.news), load_sitemap(args.sitemap)
    sessions = threading.local()

    def inspect(url):
        path = source_path(folder, url)
        try:
            if not path.exists() or args.refresh:
                if not args.fetch:
                    return {"url": url, "unavailable": "HTML not captured"}
                if urlsplit(url).hostname != "www.artbooms.com":
                    return {"url": url, "unavailable": "outside intended site"}
                time.sleep(0.5)
                if not hasattr(sessions, "http"):
                    sessions.http = requests.Session()
                    sessions.http.headers["User-Agent"] = "ARTBOOMS-Editorial-Audit/1.0"
                r = sessions.http.get(url, timeout=25)
                r.raise_for_status()
                path.write_bytes(r.content)
            page = extract_signals(path.read_text(encoding="utf-8"), url)
            key = url_key(url)
            return {"url": url, "captured_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    "page": page, "cache": cache.get(key),
                    "rss": rss.get(key), "news": news.get(key), "sitemap": sitemap.get(key),
                    "issues": compare(page, cache.get(key), rss.get(key), news.get(key), sitemap.get(key))}
        except Exception as exc:
            return {"url": url, "unavailable": f"{type(exc).__name__}: {exc}"}

    records = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        for row in pool.map(inspect, urls):
            records.append(row)
            if len(records) % 25 == 0:
                output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Captured {len(records)}/{len(urls)}", flush=True)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"urls": len(records), "captured": sum("page" in r for r in records),
                      "unavailable": sum("unavailable" in r for r in records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
