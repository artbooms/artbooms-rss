import hashlib
from datetime import datetime, timezone
from email.utils import format_datetime
import logging

logger = logging.getLogger("rss_generator")

def _as_dt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        from dateutil import parser as _p
        dt = _p.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

# try feedgen first, fallback to ElementTree if not installed
def build_rss(items: list, meta: dict):
    try:
        from feedgen.feed import FeedGenerator
        return _build_with_feedgen(items, meta)
    except Exception as e:
        logger.warning("feedgen not available or failed (%s), using fallback XML builder", e)
        return _build_fallback(items, meta)

def _build_with_feedgen(items, meta):
    from feedgen.feed import FeedGenerator
    fg = FeedGenerator()
    fg.id(meta.get('self_url') or "")
    fg.title(meta.get('title') or "ARTBOOMS")
    fg.description(meta.get('description') or "")
    fg.link(href=meta.get('self_url') or "", rel='self')
    fg.language(meta.get('language') or "it-IT")

    last_modified = None
    for it in items:
        fe = fg.add_entry()
        fe.id(it.get('url'))
        fe.title(it.get('title') or "")
        fe.link(href=it.get('url'))
        if it.get('description'):
            fe.description(it.get('description'))
        author = it.get('author')
        if author:
            try:
                fe.author({'name': author})
            except Exception:
                fe._FeedEntry__setitem('dc:creator', author)
        pub = _as_dt(it.get('published'))
        if pub:
            fe.pubDate(format_datetime(pub))
        mod = _as_dt(it.get('modified'))
        if mod:
            try:
                fe._FeedEntry__setitem('dcterms:modified', format_datetime(mod))
            except Exception:
                pass
            if (last_modified is None) or (mod > last_modified):
                last_modified = mod
        image_url = it.get('image')
        if image_url:
            try:
                fe.enclosure(image_url, 0, 'image/jpeg')
            except Exception:
                pass
            try:
                fe._FeedEntry__setitem('media:thumbnail', {'url': image_url})
            except Exception:
                pass

    build_time = meta.get('build_time') or datetime.utcnow().replace(tzinfo=timezone.utc)
    fg.lastBuildDate(format_datetime(last_modified or build_time))
    rss_bytes = fg.rss_str(pretty=True)
    etag = hashlib.sha256(rss_bytes).hexdigest()
    headers = {
        'ETag': f'W/"{etag}"',
        'Last-Modified': format_datetime(last_modified or build_time),
        'Cache-Control': 'max-age=300',
    }
    return rss_bytes, headers

def _build_fallback(items, meta):
    # minimale ma compatibile XML RSS 2.0 con namespace media e dc
    import xml.etree.ElementTree as ET
    ET.register_namespace("media", "http://search.yahoo.com/mrss/")
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = meta.get('title') or "ARTBOOMS"
    ET.SubElement(channel, "link").text = meta.get('self_url') or ""
    ET.SubElement(channel, "description").text = meta.get('description') or ""
    if meta.get('language'):
        ET.SubElement(channel, "language").text = meta.get('language')
    last_modified = None

    for it in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = it.get('title') or ""
        ET.SubElement(item, "link").text = it.get('url') or ""
        ET.SubElement(item, "guid").text = it.get('url') or ""
        if it.get('description'):
            desc = ET.SubElement(item, "description")
            desc.text = it.get('description')
        author = it.get('author')
        if author:
            dc_creator = ET.SubElement(item, "{http://purl.org/dc/elements/1.1/}creator")
            dc_creator.text = author
        pub = _as_dt(it.get('published'))
        if pub:
            ET.SubElement(item, "pubDate").text = format_datetime(pub)
        mod = _as_dt(it.get('modified'))
        if mod:
            last_modified = mod if (last_modified is None or mod > last_modified) else last_modified
            # dcterms:modified not standard in ET convenience, aggiungiamo come elemento semplice
            modtag = ET.SubElement(item, "{http://purl.org/dc/terms/}modified")
            modtag.text = format_datetime(mod)
        image = it.get('image')
        if image:
            # media:thumbnail
            thumb = ET.SubElement(item, "{http://search.yahoo.com/mrss/}thumbnail")
            thumb.set("url", image)

    build_time = meta.get('build_time') or datetime.utcnow().replace(tzinfo=timezone.utc)
    # lastBuildDate come elemento channel
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(last_modified or build_time)

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    etag = hashlib.sha256(xml_bytes).hexdigest()
    headers = {
        'ETag': f'W/"{etag}"',
        'Last-Modified': format_datetime(last_modified or build_time),
        'Cache-Control': 'max-age=300',
    }
    return xml_bytes, headers
