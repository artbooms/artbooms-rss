from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging

from article_parser import parse_article  # il file che hai appena creato

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

FEED_URL = 'https://www.artbooms.com/archivio-completo'

XML_ESCAPE_REPLACEMENTS = {
    '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '\u00A0': ' ',
}

def clean_text(text):
    if not text:
        return ''
    for bad, good in XML_ESCAPE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)

def get_articles():
    res = requests.get(FEED_URL, timeout=8)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    items = []
    for li in soup.select('li.archive-item'):
        title_tag = li.select_one('a.archive-item-link')
        date_tag = li.select_one('span.archive-item-date-before')
        link_fragment = title_tag['href'] if title_tag else ''
        title = title_tag.text.strip() if title_tag else 'No title'
        date_str = date_tag.text.strip() if date_tag else ''

        try:
            pub_date_archive = datetime.datetime.strptime(date_str, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date_archive = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        full_link = 'https://www.artbooms.com' + link_fragment

        # Estrai i dettagli dell'articolo vero e proprio
        try:
            detailed = parse_article(full_link)
        except Exception as e:
            logging.warning(f"Fallita estrazione dettagli per {full_link}: {e}")
            detailed = {}

        # Decide pubDate: preferisce quello estratto dall'articolo se valido
        pub_date = detailed.get('pubDate') or pub_date_archive

        items.append({
            'title': title,
            'link': full_link,
            'guid': full_link,
            'pub_date': pub_date,
            'author': detailed.get('author'),
            'description': detailed.get('description'),
            'image': detailed.get('image'),
        })

    if not items:
        logging.warning("⚠️ Nessun articolo trovato nel parsing HTML.")
        raise RuntimeError("Nessun articolo trovato. Verifica la struttura della pagina.")

    logging.info(f"✅ Trovati {len(items)} articoli da {FEED_URL}")
    return items

@app.route('/rss.xml')
def rss():
    try:
        articles = get_articles()
    except Exception as e:
        logging.error(f"Errore durante il parsing degli articoli: {e}")
        return Response("Errore nel generare il feed RSS", status=500)

    rss_items = ''
    for item in articles:
        title = clean_text(item['title'])
        link = clean_text(item['link'])
        guid = clean_text(item['guid'])
        pubDate = item['pub_date']
        author = clean_text(item.get('author') or '')
        description = clean_text(item.get('description') or '')
        image_url = item.get('image')

        rss_items += f"""
        <item>
            <title>{title}</title>
            <link>{link}</link>
            <guid isPermaLink="true">{guid}</guid>
            <pubDate>{pubDate}</pubDate>"""

        if author:
            rss_items += f"\n            <dc:creator>{author}</dc:creator>"

        if description:
            rss_items += f"\n            <description>{description}</description>"

        if image_url:
            escaped_img = html.escape(image_url, quote=True)
            rss_items += f"""
            <media:content url="{escaped_img}" medium="image" />"""

        rss_items += "\n        </item>"

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Artbooms RSS Feed</title>
    <link>https://www.artbooms.com/archivio-completo</link>
    <description>Feed dinamico degli articoli di Artbooms</description>
    <language>it-it</language>
    <lastBuildDate>{datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
    <atom:link href="https://artbooms-rss.onrender.com/rss.xml" rel="self" type="application/rss+xml" />
    {rss_items}
  </channel>
</rss>"""

    return Response(rss_feed.strip(), content_type='application/rss+xml; charset=utf-8')

if __name__ == '__main__':
    app.run(debug=True)
