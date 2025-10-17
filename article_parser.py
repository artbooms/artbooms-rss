import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging
from datetime import datetime
from dateutil import parser as dateparser
import re

logger = logging.getLogger("Parser")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------
# Scarica HTML
# ---------------------------------------------------------
def fetch_html(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.error("[Parser] Errore fetch %s: %s", url, e)
        return ""


# ---------------------------------------------------------
# Estrazione link articoli (corretta e robusta)
# ---------------------------------------------------------
def extract_article_links_from_archive_html(html, base_url):
    """
    Estrae i link reali agli articoli dal codice HTML dell’archivio.
    Rimuove '/archivio-completo', date appese (2016210, ecc.) e normalizza.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or "?" in href or "/tag/" in href or href.startswith("#"):
            continue

        full_url = urljoin(base_url, href)
        full_url = re.sub(r"/archivio-completo/?", "/", full_url)

        # elimina eventuali numeri accodati al titolo tipo "2016210"
        full_url = re.sub(r"(\d{4,})$", "", full_url).rstrip("/")

        if "/blog/" in full_url:
            links.add(full_url)

    clean_links = sorted(list(links))
    logger.info("[Parser] %s link articolo validi trovati e normalizzati.", len(clean_links))
    return clean_links


# ---------------------------------------------------------
# Parsing di un singolo articolo
# ---------------------------------------------------------
def parse_article(url):
    html = fetch_html(url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")

    def meta_content(*names):
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    title = meta_content("og:title", "twitter:title", "title") or (soup.title.string.strip() if soup.title else "")
    description = meta_content("og:description", "twitter:description", "description")
    image = meta_content("og:image", "twitter:image")
    author = meta_content("article:author", "author", "dc.creator", "itemprop", "name")
    published = meta_content("article:published_time", "datePublished")
    modified = meta_content("article:modified_time", "dateModified")

    if not published:
        tag = soup.find(attrs={"itemprop": "datePublished"})
        if tag and tag.get("content"):
            published = tag["content"]

    if not modified:
        tag = soup.find(attrs={"itemprop": "dateModified"})
        if tag and tag.get("content"):
            modified = tag["content"]

    def normalize_date(value):
        if not value:
            return None
        try:
            dt = dateparser.parse(value)
            return dt.isoformat()
        except Exception:
            return None

    return {
        "url": url,
        "title": title,
        "description": description,
        "image": image,
        "author": author,
        "published": normalize_date(published),
        "modified": normalize_date(modified),
    }


# ---------------------------------------------------------
# Test manuale
# ---------------------------------------------------------
if __name__ == "__main__":
    test_url = "https://www.artbooms.com/blog/vivian-suter-palais-tokyo-parigi"
    art = parse_article(test_url)
    for k, v in art.items():
        print(f"{k:>12}: {v}")
