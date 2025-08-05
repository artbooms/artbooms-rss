from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

FEED_URL = 'https://www.artbooms.com/archivio-completo'

def clean_text(text):
    """Rende il testo sicuro per XML e rimuove caratteri non validi"""
    if not text:
        return ''
    replacements = {
        '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', ' ': ' ',  # spazio non-breaking
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text, quote=True)

def get_articles():
    res = requests.get(FEED_URL)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    items = []
    for li in soup.select('li.archive-item'):
        title_tag = li.select_one('a.archive-item-link')
        date_tag = li.select_one('span.archive-item-date-before')
        link = title_tag['href'] if title_tag else ''
        title = title_tag.text.strip() if title_tag else 'No title'
        date_str = date_tag.text.strip() if date_tag else ''

        try:
            pub_date = datetime.datetime.strptime(date_str, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        full_link = 'https://www.artbooms.com' + link
        items.append({'title': title, 'link': full_link, 'pub_date': pub_date})

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
        rss_items += f"""
        <item>
            <title>{clean_text(item['title'])}</title>
            <link>{clean_text(item['link'])}</link>
            <guid isPermaLink="true">{clean_text(item['link'])}</guid>
            <pubDate>{item['pub_date']}</pubDate>
        </item>"""

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
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
