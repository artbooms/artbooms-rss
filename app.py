from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import xml.sax.saxutils as saxutils

app = Flask(__name__)

FEED_URL = 'https://www.artbooms.com/archivio-completo'
SITE_URL = 'https://www.artbooms.com'
RSS_URL = 'https://artbooms-rss.onrender.com/rss.xml'

def escape_xml(text):
    if not text:
        return ''
    text = text.replace('’', "'").replace('“', '"').replace('”', '"')
    return saxutils.escape(text.strip())

def get_articles():
    res = requests.get(FEED_URL)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    items = []
    for li in soup.select('li.archive-item'):
        title_tag = li.select_one('a.archive-item-link')
        date_tag = li.select_one('span.archive-item-date-before')
        link = title_tag['href'] if title_tag else ''
        title = title_tag.text if title_tag else 'No title'
        date_str = date_tag.text if date_tag else ''

        try:
            pub_date = datetime.datetime.strptime(date_str, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        full_link = f"{SITE_URL}{link}"
        items.append({
            'title': escape_xml(title),
            'link': escape_xml(full_link),
            'guid': escape_xml(full_link),
            'pub_date': pub_date
        })

    return items

@app.route('/rss.xml')
def rss():
    articles = get_articles()
    rss_items = ''
    for item in articles:
        rss_items += f"""
        <item>
            <title>{item['title']}</title>
            <link>{item['link']}</link>
            <guid isPermaLink="true">{item['guid']}</guid>
            <pubDate>{item['pub_date']}</pubDate>
        </item>"""

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Artbooms RSS Feed</title>
    <link>{SITE_URL}/archivio-completo</link>
    <atom:link href="{RSS_URL}" rel="self" type="application/rss+xml" />
    <description>Feed dinamico degli articoli di Artbooms</description>
    <language>it-it</language>
    {rss_items}
  </channel>
</rss>"""

    return Response(rss_feed, mimetype='application/rss+xml')

if __name__ == '__main__':
    app.run(debug=True)
