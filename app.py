from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import logging
from article_processor import parse_article

app = Flask(__name__)

ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"

def fetch_article_urls():
    """
    Scarica la pagina archivio e ritorna lista di URL articoli.
    """
    try:
        resp = requests.get(ARCHIVE_URL, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        urls = []
        # Trova i link dentro <ul class="archive-item-list">
        archive_list = soup.find("ul", class_="archive-item-list")
        if not archive_list:
            logging.warning("Archivio non trovato o struttura cambiata")
            return urls

        for li in archive_list.find_all("li", class_="archive-item"):
            a = li.find("a", class_="archive-item-link")
            if a and a.get("href"):
                link = a["href"]
                if link.startswith("/"):
                    link = "https://www.artbooms.com" + link
                urls.append(link)
        return urls
    except Exception as e:
        logging.warning(f"Errore fetching archive: {e}")
        return []

def generate_rss(items):
    """
    Genera XML RSS da lista di dizionari articolo.
    """
    from datetime import datetime
    last_build_date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    rss_items = ""
    for item in items:
        if not item:
            continue
        rss_items += f"""
        <item>
            <title>{item['title']}</title>
            <link>{item['link']}</link>
            <description>{item['description']}</description>
            <pubDate>{item['date_published']}</pubDate>
            <enclosure url="{item['image']}" type="image/jpeg" />
        </item>
        """

    rss = f"""<?xml version="1.0" encoding="UTF-8" ?>
    <rss version="2.0">
    <channel>
        <title>Artbooms RSS Feed</title>
        <link>{ARCHIVE_URL}</link>
        <description>Feed dinamico e arricchito</description>
        <language>it-it</language>
        <lastBuildDate>{last_build_date}</lastBuildDate>
        {rss_items}
    </channel>
    </rss>
    """
    return rss

@app.route("/rss.xml")
def rss_feed():
    urls = fetch_article_urls()
    articles = [parse_article(u) for u in urls]
    rss_xml = generate_rss(articles)
    return Response(rss_xml, mimetype="application/rss+xml")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=10000)
