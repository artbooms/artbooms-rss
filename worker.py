import json
import logging
import time
import requests
from bs4 import BeautifulSoup

CACHE_FILE = "article_cache.json"
BATCH_SIZE = 10
ARTICLES_TO_PROCESS = []  # Qui metti la lista completa degli URL da processare

logging.basicConfig(level=logging.INFO)

def load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
            if not isinstance(cache, list):
                logging.warning("Cache caricata non è una lista, resetto a lista vuota.")
                return []
            return cache
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.warning(f"Errore caricando cache: {e}. Resetto a lista vuota.")
        return []

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Errore salvando cache: {e}")

def fetch_article(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Qui metti la logica di estrazione contenuti
        title = soup.title.string if soup.title else "No Title"
        return {"url": url, "title": title}
    except Exception as e:
        logging.warning(f"Errore processing {url}: {e}")
        return None

def process_batch(urls, cache):
    logging.info(f"Processerò batch di {len(urls)} articoli.")
    for url in urls:
        if any(article.get("url") == url for article in cache):
            logging.info(f"Articolo già in cache: {url}")
            continue
        article = fetch_article(url)
        if article:
            cache.append(article)
            save_cache(cache)
        else:
            logging.warning(f"Fallito fetch articolo: {url}")

def worker_main():
    cache = load_cache()
    start_index = 0
    total = len(ARTICLES_TO_PROCESS)

    while start_index < total:
        batch_urls = ARTICLES_TO_PROCESS[start_index:start_index + BATCH_SIZE]
        process_batch(batch_urls, cache)
        start_index += BATCH_SIZE
        logging.info(f"Processati {start_index}/{total} articoli.")
        # Se vuoi, metti un delay per non sovraccaricare il server
        time.sleep(1)

if __name__ == "__main__":
    # Esempio: carica qui la lista articoli da processare
    # Puoi anche leggere da file o API
    ARTICLES_TO_PROCESS = [
        "https://www.artbooms.com/blog/vivian-suter-palais-tokyo-parigi",
        "https://www.artbooms.com/blog/mimmo-jodice-mostra-udine",
        # ... altri URL ...
    ]

    worker_main()
