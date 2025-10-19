import logging
from datetime import datetime, timezone
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
    build_time = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

    rss_items = []
    for it in items:
        # se it non è dict lo ignora
        if not isinstance(it, dict):
            logger.warning("Elemento RSS non valido: %s", type(it))
            continue
        title = escape(it.get("title") or "")
        link = escape(it.get("url") or "")
        desc = escape(it.get("description") or "")
        author = escape(it.get("author") or "")
        pub = it.get("published") or build_time
        guid = link or title

        rss_items.append(
            f"<item>"
            f"<title>{title}</title>"
            f"<link>{link}</link>"
            f"<description>{desc}</description>"
            f"<dc:creator>{author}</dc:creator>"
            f"<pubDate>{pub}</pubDate>"
            f"<guid>{guid}</guid>"
            f"</item>"
        )

    body = "\n".join(rss_items)
    rss = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'<channel>\n'
        f'<title>{feed_title}</title>\n'
        f'<description>{feed_description}</description>\n'
        f'<language>{feed_language}</language>\n'
        f'<lastBuildDate>{build_time}</lastBuildDate>\n'
        f'{body}\n'
        f'</channel>\n'
        f'</rss>'
    )

    logger.info("✅ RSS costruito con %d elementi effettivi.", len(rss_items))
    return rss
