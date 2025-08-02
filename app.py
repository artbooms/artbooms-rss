from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html

app = Flask(__name__)

FEED_URL = 'https://www.artbooms.com/archivio-completo'

def sanitize(text):
    return html.escape(text.strip()) if text else ''

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

        full_link = f"https://www.artbooms.com{link}"
        items.append({
            'title': sanitize(title),
            'link': sanitize(full_link),
            'pub_date': pub_date,
            'guid': sanitize(full_link)
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

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
    <channel>
        <title>Artbooms RSS Feed</title>
        <link>https://www.artbooms.com/archivio-completo</link>
        <description>Feed dinamico degli articoli di Artbooms</description>
        <language>it-it</language>
        {rss_items}
    </channel>
</rss>"""
    
    return Response(rss_feed, mimetype='application/rss+xml')

if __name__ == '__main__':
    app.run(debug=True)
