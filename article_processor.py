import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging
import time

logging.basicConfig(level=logging.INFO)

BATCH_SIZE = 10
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"

def fetch_article_urls_from_archive(archive_url=ARCHIVE_URL):
    try:
        r = requests.get(archive_url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        link_elements = soup.select("ul.archive-item-list li.archive-item a.archive-item-link")
        urls = []
        for link in link_elements:
            href = link.get("href")
            if href:
                full_url = urljoin(archive_url, href)
                urls.append(full_url)
        logging.info(f"Trovati {len(urls)} articoli nell’archivio.")
        return urls
    except Exception as e:
        logging.error(f"Errore fetching archive URLs: {e}")
        return []

def parse_article(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        meta_tags = soup.find_all("meta")
        data = {}
        for tag in meta_tags:
            if tag.has_attr("property") and tag["property"].startswith("og:"):
                key = tag["property"][3:]
                data[key] = tag.get("content", "")
            if tag.has_attr("itemprop"):
                key = tag["itemprop"]
                data[key] = tag.get("content", "")
        data["url"] = url
        return data
    except Exception as e:
        logging.warning(f"Worker: errore processing {url}: {e}")
        return None

def process_batch(urls, cache):
    for url in urls:
        if url in cache:
            continue
        data = parse_article(url)
        if data:
            cache[url] = data
        time.sleep(0.1)  # evita di sovraccaricare il server

def run_all():
    cache = {}
    all_urls = fetch_article_urls_from_archive()
    total = len(all_urls)
    for start in range(0, total, BATCH_SIZE):
        batch_urls = all_urls[start:start+BATCH_SIZE]
        process_batch(batch_urls, cache)
        logging.info(f"Processati {min(start+BATCH_SIZE, total)}/{total} articoli.")
    return cache
