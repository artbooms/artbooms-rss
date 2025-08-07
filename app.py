from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

FEED_URL = 'https://www.artbooms.com/archivio-completo'
BASE_URL = 'https://www.artbooms.com'

def clean_text(text):
    if not text:
        return ''
    replacements = {
        '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', ' ': ' ',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text, quote=True)

def extract_metadata(article_url):
    try:
        res = requests.get(article_url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')

        description = soup.find('meta', {'itemprop': 'description'})
        image = soup.find('meta', {'itemprop': 'image'})
        author = soup.find('meta', {'itemprop': 'author'})
        date_published = soup.find('meta', {'itemprop': 'datePublished'})
        date_modified = soup.find('meta', {'itemprop': 'dateModified'})

        return {
            'description': description['content'] if description else '',
            'image': image['content'] if image else '',
            'author': author['content'] if author else '',
            'datePublished': date_published['content'] if date_published else '',
            'dateModified': date_modified['content'] if date_modified else ''
        }

    except Exception as e:
        logging.warning(f"Errore durante l'estrazione metadati da {article_url}: {e}")
        return {
            'description': '',
            'image': '',
            'author': '',
            'datePublished': '',
            'dateModified': ''
        }

def get_articles(limit=10):
    res = requests.get(FEED_URL, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    items = []
    for li in soup.select('li.archive-item')[:limit]:
        title_tag = li.select_one('a.archive-item-link')
        date_tag = li.select_one('span.archive-item-date-before')

        link = BASE_URL + title_tag['href']
        title = title_tag.text.strip()
        date_str = date_tag.text.strip()

        try:
            pub_date = datetime.datetime.strptime(date_str, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        meta = extract_metadata(link)

        items.append({
            'title': title,
            'link': link,
            'pub_date': pub_date,
            'description': meta['description'],
            'image': meta['image'],
            'author': meta['author'],
            'dateModified': meta['dateModified']
        })

        time.sleep(1.5)  # rallenta per evitare timeout su Render

    logging.info(f"Trovati {len(items)} articoli")
    return items

@app.route('/rss.xml')
def rss():
    try:
        articles = get_articles(limit=10)  # inizia con 10 articoli
    except Exception as e:
        logging.error(f"Errore nel recupero articoli: {e}")
        return Response("Errore nel generare il feed RSS", status=500)

    rss_items = ''
    for item in articles:
        rss_items += f"""
        <item>
            <title>{clean_text(item['title'])}</title>
            <link>{clean_text(item['link'])}</link>
            <guid isPermaLink="true">{clean_text(item['link'])}</guid>
            <pubDate>{item['pub_date']}</pubDate>
            <description>{clean_text(item['description'])}</description>
            <author>{clean_text(item['author'])}</author>
        </item>"""

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Artbooms RSS Feed</title>
    <link>{FEED_URL}</link>
    <description>Feed dinamico e arricchito</description>
    <language>it-it</language>
    <lastBuildDate>{datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
    <atom:link href="https://artbooms-rss.onrender.com/rss.xml" rel="self" type="application/rss+xml" />
    {rss_items}
  </channel>
</rss>"""

    return Response(rss_feed.strip(), content_type='application/rss+xml; charset=utf-8')

@app.route('/test-links')
def test_links():
    try:
        articles = get_articles(limit=3)
        links = [a['link'] for a in articles]
        return '<br>'.join(links)
    except Exception as e:
        return f"Errore: {e}"
