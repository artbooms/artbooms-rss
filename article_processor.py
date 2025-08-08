import requests
from bs4 import BeautifulSoup
import logging

def parse_article(url):
    """
    Scarica e analizza la pagina articolo per estrarre i meta tag.
    Restituisce un dizionario con i dati necessari per RSS.
    """
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        meta = {}

        # Estrazione meta tag og:* e itemprop:*
        for tag in soup.find_all("meta"):
            if tag.get("property") and tag.get("content"):
                meta[tag["property"]] = tag["content"]
            elif tag.get("itemprop") and tag.get("content"):
                meta[tag["itemprop"]] = tag["content"]

        return {
            "title": meta.get("og:title") or meta.get("name") or "No title",
            "link": meta.get("og:url") or url,
            "description": meta.get("og:description") or "",
            "image": meta.get("og:image") or "",
            "date_published": meta.get("datePublished") or meta.get("article:published_time") or "",
            "author": meta.get("author") or "",
        }
    except Exception as e:
        logging.warning(f"Worker: errore processing {url}: {e}")
        return None
