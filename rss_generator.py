import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from dateutil import parser as dateparser

# helper date
def _as_dt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        dt = dateparser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def build_rss(items, meta):
    """
    Crea RSS 2.0 con dc:creator e dcterms:modified (compatibile Google News).
    """
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
    ET.register_namespace("dcterms", "http://purl.org/dc/terms/")
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    ET.register_namespace("media", "http://search.yahoo.com/mrss/")

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = meta.get("title", "Artbooms RSS Feed")
    ET.SubElement(channel, "link").text = meta.get("link", "https://www.artbooms.com")
    ET.SubElement(channel, "description").text = meta.get("description", "Ultimi articoli da Artbooms")
    ET.SubElement(channel, "language").text = meta.get("language", "it-IT")

    # self link (opzionale)
    atom = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom.set("href", meta.get("self", "https://artbooms-rss.onrender.com/rss"))
    atom.set("rel", "self")
    atom.set("type", "application/rss+xml")

    last_mod = None

    for it in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = it.get("title") or ""
        ET.SubElement(item, "link").text = it.get("url") or ""
        g = ET.SubElement(item, "guid")
        g.text = it.get("url") or ""
        g.set("isPermaLink", "true")

        desc = it.get("description") or ""
        ET.SubElement(item, "description").text = desc

        # ✅ autore in dc:creator
        author = it.get("author")
        if author:
            ET.SubElement(item, "{http://purl.org/dc/elements/1.1/}creator").text = author

        pub = _as_dt(it.get("published"))
        if pub:
            ET.SubElement(item, "pubDate").text = format_datetime(pub)

        mod = _as_dt(it.get("modified"))
        if mod:
            ET.SubElement(item, "{http://purl.org/dc/terms/}modified").text = mod.isoformat()
            if (last_mod is None) or (mod > last_mod):
                last_mod = mod

        img = it.get("image")
        if img:
            thumb = ET.SubElement(item, "{http://search.yahoo.com/mrss/}thumbnail")
            thumb.set("url", img)

    ET.SubElement(channel, "lastBuildDate").text = format_datetime(last_mod or datetime.now(timezone.utc))
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
