import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape
from html import unescape
import re

logger = logging.getLogger("rss_generator")

def build_rss(items: list, meta: dict):
    try:
        n = len(items)
        logger.info("🧩 Costruzione RSS: ricevuti %d articoli.", n)
        if n == 0:
            logger.warning("⚠️ Nessun articolo passato a build_rss — feed vuoto.")
    except Exception as e:
        logger.exception("Errore conteggio articoli: %s", e)
        items = []
        n = 0

    try:
        items = [it for it in items if it.get("published")]
        items = sorted(items, key=lambda x: x["published"], reverse=True)
        logger.info("📊 DEBUG RSS: ordinati %d articoli per data di pubblicazione (solo 'published').", len(items))
        if items:
            logger.info("🕒 Ultimo pubblicato: %s", items[0].get("title"))
    except Exception as e:
        logger.exception("Errore ordinamento articoli: %s", e)

    feed_title = escape(meta.get("title", "ARTBOOMS – Archivio completo"))
    feed_description = escape(meta.get("description", "Tutti gli articoli di Artbooms"))
    feed_language = meta.get("language", "it-IT")
    build_time = datetime.utcnow().replace(tzinfo=timezone.utc)
    build_time_rfc = format_datetime(build_time)

    rss_items = []
    for it in items:
        if not isinstance(it, dict):
            logger.warning("Elemento RSS non valido: %s", type(it))
            continue

        if not it.get("title") and not it.get("description"):
            logger.warning("Articolo senza titolo e descrizione: %s", it.get("url"))
            continue

        raw_title = (it.get("title") or "").strip()
        if not raw_title:
            raw_title = "(senza titolo)"
        title = escape(unescape(raw_title))

        raw_url = (it.get("url") or "").strip()
        guid = escape(raw_url)
        link = escape(raw_url)

        raw_desc = (it.get("description") or "")
        if "&" in raw_desc:
            raw_desc = re.sub(r"&[^;]{0,10}$", "", raw_desc)
            raw_desc = re.sub(r"&(?![A-Za-z0-9#]+;)", "&amp;", raw_desc)
            raw_desc = re.sub(r"&(?=[\s.,;:!?])", "&amp;", raw_desc)
            raw_desc = re.sub(r"&amp;([A-Z][a-z]+)", r"&amp; \1", raw_desc)

        desc = f"<![CDATA[{raw_desc.strip()}]]>"

        author = escape(it.get("author") or "")
        image = it.get("image")
        pub_iso = it.get("published")

        try:
            if pub_iso:
                dt = datetime.fromisoformat(pub_iso.replace("Z", "+00:00"))
                pub_rfc = format_datetime(dt)
            else:
                pub_rfc = build_time_rfc
        except Exception:
            pub_rfc = build_time_rfc

        item_xml = [
            "<item>",
            f"<title>{title}</title>",
            f"<link>{link}</link>",
            f"<guid isPermaLink=\"true\">{guid}</guid>",
            f"<description>{desc}</description>",
            f"<dc:creator>{author}</dc:creator>",
            f"<pubDate>{pub_rfc}</pubDate>",
        ]

        if image:
            safe_img = escape(image)
            item_xml.append(f'<enclosure url="{safe_img}" type="image/jpeg" length="0" />')

        item_xml.append("</item>")
        rss_items.append("\n".join(item_xml))

    body = "\n".join(rss_items)
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "<channel>\n"
        f"<title>{feed_title}</title>\n"
        f"<link>https://www.artbooms.com</link>\n"
        f"<description>{feed_description}</description>\n"
        f"<language>{feed_language}</language>\n"
        f"<atom:link href=\"https://rss.artbooms.com/rss\" rel=\"self\" type=\"application/rss+xml\" />\n"
        f"<lastBuildDate>{build_time_rfc}</lastBuildDate>\n"
        f"{body}\n"
        "</channel>\n"
        "</rss>"
    )

    logger.info("✅ RSS costruito con %d elementi effettivi.", len(rss_items))
    return rss
