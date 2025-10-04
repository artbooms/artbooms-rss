import datetime
import xml.etree.ElementTree as ET

def generate_rss(articles, feed_url=None):
    rss = ET.Element(
        "rss",
        version="2.0",
        attrib={
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
            "xmlns:media": "http://search.yahoo.com/mrss/",
            "xmlns:ns1": "http://purl.org/dc/terms/",
        },
    )

    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "ARTBOOMS - Archivio completo"
    ET.SubElement(channel, "link").text = feed_url or ""
    ET.SubElement(channel, "description").text = "Tutti gli articoli di Artbooms con aggiornamenti automatici"
    ET.SubElement(channel, "language").text = "it-IT"

    for article in articles:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = article.get("title", "")
        ET.SubElement(item, "link").text = article.get("link", "")

        # GUID con isPermaLink per Google News
        guid_tag = ET.SubElement(item, "guid", attrib={"isPermaLink": "true"})
        guid_tag.text = article.get("link", "")

        ET.SubElement(item, "description").text = article.get("description", "")
        ET.SubElement(item, "dc:creator").text = article.get("author", "")
        ET.SubElement(item, "pubDate").text = article.get("published", "")

        if article.get("modified"):
            ET.SubElement(item, "ns1:modified").text = article.get("modified", "")

        if article.get("image"):
            ET.SubElement(
                item, "media:thumbnail", attrib={"url": article["image"]}
            )

    # Data di ultima modifica feed
    if articles:
        latest = max(a.get("modified", "") or a.get("published", "") for a in articles)
        if latest:
            ET.SubElement(channel, "lastBuildDate").text = latest

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True).decode("utf-8")
