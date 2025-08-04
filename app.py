from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging

app = Flask(__name__)

FEED_URL = 'https://www.artbooms.com/archivio-completo'
BASE_URL = 'https://www.artbooms.com'

def clean_text(text):
    if not text:
        return ''
    replacements = {
        '\u200b': '', '\u200c': '', '\u200d': '', '…': '...', '’': "'",
        '“': '"', '”': '"', '–': '-', '—': '-', '\xa0': ' ',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)

def fetch_metadata(url):
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        og = lambda prop: soup.find("meta", property=prop)
        get = lambda p: og(p)['content'].strip() if og(p) and og(p).has_attr('content') else ''

        title = get('og:title')
        description = get('og:description')
        image = get('og:image')
        pub_date_meta = soup.find("meta", itemprop="datePublished")
        pub_date_str = pub_date_meta['content'] if pub_date_meta else ''
        try:
            pub_date = datetime.datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
        except Exception:
            pub_date = datetime.datetime.utcnow()
        pub_date_rss = pub_date.strftime('%a, %d %b %Y %H:%M:%S GMT')

        article_tag = soup.find('article', class_='hentry')
        category = ''
        author = ''
        if article_tag:
            for c in article_tag['class']:
                if c.startswith('category-'):
                    category = c.replace('category-', '')
                elif c.startswith('author-'):
                    author = c.replace('author-', '')

        return {
            'title': title,
            'link': url,
            'description': description,
            'image': image,
            'pub_date': pub_date_rss,
            'category': category,
            'author': author
        }
    except Exception as e:
        logging.warning(f"Errore nel parsing articolo {url}: {e}")
        return None

def get_article_links():
    try:
        res = requests.get(FEED_URL, timeout=10)
        res.raise_for_status()
    except Exception as e:
        logging.error(f"Errore fetching FEED_URL: {e}")
        return []

    soup = BeautifulSoup(res.text, 'html.parser')
    links = []
    for li in soup.select('li.archive-item'):
        tag = li.select_one('a.archive-item-link')
        if tag and tag['href']:
            links.append(BASE_URL + tag['href'])
    return links

@app.route('/rss.xml')
def rss():
    article_urls = get_article_links()
    rss_items = ''
    for url in article_urls:
        item = fetch_metadata(url)
        if not item:
            continue

        rss_items += f"""
        <item>
            <title>{clean_text(item['title'])}</title>
            <link>{clean_text(item['link'])}</link>
            <guid isPermaLink="true">{clean_text(item['link'])}</guid>
            <pubDate>{item['pub_date']}</pubDate>
            <description><![CDATA[{item['description']}]]></description>
            <category>{clean_text(item['category'])}</category>
            <author>{clean_text(item['author'])}</author>
            <enclosure url="{clean_text(item['image'])}" type="image/jpeg" />
        </item>"""

    last_build_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" 
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Artbooms RSS Feed</title>
    <link>{BASE_URL}/archivio-completo</link>
    <description>Ultimi articoli da
