import requests
from bs4 import BeautifulSoup
from flask import Flask, Response
from datetime import datetime
import xml.etree.ElementTree as ET
import json
import os
import time

app = Flask(__name__)

ARCHIVE_URL = 'https://www.artbooms.com/archivio-completo'
BASE_URL = 'https://www.artbooms.com'
CACHE_FILE = 'articles_cache.json'
ARTICLES_PER_RUN = 10  # Carica 10 articoli per volta


def fetch_archive_links():
    response = requests.get(ARCHIVE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.select('a[href^="/blog/"]')
    urls = list({BASE_URL + a['href'] for a in links})
    return urls


def fetch_article_data(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    def get_meta(property_name):
        tag = soup.find('meta', property=property_name)
        return tag['content'] if tag else ''

    def get_itemprop(name):
        tag = soup.find(attrs={'itemprop': name})
        return tag['content'] if tag and 'content' in tag.attrs else tag.text.strip() if tag else ''

    title = get_meta('og:title') or soup.title.text.strip()
    description = get_meta('og:description') or ''
    author = get_meta('article:author') or get_itemprop('author') or 'Artbooms'
    image = get_meta('og:image') or ''
    pub_date_raw = get_itemprop('datePublished') or '2025-01-01'
    mod_date_raw = get_itemprop('dateModified') or pub_date_raw

    pub_date = datetime.strptime(pub_date_raw, '%Y-%m-%d')
    mod_date = datetime.strptime(mod_date_raw, '%Y-%m-%d')

    return {
        'title': title,
        'link': url,
        'guid': url,
        'pubDate': pub_date.strftime('%a, %d %b %Y 00:00:00 GMT'),
        'description': description,
        'author': author,
        'image': image,
        'lastUpdate': mod_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    }


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


@app.route('/rss.xml')
def rss():
    cache = load_cache()
    done_urls = {a['link'] for a in cache}

    try:
        archive_links = fetch_archive_links()
    except Exception as e:
        return Response(f"Errore nel recupero dell'archivio: {str(e)}", status=500)

    to_parse = [url for url in archive_links if url not in done_urls][:ARTICLES_PER_RUN]

    for url in to_parse:
        try:
            article = fetch_article_data(url)
            cache.append(article)
            time.sleep(1.5)
        except Exception as e:
            continue

    save_cache(cache)

    rss = ET.Element('rss', version='2.0', attrib={'xmlns:atom': 'http://www.w3.org/2005/Atom'})
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'Artbooms RSS Feed'
    ET.SubElement(channel, 'link').text = ARCHIVE_URL
    ET.SubElement(channel, 'description').text = 'Feed dinamico e arricchito'
    ET.SubElement(channel, 'language').text = 'it-it'
    ET.SubElement(channel, 'lastBuildDate').text = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    ET.SubElement(channel, 'atom:link', attrib={
        'href': 'https://artbooms-rss.onrender.com/rss.xml',
        'rel': 'self',
        'type': 'application/rss+xml'
    })

    for item in sorted(cache, key=lambda x: x['pubDate'], reverse=True):
        i = ET.SubElement(channel, 'item')
        ET.SubElement(i, 'title').text = item['title']
        ET.SubElement(i, 'link').text = item['link']
        ET.SubElement(i, 'guid', attrib={'isPermaLink': 'true'}).text = item['guid']
        ET.SubElement(i, 'pubDate').text = item['pubDate']
        ET.SubElement(i, 'description').text = item['description']
        ET.SubElement(i, 'author').text = item['author']

    xml_str = ET.tostring(rss, encoding='utf-8')
    return Response(xml_str, mimetype='application/rss+xml')


if __name__ == '__main__':
    app.run(debug=True)
