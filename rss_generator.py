import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

logger = logging.getLogger("rss_generator")

def build_rss(items: list, meta: dict):
    """Costruisce il feed RSS 2.0 da una lista di articoli."""
    try:
        n = len(items)
        logger.info("🧩 Costruzione RSS: ricevuti %d articoli.", n)
        if n == 0:
            logger.warning("⚠️ Nessun articolo passato a build_rss — feed vuoto.")
    except Exception as e:
        logger.exception("Errore conteggio articoli: %s", e)
        items = []
        n = 0

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

        title = escape(it.get("title") or "")
        link = escape(it.get("url") or "")
        desc = escape(it.get("description") or "")
        author = escape(it.get("author") or "")
        image = it.get("image")
        pub_iso = it.get("published") or it.get("modified")
        guid = link or title

        # 🔧 Conversione data in RFC2822
        try:
            if pub_iso:
                dt = datetime.fromisoformat(pub_iso.replace("Z", "+00:00"))
                pub_rfc = format_datetime(dt)
            else:
                pub_rfc = build_time_rfc
        except Exception:
            pub_rfc = build_time_rfc

        # 🔧 Costruzione blocco <item> completo con immagine
        item_xml = [
            "<item>",
            f"<title>{title}</title>",
            f"<link>{link}</link>",
            f"<description>{desc}</description>",
            f"<dc:creator>{author}</dc:creator>",
            f"<pubDate>{pub_rfc}</pubDate>",
            f"<guid>{guid}</guid>",
        ]

        # 🔧 Aggiunge immagine come enclosure (standard RSS)
        if image:
            safe_img = escape(image)
            item_xml.append(f'<enclosure url="{safe_img}" type="image/jpeg" />')
            item_xml.append(f'<media:content url="{safe_img}" medium="image" />')

        item_xml.append("</item>")
        rss_items.append("\n".join(item_xml))

    body = "\n".join(rss_items)
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:media="http://search.yahoo.com/mrss/">\n'
        "<channel>\n"
        f"<title>{feed_title}</title>\n"
        f"<description>{feed_description}</description>\n"
        f"<language>{feed_language}</language>\n"
        f"<lastBuildDate>{build_time_rfc}</lastBuildDate>\n"
        f"{body}\n"
        "</channel>\n"
        "</rss>"
    )

    logger.info("✅ RSS costruito con %d elementi effettivi.", len(rss_items))
    return rss
