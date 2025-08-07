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
ARTICLES_PER_RUN = 3


def fetch_archive_links():
    response = requests.get(ARCHIVE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.select('a[href^="/blog/"]')
    urls = list({BASE_URL + a['href'] for a in links})
    return urls


def fetch_article_data(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    title = soup.find('meta', property='og:title')
    if title:
        title = title['content']
    else:
        title = soup.title.text.strip()

    description = soup.find('meta', attrs={'name': 'description'})
    description = description['content'] if description else ''

    author_tag = soup.find(itemprop='author')
    author = author_tag.text.strip() if author_tag else ''

    date_tag = soup.find(itemprop='datePublished')
    date = date_tag['content'] if date_tag else '2025-01-01'

    image_tag = soup.find('meta', property='og:image')
    image = image_tag['content'] if image_tag else ''

    return {
        'title': title,
        'link': url,
        'guid': url,
        'pubDate': datetime.strptime(date, '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT'),
        'description': description,
        'author': author,
        'image': image,
        'lastUpdate': datetime.utcnow().isoformat()
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
            continue  # Silenziosamente ignora errori

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
