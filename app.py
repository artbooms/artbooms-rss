from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import time
import xml.etree.ElementTree as ET

app = Flask(__name__)

ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def extract_article_links():
    res = requests.get(ARCHIVE_URL, headers=HEADERS)
    soup = BeautifulSoup(res.content, "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/blog/" in href and href not in links:
            full_url = f"https://www.artbooms.com{href}" if href.startswith("/") else href
            links.append(full_url)

    return links


def parse_article(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.content, "html.parser")

        # SEO title
        title_tag = soup.find("meta", property="og:title")
        title = title_tag["content"] if title_tag else soup.title.string.strip()

        # Description
        desc_tag = soup.find("meta", property="og:description")
        description = desc_tag["content"] if desc_tag else ""

        # Author
        author_tag = soup.find("meta", attrs={"itemprop": "author"})
        author = author_tag["content"] if author_tag else "Artbooms"

        # Pub date
        pubdate_tag = soup.find("meta", attrs={"itemprop": "datePublished"})
        pub_date = pubdate_tag["content"] if pubdate_tag else None

        # Last modified
        mod_tag = soup.find("meta", attrs={"itemprop": "dateModified"})
        last_modified = mod_tag["content"] if mod_tag else pub_date

        # Image
        img_tag = soup.find("meta", property="og:image")
        image_url = img_tag["content"] if img_tag else None

        return {
            "title": title,
            "link": url,
            "description": description,
            "author": author,
            "pubDate": pub_date,
            "lastModified": last_modified,
            "image": image_url
        }

    except Exception as e:
        print(f"Errore su {url}: {e}")
        return None


@app.route("/rss.xml")
def rss():
    articles = []
    links = extract_article_links()

    for link in links:
        article = parse_article(link)
        if article:
            articles.append(article)
        time.sleep(1.5)  # Attendi 1.5 secondi tra le richieste

    rss = ET.Element("rss", version="2.0", attrib={"xmlns:atom": "http://www.w3.org/2005/Atom"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = "Artbooms RSS Feed"
    ET.SubElement(channel, "link").text = ARCHIVE_URL
    ET.SubElement(channel, "description").text = "Feed dinamico e arricchito"
    ET.SubElement(channel, "language").text = "it-it"
    ET.SubElement(channel, "lastBuildDate").text = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    ET.SubElement(channel, "atom:link", {
        "href": "https://artbooms-rss.onrender.com/rss.xml",
        "rel": "self",
        "type": "application/rss+xml"
    })

    for item in articles:
        item_el = ET.SubElement(channel, "item")
        ET.SubElement(item_el, "title").text = item["title"]
        ET.SubElement(item_el, "link").text = item["link"]
        ET.SubElement(item_el, "guid", isPermaLink="true").text = item["link"]
        ET.SubElement(item_el, "pubDate").text = datetime.strptime(item["pubDate"], "%Y-%m-%d").strftime("%a, %d %b %Y 00:00:00 GMT") if item["pubDate"] else ""
        ET.SubElement(item_el, "description").text = item["description"]
        ET.SubElement(item_el, "author").text = item["author"]

        # image as enclosure
        if item["image"]:
            ET.SubElement(item_el, "enclosure", url=item["image"], type="image/webp")

    xml_str = ET.tostring(rss, encoding="utf-8", method="xml")
    return Response(xml_str, mimetype="application/rss+xml")


@app.route("/")
def index():
    return "RSS feed disponibile su /rss.xml"


if __name__ == "__main__":
    app.run()
