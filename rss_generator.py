mport hashlib
from datetime import datetime, timezone
from email.utils import format_datetime
import logging
import xml.etree.ElementTree as ET

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

def build_rss(items: list, meta: dict):
    # Namespace richiesti
    ET.register_namespace("media", "http://search.yahoo.com/mrss/")
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    ET.register_namespace("dcterms", "http://purl.org/dc/terms/")
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")

    # Calcola subito l'ultima modifica (per posizionare lastBuildDate PRIMA degli item)
    last_modified = None
    for it in items:
        dt = _as_dt(it.get("modified")) or _as_dt(it.get("published"))
        if dt and (last_modified is None or dt > last_modified):
            last_modified = dt
    build_time = meta.get("build_time") or datetime.utcnow().replace(tzinfo=timezone.utc)
    last_build = last_modified or build_time

    # Root e channel
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    title = meta.get("title", "ARTBOOMS - Archivio completo")
    self_url = meta.get("self_url", "https://artbooms-rss.onrender.com/rss")
    desc = meta.get("description", "Tutti gli articoli di Artbooms con aggiornamenti automatici")
    lang = meta.get("language", "it-IT")

    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = "https://www.artbooms.com"
    ET.SubElement(channel, "description").text = desc
    ET.SubElement(channel, "language").text = lang

    # atom:link self ASSOLUTO
    atom_link = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", self_url)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # lastBuildDate PRIMA degli item (elimina "Misplaced Item")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(last_build)

    # (Niente <image> di canale: elimina warning "image format/title")
    # Se un giorno vuoi aggiungerlo, usa un .jpg/.png e metti lo stesso titolo del channel.

    for it in items:
        item = ET.SubElement(channel, "item")
        title_i = it.get("title") or ""
        url = it.get("url") or ""
        desc_i = it.get("description") or ""
        author = it.get("author") or None
        pub = _as_dt(it.get("published"))
        mod = _as_dt(it.get("modified"))
        image = it.get("image")

        ET.SubElement(item, "title").text = title_i
        ET.SubElement(item, "link").text = url

        guid = ET.SubElement(item, "guid", {"isPermaLink": "true"})
        guid.text = url

        # se manca description -> excerpt automatico
        if not desc_i:
            desc_i = _make_excerpt(title_i or url)
        ET.SubElement(item, "description").text = desc_i

        if author:
            ET.SubElement(item, "{http://purl.org/dc/elements/1.1/}creator").text = author

        if pub:
            ET.SubElement(item, "pubDate").text = format_datetime(pub)

        if mod:
            ET.SubElement(item, "{http://purl.org/dc/terms/}modified").text = mod.astimezone(timezone.utc).isoformat()

        if image:
            thumb = ET.SubElement(item, "{http://search.yahoo.com/mrss/}thumbnail")
            thumb.set("url", image)

    # Serializza + header HTTP
    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    etag = hashlib.sha256(xml_bytes).hexdigest()
    headers = {
        "ETag": f'W/"{etag}"',
        "Last-Modified": format_datetime(last_build),
        "Cache-Control": "max-age=300",
        "Content-Type": "application/rss+xml; charset=utf-8",
    }
    return xml_bytes, headers

def _make_excerpt(text, length=200):
    clean = (text or "").replace("\n", " ").replace("\r", " ").strip()
    if len(clean) > length:
        clean = clean[:length].rsplit(" ", 1)[0] + "…"
    return clean
