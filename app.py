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
        '…': '...', '’': "'", '“': '"', '”': '"',
        '–': '-', '—': '-', ' ': ' ',  # spazio non-breaking
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
        description = ''
        image_url = ''

        try:
            article_res = requests.get(full_link, timeout=10)
            article_soup = BeautifulSoup(article_res.text, 'html.parser')
            first_paragraph = article_soup.select_one('div.sqs-block-content p')
            if first_paragraph:
                description = clean_text(first_paragraph.get_text())

            img_tag = article_soup.select_one('img')
            if img_tag and img_tag.has_attr('src'):
                image_url = img_tag['src']

        except Exception as e:
            logging.error(f"Errore durante l'estrazione da {full_link}: {e}")

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
    articles = get_articles()
    rss_items = ''
    for item in articles:
        enclosure = f'<enclosure url="{item["image_url"]}" type="image/jpeg" />' if item['image_url'] else ''
        description = f'<description>{item["description"]}</description>' if item['description'] else ''

        rss_items += f"""
        <item>
            <title>{clean_text(item['title'])}</title>
            <link>{clean_text(item['link'])}</link>
            <guid isPermaLink="true">{clean_text(item['link'])}</guid>
            <pubDate>{item['pub_date']}</pubDate>
            {description}
            {enclosure}
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

    return Response(rss_feed.strip(), mimetype='application/rss+xml')

if __name__ == '__main__':
    app.run(debug=True)
