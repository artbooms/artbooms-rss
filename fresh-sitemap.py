import datetime
import requests
from flask import Response

# ✅ Cache ufficiale (repo principale)
FRESH_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss/main/cache/articles_cache.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# ✅ finestra “fresh” (Discover / scoperta): 7 giorni
DAYS_WINDOW = 7


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
    return resp


def fresh_sitemap_view():
    """
    Sitemap standard (NON Google News).
    Include URL pubblicati/modificati negli ultimi DAYS_WINDOW giorni.
    """
    try:
        r = requests.get(FRESH_CACHE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        empty_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
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
        if not url:
            continue

        dt_str = (it.get("modified") or "").strip() or (it.get("published") or "").strip()
        if not dt_str:
            continue

        try:
            dt = datetime.datetime.fromisoformat(dt_str)
        except ValueError:
            continue

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        if now - dt > window:
            continue

        recent.append((dt, url))

    # più recente → meno recente
    recent.sort(key=lambda x: x[0], reverse=True)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for dt, url in recent:
        parts.append("  <url>")
        parts.append(f"    <loc>{_escape_xml(url)}</loc>")
        parts.append(f"    <lastmod>{dt.replace(microsecond=0).isoformat()}</lastmod>")
        parts.append("  </url>")

    parts.append("</urlset>")
    return _xml_response("\n".join(parts))
