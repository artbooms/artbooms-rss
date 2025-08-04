from flask import Flask, make_response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

FEED_URL = 'https://www.artbooms.com/archivio-completo'

def clean_text(text):
    if not text:
        return ''
    replacements = {'…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', ' ': ' '}
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)

def get_articles():
    res = requests.get(FEED_URL, timeout=10)
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
            pub_date = datetime.datetime.strptime(date_str, '%b %d, %Y')\
                        .strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        full_link = 'https://www.artbooms.com' + link
        description = ''
        image_url = ''

        try:
            a = requests.get(full_link, timeout=10)
            a.raise_for_status()
            article = BeautifulSoup(a.text, 'html.parser')

            p = article.select_one('div.sqs-block-content p')
            if p: description = clean_text(p.get_text())

            img = article.select_one('img')
            if img and img.get('src'): image_url = img['src']

        except Exception as e:
            logging.error(f"Errore parsing articolo {full_link}: {e}")

        items.append({
            'title': title,
            'link': full_link,
            'pub_date': pub_date,
            'description': description,
            'image_url': image_url
        })

    return items

@app.route('/rss.xml')
def rss():
    items = get_articles()
    rss_items = ''
    for it in items:
        rss_items += f"""
        <item>
            <title>{clean_text(it['title'])}</title>
            <link>{clean_text(it['link'])}</link>
            <guid isPermaLink="true">{clean_text(it['link'])}</guid>
            <pubDate>{it['pub_date']}</pubDate>"""
        if it['description']:
            rss_items += f"\n            <description>{it['description']}</description>"
        if it['image_url']:
            rss_items += f"""
            <enclosure url="{clean_text(it['image_url'])}" type="image/jpeg" length="0" />"""
        rss_items += "\n        </item>"

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
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

    resp = make_response(rss.strip())
    resp.headers['Content-Type'] = 'application/rss+xml; charset=utf-8'
    resp.headers['Content-Encoding'] = 'identity'
    return resp

if __name__ == '__main__':
    app.run(debug=True)
