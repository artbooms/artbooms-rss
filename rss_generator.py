import hashlib
from datetime import datetime, timezone
from email.utils import format_datetime
import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger("rss_generator")

# ---------------------------------------------------------
# Utility per conversione date
# ---------------------------------------------------------
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


# ---------------------------------------------------------
# Funzione principale: crea feed RSS 2.0 completo
# ---------------------------------------------------------
def build_rss(items: list, meta: dict):
    """
    Crea un feed RSS 2.0 con namespace media, atom e dcterms,
    compatibile con Google News e lettori RSS.
    """
    ET.register_namespace("media", "http://search.yahoo.com/mrss/")
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    ET.register_namespace("dcterms", "http://purl.org/dc/terms/")
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    # -----------------------------
    # Meta del canale
    # -----------------------------
    title = meta.get("title", "ARTBOOMS - Archivio completo")
    self_url = "https://artbooms-rss.onrender.com/rss"
    desc = meta.get("description", "Tutti gli articoli di Artbooms con aggiornamenti automatici")
    lang = meta.get("language", "it-IT")

    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = "https://www.artbooms.com"
    ET.SubElement(channel, "description").text = desc
    ET.SubElement(channel, "language").text = lang

    # Immagine/logo principale (Google News friendly)
    img = ET.SubElement(channel, "image")
    ET.SubElement(img, "url").text = "https://www.artbooms.com/favicon.ico"
    ET.SubElement(img, "title").text = "ARTBOOMS"
    ET.SubElement(img, "link").text = "https://www.artbooms.com"

    # Atom self-link
    atom_link = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", self_url)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    last_modified = None

    # -----------------------------
    # Articoli (items)
    # -----------------------------
    for it in items:
        item = ET.SubElement(channel, "item")
        title = it.get("title") or ""
        url = it.get("url") or ""
        desc = it.get("description") or ""
        author = it.get("author") or None
        pub = _as_dt(it.get("published"))
        mod = _as_dt(it.get("modified"))
        image = it.get("image")

        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = url

        guid = ET.SubElement(item, "guid")
        guid.text = url
        guid.set("isPermaLink", "true")

        # Se manca description, creiamo un estratto automatico
        if not desc:
            desc = _make_excerpt(title or url)
        ET.SubElement(item, "description").text = desc

        # ✅ Autore giornalistico corretto
        if author:
            ET.SubElement(item, "{http://purl.org/dc/elements/1.1/}creator").text = author

        if pub:
            ET.SubElement(item, "pubDate").text = format_datetime(pub)

        if mod:
            ET.SubElement(item, "{http://purl.org/dc/terms/}modified").text = mod.astimezone(timezone.utc).isoformat()
            if (last_modified is None) or (mod > last_modified):
                last_modified = mod

        if image:
            thumb = ET.SubElement(item, "{http://search.yahoo.com/mrss/}thumbnail")
            thumb.set("url", image)

        # 📎 Fonte editoriale (Google News)
        source = ET.SubElement(item, "source")
        source.set("url", "https://www.artbooms.com")
        source.text = "ARTBOOMS"

        # 🕒 Commento invisibile per debug/monitoraggio
        item.append(ET.Comment(f"last_build: {datetime.utcnow().isoformat()}"))

    # -----------------------------
    # Ultima modifica globale
    # -----------------------------
    build_time = meta.get("build_time") or datetime.utcnow().replace(tzinfo=timezone.utc)
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(last_modified or build_time)

    # -----------------------------
    # Conversione XML e header HTTP
    # -----------------------------
    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    etag = hashlib.sha256(xml_bytes).hexdigest()
    headers = {
        "ETag": f'W/"{etag}"',
        "Last-Modified": format_datetime(last_modified or build_time),
        "Cache-Control": "max-age=300",
        "Content-Type": "application/rss+xml; charset=utf-8",
    }

    return xml_bytes, headers


# ---------------------------------------------------------
# Helper per generare un estratto breve
# ---------------------------------------------------------
def _make_excerpt(text, length=200):
    """
    Crea un breve riassunto (excerpt) da testo o titolo se manca la descrizione.
    """
    clean = text.replace("\n", " ").replace("\r", " ").strip()
    if len(clean) > length:
        clean = clean[:length].rsplit(" ", 1)[0] + "…"
    return clean
