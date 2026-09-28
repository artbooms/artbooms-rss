import datetime
import json
import logging
import requests
from flask import Response

NEWS_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# 🔹 finestra Google News: 2 giorni
DAYS_WINDOW = 2

SITE_NAME = "ARTBOOMS"
LANG = "it"
KEYWORDS = "arte contemporanea, arte e cultura"


def _escape_xml(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


def _xml_response(xml: str) -> Response:
    resp = Response(xml, mimetype="application/xml")
    resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _cache_items(data):
    if not isinstance(data, dict):
        raise ValueError("La cache non è un oggetto JSON")
    raw = data.get("items")
    items = list(raw.values()) if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ValueError("La cache non contiene articoli")
    for item in items:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("url", "title", "published")
        ):
            raise ValueError("Articolo incompleto nella cache")
        datetime.datetime.fromisoformat(item["published"])
    return items


def news_sitemap_view():
    """
    Genera la News Sitemap leggendo la cache JSON su GitHub.

    - Usa solo i campi: url, title, published
    - Finestra temporale: ultimi DAYS_WINDOW giorni
    - news:keywords = "arte contemporanea, arte e cultura"
    - news:title = "<titolo>"
    """
    try:
        resp = requests.get(
            NEWS_CACHE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        items = _cache_items(resp.json())
    except Exception as exc:
        logging.warning("News sitemap: uso la cache locale; GitHub non disponibile: %s", exc)
        try:
            with open("cache/articles_cache.json", "r", encoding="utf-8") as f:
                items = _cache_items(json.load(f))
        except Exception as local_exc:
            logging.error("News sitemap: nessuna cache valida: %s", local_exc)
            response = Response("Sitemap temporaneamente non disponibile", status=503, mimetype="text/plain")
            response.headers["Retry-After"] = "300"
            response.headers["Cache-Control"] = "no-store"
            return response

    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    window = datetime.timedelta(days=DAYS_WINDOW)

    recent = []
    newest = None
    for it in items:
        if not isinstance(it, dict):
            continue

        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        pub_str = (it.get("published") or "").strip()

        if not url or not title or not pub_str:
            continue

        try:
            pub_dt = datetime.datetime.fromisoformat(pub_str)
        except ValueError:
            continue

        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=datetime.timezone.utc)

        if newest is None or pub_dt > newest[0]:
            newest = (pub_dt, url)

        if now - pub_dt > window:
            continue

        it["_pub_dt"] = pub_dt
        it["_url"] = url
        it["_title"] = title
        recent.append(it)

    recent.sort(key=lambda a: a["_pub_dt"], reverse=True)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">',
    ]

    for it in recent:
        loc = _escape_xml(it["_url"])
        title = _escape_xml(it["_title"])
        pub_iso = it["_pub_dt"].replace(microsecond=0).isoformat()

        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append("    <news:news>")
        parts.append("      <news:publication>")
        parts.append(f"        <news:name>{_escape_xml(SITE_NAME)}</news:name>")
        parts.append(f"        <news:language>{LANG}</news:language>")
        parts.append("      </news:publication>")
        parts.append(f"      <news:publication_date>{pub_iso}</news:publication_date>")
        parts.append(f"      <news:title>{title}</news:title>")
        parts.append(f"      <news:keywords>{_escape_xml(KEYWORDS)}</news:keywords>")
        parts.append("    </news:news>")
        parts.append("  </url>")

    # Fuori dalle 48 ore resta un URL standard, senza metadati Google News.
    if not recent and newest is not None:
        parts.append("  <url>")
        parts.append(f"    <loc>{_escape_xml(newest[1])}</loc>")
        parts.append("  </url>")

    parts.append("</urlset>")
    return _xml_response("\n".join(parts))
