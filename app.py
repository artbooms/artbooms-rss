import requests
from bs4 import BeautifulSoup
from flask import Flask, Response
from datetime import datetime
import xml.etree.ElementTree as ET
import json
import os
import time
import threading

app = Flask(__name__)

ARCHIVE_URL = 'https://www.artbooms.com/archivio-completo'
BASE_URL = 'https://www.artbooms.com'
CACHE_FILE = 'articles_cache.json'
ARTICLES_PER_BATCH = 5
SLEEP_BETWEEN_BATCHES = 2  # secondi di pausa per non stressare il server


def fetch_archive_links():
    """Scarica tutti i link degli articoli dall'archivio."""
    response = requests.get(ARCHIVE_URL, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.select('a[href^="/blog/"]')
    urls = list({BASE_URL + a['href'] for a in links})
    return urls


def fetch_article_data(url):
    """Scarica i dati SEO e le informazioni dall'articolo."""
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    title_tag = soup.find('meta', property='og:title')
    title = title_tag['content'] if title_tag else soup.title.text.strip()

    description_tag = soup.find('meta', attrs={'name': 'description'})
    description = description_tag['content'] if description_tag else ''

    author_tag = soup.find(itemprop='author')
    author = author_tag.text.strip() if author_tag else ''

    date_tag = soup.find(itemprop='datePublished')
    date = date_tag['content'] if date_tag else '2025-01-01'

    updated_tag = soup.find(itemprop='dateModified')
    updated = updated_tag['content'] if updated_tag else date

    image_tag = soup.find('meta', property='og:image')
    image = image_tag['content'] if image_tag else ''

    return {
        'title': title,
        'link': url,
        'guid': url,
        'pubDate': datetime.strptime(date, '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT'),
        'lastUpdate': datetime.strptime(updated, '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT'),
        'description': description,
        'author': author,
        'image': image
    }


def load_cache():
    """Carica il file JSON con gli articoli già salvati."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_cache(cache):
    """Salva il file JSON con tutti gli articoli."""
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def background_loader():
    """Carica tutti gli articoli in blocchi senza bloccare il feed."""
    while True:
        try:
            cache = load_cache()
            cache_dict = {a['link']: a for a in cache}
            archive_links = fetch_archive_links()

            # articoli ancora da processare
            to_parse = []
            for link in archive_links:
                if link not in cache_dict:
                    to_parse.append(link)
                else:
                    # Controlla modifiche
                    try:
                        latest_data = fetch_article_data(link)
                        if latest_data['lastUpdate'] != cache_dict[link]['lastUpdate']:
                            cache_dict[link] = latest_data
                            print(f"Aggiornato articolo modificato: {link}")
                    except Exception:
                        continue

            # salva eventuali aggiornamenti da modifiche
            save_cache(list(cache_dict.values()))

            # batch caricamento nuovi
            if to_parse:
                batch = to_parse[:ARTICLES_PER_BATCH]
                for url in batch:
                    try:
                        article = fetch_article_data(url)
                        cache_dict[url] = article
                        save_cache(list(cache_dict.values()))
                        time.sleep(1)
                    except Exception:
                        continue
                time.sleep(SLEEP_BETWEEN_BATCHES)
            else:
                # archivio completo → ricontrolla ogni 5 min
                time.sleep(300)

        except Exception as e:
            print(f"Errore loader: {e}")
            time.sleep(60)


@app.route('/rss.xml')
def rss():
    """Genera il feed RSS dai dati salvati in cache."""
    cache = load_cache()

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
        if item.get('image'):
            ET.SubElement(i, 'enclosure', attrib={'url': item['image'], 'type': 'image/jpeg'})

    xml_str = ET.tostring(rss, encoding='utf-8')
    return Response(xml_str, mimetype='application/rss+xml')


if __name__ == '__main__':
    threading.Thread(target=background_loader, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
