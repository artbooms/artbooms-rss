"""Experimental WebSub topic notification for the Google News XML sitemap.

Google's public hub documents RSS/Atom feeds, not XML news sitemaps.
An HTTP 2xx acknowledges only the publish request, not a Google News crawl.
This path is independent of the existing durable RSS/WebSub publisher.
"""
import logging
import xml.etree.ElementTree as ET

import requests

from news_sitemap import NEWS_SITEMAP_URL, NEWS_SITEMAP_HUB

logger = logging.getLogger("news_sitemap_websub")
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
NEWS_NS = "http://www.google.com/schemas/sitemap-news/0.9"
HEADERS = {"User-Agent": "ARTBOOMS-NewsSitemap-WebSub/1.0 (+https://www.artbooms.com)"}
TIMEOUT = (3, 10)


def notify_news_sitemap_topic(updated_urls):
    """Try one *separate* hub notification, only when a new/updated article
    with Google News metadata is verified in the PUBLIC sitemap.

    No periodic pings, no changes to RSS/WebSub durable state, no exception to
    the caller, no claim that Google subscribes to this XML sitemap topic.
    """
    # None is reserved for a single startup catch-up of current News items.
    # The normal publishing path always supplies exact updated URLs.
    changed = None if updated_urls is None else {
        url for url in updated_urls
        if isinstance(url, str) and url.startswith("https://www.artbooms.com/blog/")
    }
    if changed is not None and not changed:
        return False
    try:
        # Same public URL that Google would fetch, not an internal/cache copy.
        response = requests.get(
            NEWS_SITEMAP_URL,
            headers={**HEADERS, "Cache-Control": "no-cache"},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code != 200:
            logger.warning(
                "News sitemap WebSub: sitemap pubblica HTTP %s; nessun POST",
                response.status_code,
            )
            return False
        root = ET.fromstring(response.content)
        if root.tag != f"{{{SITEMAP_NS}}}urlset":
            logger.warning("News sitemap WebSub: radice XML non valida; nessun POST")
            return False
        eligible = {
            entry.findtext(f"{{{SITEMAP_NS}}}loc")
            for entry in root.findall(f"{{{SITEMAP_NS}}}url")
            if entry.find(f"{{{NEWS_NS}}}news") is not None
        }
        if not eligible or (changed is not None and not eligible.intersection(changed)):
            logger.warning(
                "News sitemap WebSub: articolo cambiato non presente con metadati News "
                "nella sitemap pubblica; nessun POST"
            )
            return False
        result = requests.post(
            NEWS_SITEMAP_HUB,
            data={"hub.mode": "publish", "hub.url": NEWS_SITEMAP_URL},
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        if 200 <= result.status_code < 300:
            logger.info(
                "News sitemap WebSub: hub HTTP %s, topic=%s; "
                "accettazione NON equivale a scansione Google News",
                result.status_code, NEWS_SITEMAP_URL,
            )
            return True
        logger.warning(
            "News sitemap WebSub: hub HTTP %s, topic=%s",
            result.status_code, NEWS_SITEMAP_URL,
        )
    except (requests.RequestException, ET.ParseError, ValueError, TypeError) as exc:
        logger.warning(
            "News sitemap WebSub: notifica sperimentale fallita (%s); "
            "RSS e stato WebSub invariati",
            type(exc).__name__,
        )
    return False
