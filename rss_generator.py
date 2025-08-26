from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element, SubElement
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib

NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'dcterms': 'http://purl.org/dc/terms/',
    'media': 'http://search.yahoo.com/mrss/'
}
for p, uri in NS.items():
    ET.register_namespace(p, uri)


def _as_dt(s):
    if isinstance(s, datetime):
        return s
    if not s:
        return None
    try:
        s = s.replace('Z', '+00:00')
        return datetime.fromisoformat(s)
    except Exception:
        return None


def build_rss(items, meta):
    rss = Element('rss', attrib={'version': '2.0', 'xmlns:atom': NS['atom'], 'xmlns:dc': NS['dc'], 'xmlns:dcterms': NS['dcterms'], 'xmlns:media': NS['media']})
    ch = SubElement(rss, 'channel')

    SubElement(ch, 'title').text = meta.get('title')
    SubElement(ch, 'link').text = 'https://www.artbooms.com'
    SubElement(ch, 'description').text = meta.get('description')

    self_url = meta.get('self_url')
    if self_url:
        SubElement(ch, f"{{{NS['atom']}}}link", attrib={'href': self_url, 'rel': 'self', 'type': 'application/rss+xml'})

    last_mod = None

    for it in items:
        item = SubElement(ch, 'item')
        link = it.get('url')
        SubElement(item, 'title').text = it.get('title') or link
        SubElement(item, 'link').text = link
        SubElement(item, 'guid', attrib={'isPermaLink': 'true'}).text = link

        if it.get('description'):
            SubElement(item, 'description').text = it.get('description')
        if it.get('author'):
            SubElement(item, f"{{{NS['dc']}}}creator").text = it.get('author')

        pub = _as_dt(it.get('published'))
        mod = _as_dt(it.get('modified')) or pub
        if pub:
            SubElement(item, 'pubDate').text = format_datetime(pub)
        if mod:
            SubElement(item, f"{{{NS['dcterms']}}}modified").text = format_datetime(mod)
            if (last_mod is None) or (mod > last_mod):
                last_mod = mod

        # immagine come enclosure e media:content
        img = it.get('image')
        if img:
            SubElement(item, 'enclosure', attrib={'url': img, 'type': 'image/jpeg'})
            SubElement(item, f"{{{NS['media']}}}content", attrib={'url': img, 'medium': 'image'})

    # lastBuildDate
    build = last_mod or datetime.utcnow().replace(tzinfo=timezone.utc)
    SubElement(ch, 'lastBuildDate').text = format_datetime(build)

    xml_bytes = ET.tostring(rss, encoding='utf-8')
    etag = hashlib.sha256(xml_bytes).hexdigest()
    headers = {
        'ETag': f'W/"{etag}"',
        'Last-Modified': format_datetime(build),
        'Cache-Control': 'public, max-age=300'
    }
    return xml_bytes, headers
