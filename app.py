from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging
import json
import os
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

FEED_URL = 'https://www.artbooms.com/archivio-completo'
CACHE_FILE = 'articles_cache.json'
PAGE_SIZE = 5  # articoli per batch
DELAY_BETWEEN_BATCHES = 1  # secondi di pausa tra batch per evitare timeout o sovraccarico

def clean_text(text):
    if not text:
        return ''
    replacements = {
        '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '\xa0': ' ',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text, quote=True)

def fetch_article_details(url):
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        description_tag = soup.find('meta', property='og:description') or soup.find('meta', itemprop='description')
        description = description_tag['content'] if description_tag else ''

        image_tag = soup.find('meta', property='og:image') or soup.find('meta', itemprop='image')
        image = image_tag['content'] if image_tag else ''

        author_tag = soup.find('meta', itemprop='author')
        author = author_tag['content'] if author_tag else ''

        date_published_tag = soup.find('meta', itemprop='datePublished')
        date_published = date_published_tag['content'] if date_published_tag else ''

        date_modified_tag = soup.find('meta', itemprop='dateModified')
        date_modified = date_modified_tag['content'] if date_modified_tag else ''

        return {
            'description': description,
            'image': image,
            'author': author,
            'date_published': date_published,
            'date_modified': date_modified,
        }
    except Exception as e:
        logging.warning(f"Errore fetching details da {url}: {e}")
        return {}

def parse_date_rss_format(datestr):
    try:
        if not datestr:
            return datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
        datestr = datestr.replace('+0200', '')
        dt = datetime.datetime.strptime(datestr, '%Y-%m-%dT%H:%M:%S')
        return dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
    except Exception:
        return datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

def fetch_all_articles_basic():
    res = requests.get(FEED_URL)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    articles = []
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
        articles.append({
            'title': title,
            'link': full_link,
            'pub_date': pub_date,
        })
    return articles

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_cache(data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def build_cache_full():
    logging.info("🔄 Inizio a costruire la cache completa degli articoli...")
    articles = fetch_all_articles_basic()

    detailed_articles = []
    for i in range(0, len(articles), PAGE_SIZE):
        batch = articles[i:i+PAGE_SIZE]
        logging.info(f"📝 Elaborazione batch articoli {i+1} - {i+len(batch)}")
        for art in batch:
            details = fetch_article_details(art['link'])
            detailed_articles.append({
                **art,
                **details,
            })
        save_cache({'articles': detailed_articles})
        logging.info(f"💾 Cache salvata con {len(detailed_articles)} articoli.")
        time.sleep(DELAY_BETWEEN_BATCHES)

    logging.info("✅ Cache completa costruita.")
    return detailed_articles

@app.route('/rss.xml')
def rss():
    try:
        cache = load_cache()
        if not cache or 'articles' not in cache:
            # Se cache vuota o assente costruiscila
            articles = build_cache_full()
        else:
            articles = cache['articles']

        rss_items = ''
        for item in articles:
            pub_date_final = parse_date_rss_format(item.get('date_modified')) if item.get('date_modified') else item['pub_date']

            rss_items += f"""
            <item>
                <title>{clean_text(item['title'])}</title>
                <link>{clean_text(item['link'])}</link>
                <guid isPermaLink="true">{clean_text(item['link'])}</guid>
                <pubDate>{pub_date_final}</pubDate>
                <description><![CDATA[{item.get('description', '')}]]></description>
                <author>{clean_text(item.get('author', ''))}</author>
                {f'<enclosure url="{item.get("image", "")}" type="image/jpeg" />' if item.get('image') else ''}
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

    except Exception as e:
        logging.error(f"Errore durante il parsing degli articoli: {e}")
        return Response("Errore nel generare il feed RSS", status=500)

if __name__ == '__main__':
    app.run(debug=True)
