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
ARTICLES_PER_RUN = 5  # numero di articoli per chiamata


# Recupera tutti i link dall'archivio
def fetch_archive_links():
    response = requests.get(ARCHIVE_URL, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.select('a[href^="/blog/"]')
    urls = list({BASE_URL + a['href'] for a in links})
    return urls


# Recupera i dati dall'articolo
def fetch_article_data(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    # Titolo SEO
    title_tag = soup.find('meta', property='og:title')
    title = title_tag['content'] if title_tag else soup.title.text.strip()

    # Descrizione
    description_tag = soup.find('meta', attrs={'name': 'description'})
    description = description_tag['content'] if description_tag else ''

    # Autore
    author_tag = soup.find(itemprop='author')
    author = author_tag.text.strip() if author_tag else ''

    # Data pubblicazione
    date_pub_tag = soup.find(itemprop='datePublished')
    date_pub = date_pub_tag['content'] if date_pub_tag else '2025-01-01'

    # Data modifica
    date_mod_tag = soup.find(itemprop='dateModified')
    date_mod = date_mod_tag['content'] if date_mod_tag else date_pub

    # Immagine principale
    image_tag = soup.find('meta', property='og:image')
    image = image_tag['content'] if image_tag else ''

    return {
        'title': title,
        'link': url,
        'guid': url,
        'pubDate': datetime.strptime(date_pub, '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT'),
        'lastModified': datetime.strptime(date_mod, '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT'),
        'description': description,
        'author': author,
        'image': image,
        'lastUpdate': datetime.utcnow().isoformat()
    }


# Carica cache
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


# Salva cache
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

    # Se ci sono articoli non ancora processati → batch
    to_parse = [url for url in archive_links if url not in done_urls][:ARTICLES_PER_RUN]
    for url in to_parse:
        try:
            article = fetch_article_data(url)
            cache.append(article)
            time.sleep(1)  # evita stress sul server
        except Exception:
            continue

    # Se abbiamo finito tutti gli articoli, aggiorna solo i modificati
    if not to_parse:
        for url in archive_links:
            try:
                article = fetch_article_data(url)
                for i, old in enumerate(cache):
                    if old['link'] == url and old['lastModified'] != article['lastModified']:
                        cache[i] = article  # aggiornamento
                        break
                else:
                    if url not in done_urls:
                        cache.append(article)
            except Exception:
                continue

    save_cache(cache)

    # Creazione XML
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
        ET.SubElement(i, 'lastModified').text = item['lastModified']
        if item['image']:
            ET.SubElement(i, 'enclosure', attrib={'url': item['image'], 'type': 'image/webp'})

    xml_str = ET.tostring(rss, encoding='utf-8')
    return Response(xml_str, mimetype='application/rss+xml')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
