import datetime
import requests
from flask import Response

# ✅ Cache ufficiale (repo principale)
NEWS_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# ✅ Google News: ultimi 2 giorni (48 ore)
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
    # ✅ evita cache “strana”
    resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def news_sitemap_view():
    """
    News Sitemap (Google News) leggendo la cache JSON su GitHub.

    - Usa solo: url, title, published
    - Finestra temporale: ultimi DAYS_WINDOW giorni (qui: 2)
    """
    try:
        r = requests.get(NEWS_CACHE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        empty_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
</urlset>"""
        return _xml_response(empty_xml)

    raw_items = data.get("items", [])
    if isinstance(raw_items, dict):
        items = list(raw_items.values())
    elif isinstance(raw_items, list):
        items = raw_items
    else:
        items = []

    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    window = datetime.timedelta(days=DAYS_WINDOW)

    recent = []
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

        if now - pub_dt > window:
            continue

        it["_pub_dt"] = pub_dt
        it["_url"] = url
        it["_title"] = title
        recent.append(it)

    # più recente → meno recente
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
        parts.append(f"      <news:keywords>{_escape_xml(KEYWORDS)}</news:keywords>")
        parts.append(f"      <news:publication_date>{pub_iso}</news:publication_date>")
        parts.append(f"      <news:title>{title} — {_escape_xml(SITE_NAME)}</news:title>")
        parts.append("    </news:news>")
        parts.append("  </url>")

    parts.append("</urlset>")
    return _xml_response("\n".join(parts))
