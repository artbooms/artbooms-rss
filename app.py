# app.py
import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify
from datetime import datetime
import xml.etree.ElementTree as ET
import json
import os
import time
import threading
import hashlib
import logging

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

ARCHIVE_URL = 'https://www.artbooms.com/archivio-completo'
BASE_URL = 'https://www.artbooms.com'
CACHE_FILE = 'articles_cache.json'
BATCH_SIZE = 10           # quanti articoli processare per batch (tu volevi 10)
SLEEP_BETWEEN_ART = 1.2   # pausa tra richieste per non sovraccaricare il sito
REQUEST_TIMEOUT = 8       # timeout per singola richiesta HTTP

# stato del worker
_worker_thread = None
_worker_lock = threading.Lock()
_worker_running = False

# lock per accesso al file cache
_cache_lock = threading.Lock()

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Artbooms-RSS/1.0)"}


def load_cache():
    with _cache_lock:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                try:
                    return json.load(f)
                except Exception as e:
                    logging.warning(f"Errore leggendo cache JSON: {e}")
                    return []
        else:
            return []


def save_cache(cache):
    with _cache_lock:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_archive_links(session):
    """Ritorna lista di link articoli unici in ordine di apparizione."""
    logging.info("Fetch archive links...")
    r = session.get(ARCHIVE_URL, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    anchors = soup.select('a[href^="/blog/"]')
    seen = set()
    urls = []
    for a in anchors:
        href = a.get('href')
        if not href:
            continue
        full = BASE_URL + href if href.startswith('/') else href
        if full not in seen:
            seen.add(full)
            urls.append(full)
    logging.info(f"Trovati {len(urls)} link nell'archivio.")
    return urls


def safe_get_meta(soup, prop=None, itemprop=None, name=None):
    """Helper per ottenere meta tag con diverse strategie."""
    if prop:
        tag = soup.find('meta', property=prop)
        if tag and tag.get('content'):
            return tag['content'].strip()
    if itemprop:
        tag = soup.find(attrs={'itemprop': itemprop})
        if tag:
            if tag.get('content'):
                return tag['content'].strip()
            # alcuni itemprop possono essere <span itemprop="author">Nome</span>
            return tag.get_text(strip=True)
    if name:
        tag = soup.find('meta', attrs={'name': name})
        if tag and tag.get('content'):
            return tag['content'].strip()
    return ''


def parse_date_to_rss(datestr):
    """Prova a convertire varie forme ISO in RFC2822-like per RSS."""
    if not datestr:
        return datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    # prova fromisoformat (gestisce +/-HH:MM)
    try:
        # normalize timezone Z
        ds = datestr.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ds)
        return dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
    except Exception:
        # fallback: se è YYYY-MM-DD
        try:
            dt = datetime.strptime(datestr[:10], '%Y-%m-%d')
            return dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            return datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')


def compute_checksum(title, description, author, modified):
    s = (title or '') + '||' + (description or '') + '||' + (author or '') + '||' + (modified or '')
    return hashlib.md5(s.encode('utf-8')).hexdigest()


def fetch_article(session, url):
    """Estrae i metadati SEO dall'articolo."""
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        title = safe_get_meta(soup, prop='og:title') or (soup.title.string.strip() if soup.title else '')
        description = safe_get_meta(soup, prop='og:description') or safe_get_meta(soup, name='description')
        author = safe_get_meta(soup, prop='article:author') or safe_get_meta(soup, itemprop='author') or ''
        pub_raw = safe_get_meta(soup, itemprop='datePublished')
        mod_raw = safe_get_meta(soup, itemprop='dateModified') or pub_raw
        image = safe_get_meta(soup, prop='og:image')

        pubDate = parse_date_to_rss(pub_raw)
        lastModified = parse_date_to_rss(mod_raw)

        checksum = compute_checksum(title, description, author, lastModified)

        return {
            'title': title,
            'link': url,
            'guid': url,
            'pubDate': pubDate,
            'lastModified': lastModified,
            'description': description,
            'author': author,
            'image': image,
            'checksum': checksum,
            'fetched_at': datetime.utcnow().isoformat()
        }
    except Exception as e:
        logging.warning(f"Errore fetch article {url}: {e}")
        return None


