import datetime
import requests
from flask import Response

NEWS_CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss-v2/main/cache/articles_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


def news_sitemap_view():
    """
    Genera la News Sitemap leggendo direttamente la cache JSON su GitHub.
    Non tocca il feed.
    """
    # 1) Leggo la cache remota
    try:
        resp = requests.get(NEWS_CACHE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        raw_items = data.get("items", [])
        if isinstance(raw_items, dict):
            items = list(raw_items.values())
        elif isinstance(raw_items, list):
            items = raw_items
        else:
            items = []
    except Exception:
        empty_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
</urlset>"""
        return Response(empty_xml, mimetype="application/xml")

    # 2) Filtro articoli recenti (ultimi 7 giorni, come il worker Cloudflare)
    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    window = datetime.timedelta(days=7)
    recent = []
    for a in items:
        pub_str = a.get("published")
        if not pub_str:
            continue
        try:
            pub_dt = datetime.datetime.fromisoformat(pub_str)
        except ValueError:
            continue
        if now - pub_dt <= window:
            recent.append(a)

    # 3) Costruisco l'XML News Sitemap
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">',
    ]

    for a in recent:
        loc = (a.get("url") or "").strip()
        title = (a.get("title") or "").strip()
        pub_str = (a.get("published") or "").strip()
        if not loc or not title or not pub_str:
            continue

        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append("    <news:news>")
        parts.append("      <news:publication>")
        parts.append("        <news:name>ARTBOOMS</news:name>")
        parts.append("        <news:language>it</news:language>")
        parts.append("      </news:publication>")
        parts.append("      <news:keywords>arte contemporanea, arte e cultura</news:keywords>")
        parts.append(f"      <news:publication_date>{pub_str}</news:publication_date>")
        parts.append(f"      <news:title>{title} — ARTBOOMS</news:title>")
        parts.append("    </news:news>")
        parts.append("  </url>")

    parts.append("</urlset>")
    xml = "\n".join(parts)

    return Response(xml, mimetype="application/xml")
