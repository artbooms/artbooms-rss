import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Registra namespace Dublin Core (per <dc:creator>)
ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
ET.register_namespace("media", "http://search.yahoo.com/mrss/")

def build_rss_feed(articles, site_url="https://www.artbooms.com", feed_title="Artbooms RSS"):
    """
    Genera il feed RSS 2.0 con compatibilità Google News.
    Gli articoli devono essere ordinati dal più vecchio al più nuovo.
    I più recenti vengono visualizzati per primi nel feed.
    """

    # Root element
    rss = ET.Element(
        "rss",
        version="2.0",
        attrib={
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
            "xmlns:media": "http://search.yahoo.com/mrss/",
        },
    )

    # Channel
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = feed_title
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = (
        "Ultimi articoli e notizie d'arte da Artbooms.com — feed aggiornato automaticamente."
    )
    ET.SubElement(channel, "language").text = "it-IT"
    ET.SubElement(channel, "generator").text = "Artbooms RSS Generator"
    ET.SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    # Icona (puoi cambiarla in futuro con un logo vero)
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = "https://www.artbooms.com/assets/artbooms-logo.png"
    ET.SubElement(image, "title").text = feed_title
    ET.SubElement(image, "link").text = site_url

    # Costruisci gli item del feed
    # Mostra per primi i più recenti (ordine inverso)
    for art in reversed(articles):
        item = ET.SubElement(channel, "item")

        # Titolo e link
        ET.SubElement(item, "title").text = art.get("title") or "Articolo senza titolo"
        ET.SubElement(item, "link").text = art.get("url")

        # Descrizione (usa CDATA per sicurezza)
        description = art.get("description") or ""
        desc_elem = ET.SubElement(item, "description")
        desc_elem.text = f"<![CDATA[{description}]]>"

        # Date
        if art.get("published"):
            pub_date = datetime.fromisoformat(art["published"].replace("Z", "+00:00"))
            ET.SubElement(item, "pubDate").text = pub_date.strftime("%a, %d %b %Y %H:%M:%S %z")

        # Autore (Google News -> <dc:creator>)
        if art.get("author"):
            creator = ET.SubElement(item, "{http://purl.org/dc/elements/1.1/}creator")
            creator.text = art["author"]

        # Immagine principale
        if art.get("image"):
            media_thumb = ET.SubElement(item, "{http://search.yahoo.com/mrss/}thumbnail")
            media_thumb.set("url", art["image"])

        # GUID (identificatore univoco)
        guid = ET.SubElement(item, "guid")
        guid.set("isPermaLink", "true")
        guid.text = art.get("url")

    return ET.ElementTree(rss)


def save_rss_feed(articles, output_path="feed.xml"):
    """Salva il feed RSS su file XML leggibile."""
    tree = build_rss_feed(articles)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"Feed RSS salvato: {output_path}")