def background_worker_main():
    """Worker che processa i batch fino a completare l'archivio, poi passa a check modifiche."""
    global _worker_running
    logging.info("Background worker partito.")
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        links = fetch_archive_links(session)
    except Exception as e:
        logging.error(f"Impossibile ottenere archive links nel worker: {e}")
        _worker_running = False
        return

    while True:
        cache = load_cache()
        done = {a['link']: a for a in cache}
        # nuovi da processare
        to_process = [u for u in links if u not in done][:BATCH_SIZE]
        if to_process:
            logging.info(f"Worker: processerò batch di {len(to_process)} articoli.")
            for url in to_process:
                try:
                    art = fetch_article(session, url)
                    if art:
                        # ricarica cache e salva (per sicurezza)
                        cache = load_cache()
                        cache_urls = {c['link'] for c in cache}
                        if art['link'] not in cache_urls:
                            cache.append(art)
                            save_cache(cache)
                            logging.info(f"Worker: aggiunto {url} (cache size {len(cache)})")
                        else:
                            # non dovrebbe succedere, ma in caso aggiorna
                            cache = [c for c in cache if c['link'] != art['link']] + [art]
                            save_cache(cache)
                    time.sleep(SLEEP_BETWEEN_ART)
                except Exception as e:
                    logging.warning(f"Worker: errore processing {url}: {e}")
            # dopo ogni batch lasciamo un piccolo riposo
            time.sleep(0.5)
            continue

        # se non ci sono nuovi, controlliamo a gruppi di BATCH_SIZE se qualche articolo è cambiato (checksum)
        logging.info("Worker: nessun nuovo articolo. Controllo modifiche a gruppi.")
        updated_any = False
        for i in range(0, len(links), BATCH_SIZE):
            group = links[i:i + BATCH_SIZE]
            for url in group:
                try:
                    art = fetch_article(session, url)
                    if not art:
                        continue
                    cache = load_cache()
                    idx = next((ix for ix, c in enumerate(cache) if c['link'] == url), None)
                    if idx is not None:
                        old = cache[idx]
                        if old.get('checksum') != art.get('checksum'):
                            cache[idx] = art
                            save_cache(cache)
                            updated_any = True
                            logging.info(f"Worker: articolo modificato aggiornato: {url}")
                    else:
                        # articolo non in cache (teoricamente non dovrebbe), lo aggiungiamo
                        cache.append(art)
                        save_cache(cache)
                        updated_any = True
                        logging.info(f"Worker: articolo aggiunto in fase verifica: {url}")
                    time.sleep(SLEEP_BETWEEN_ART)
                except Exception as e:
                    logging.warning(f"Worker: errore check-modify {url}: {e}")
            # dopo ogni gruppo facciamo una pausa e poi proseguiamo alla prossima
            time.sleep(1)
        if not updated_any:
            logging.info("Worker: nessuna modifica trovata nell'intero archivio. Worker terminerà e resterà dormiente.")
            break

    _worker_running = False
    logging.info("Background worker terminato.")


def ensure_worker_running():
    """Avvia il worker in background solo se non già attivo."""
    global _worker_thread, _worker_running
    with _worker_lock:
        if _worker_running:
            return
        _worker_running = True
        _worker_thread = threading.Thread(target=background_worker_main, daemon=True)
        _worker_thread.start()
        logging.info("Worker lanciato (thread daemon).")


@app.route('/rss.xml')
def rss():
    # Start worker if needed (non blocca la risposta)
    try:
        # se cache non contiene tutti i link avvia il worker
        session = requests.Session()
        session.headers.update(HEADERS)
        links = fetch_archive_links(session)
        cache = load_cache()
        if len(cache) < len(links):
            ensure_worker_running()
    except Exception as e:
        logging.warning(f"rss(): non sono riuscito ad ottenere archive links: {e}")

    # Genera il feed sulla base della cache attuale (ritorno immediato)
    cache = load_cache()
    rss = ET.Element('rss', version='2.0', attrib={'xmlns:atom': 'http://www.w3.org/2005/Atom'})
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'Artbooms RSS Feed'
    ET.SubElement(channel, 'link').text = ARCHIVE_URL
    ET.SubElement(channel, 'description').text = 'Feed dinamico e arricchito'
    ET.SubElement(channel, 'language').text = 'it-it'
    ET.SubElement(channel, 'lastBuildDate').text = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    ET.SubElement(channel, 'atom:link', attrib={
        'href': ARCHIVE_URL,
        'rel': 'self',
        'type': 'application/rss+xml'
    })

    for item in sorted(cache, key=lambda x: x.get('pubDate', ''), reverse=True):
        i = ET.SubElement(channel, 'item')
        ET.SubElement(i, 'title').text = item.get('title', '')
        ET.SubElement(i, 'link').text = item.get('link', '')
        ET.SubElement(i, 'guid', attrib={'isPermaLink': 'true'}).text = item.get('guid', '')
        ET.SubElement(i, 'pubDate').text = item.get('pubDate', '')
        ET.SubElement(i, 'description').text = item.get('description', '')
        ET.SubElement(i, 'author').text = item.get('author', '')
        if item.get('lastModified'):
            ET.SubElement(i, 'lastModified').text = item.get('lastModified', '')
        if item.get('image'):
            ET.SubElement(i, 'enclosure', attrib={'url': item.get('image'), 'type': 'image/webp'})

    xml_str = ET.tostring(rss, encoding='utf-8')
    return Response(xml_str, mimetype='application/rss+xml')


@app.route('/rss-status')
def rss_status():
    """Endopoint di stato per debug e monitoraggio."""
    cache = load_cache()
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        links = fetch_archive_links(session)
    except Exception:
        links = []
    return jsonify({
        "cache_count": len(cache),
        "links_count": len(links),
        "worker_running": _worker_running
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
