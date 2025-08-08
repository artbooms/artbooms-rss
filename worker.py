import requests
from bs4 import BeautifulSoup
import logging

def fetch_article_urls_from_archive(archive_url="https://www.artbooms.com/archivio-completo"):
    try:
        r = requests.get(archive_url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Esempio di selettore: prendi tutti i link interni agli articoli nel feed archivio
        # Cambia 'a.post-link' con il selettore giusto per la tua pagina
        link_elements = soup.select("a.post-link") 
        
        urls = []
        for link in link_elements:
            href = link.get("href")
            if href and href.startswith("http"):
                urls.append(href)
            elif href:
                # Se è un link relativo lo converto in assoluto
                from urllib.parse import urljoin
                full_url = urljoin(archive_url, href)
                urls.append(full_url)

        logging.info(f"Trovati {len(urls)} articoli nell’archivio.")
        return urls

    except Exception as e:
        logging.error(f"Errore fetching archive URLs: {e}")
        return []
