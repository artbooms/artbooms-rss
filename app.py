import requests
from bs4 import BeautifulSoup
from flask import Flask, Response
from datetime import datetime
import xml.etree.ElementTree as ET
import json
import os
import time
import hashlib

app = Flask(__name__)

ARCHIVE_URL = 'https://www.artbooms.com/archivio-completo'
BASE_URL = 'https://www.artbooms.com'
CACHE_FILE = 'articles_cache.json'
ARTICLES_PER_RUN = 10


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
    title = title['content'].strip() if title else soup.title.text.strip()

    description = soup.find('meta', attrs={'name': 'description'})
    description = description['content'].strip() if description else ''

    author_tag = soup.find(itemprop='author')
    author = author_tag.text.strip() if author_tag else ''

    date_tag = soup.find(itemprop='datePublished')
    date_published = date_tag['content'] if date_tag else '2025-01-01'

    modified_tag = soup.find(itemprop='dateModified')
    date_modified = modified_tag['content'] if modified_tag else date_published

    image_tag = soup.find('meta', property='og:image')
    image = image_tag['content'] if image_tag else ''

    # Genera un checksum per confrontare i contenuti
    checksum = hashlib.md5((title + description + author + date_modified).encode('utf-8')).hexdigest()

    return {
        'title': title,
        'link': url,
        'guid': url,
        'pubDate': datetime.strptime(date_published, '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT'),
        'description': description,
        'author': author,
        'image': image,
        'lastUpdate': datetime.utcnow().isoformat(),
        'modified': date_modified,
        'checksum': checksum
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
    cache_by_url = {item['link']: item for item in cache}

    try:
        archive_links = fetch_archive_links()
    except Exception as e:
        return Response(f"Errore nel recupero dell'archivio: {str(e)}", status=500)

    to_process = [url for url in archive_links if url not in cache_by_url]
    modified_check = [url for url in archive_links if url in cache_by_url]

    processed = 0

    # Aggiunge nuovi articoli
    for url in to_process:
        if processed >= ARTICLES_PER_RUN:
            break
        try:
            article = fetch_article_data(url)
            cache.append(article)
            processed += 1
            time.sleep(1.5)
        except:
            continue

    # Aggiorna articoli modificati
    for url in modified_check:
        if processed >= ARTICLES_PER_RUN:
            break
        try:
            article = fetch_article_data(url)
            old = cache_by_url[url]
            if old['checksum'] != article['checksum']:
                cache = [a for a in cache if a['link'] != url]
                cache.append(article)
                processed += 1
                time.sleep(1.5)
        except:
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
