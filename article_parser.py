import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging

logger = logging.getLogger("article_parser")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

HEADERS = {"User-Agent": USER_AGENT}


# ---------------------------------------------------------
# Scarica una pagina HTML con gestione errori
# ---------------------------------------------------------
def fetch_html(url):
    """Scarica HTML di una pagina e ritorna il testo."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error("Errore fetch %s: %s", url, e)
        return ""


# ---------------------------------------------------------
# Estrae link validi dagli archivi di Squarespace
# ---------------------------------------------------------
def extract_article_links_from_archive_html(html, base_url):
    """
    Estrae tutti i link a singoli articoli dal codice HTML dell'archivio.
    Corregge il dominio per evitare percorsi come /archivio-completo/blog/...
    """
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Escludiamo categorie, tag e query (?month)
        if "/blog/" in href and not any(x in href for x in ["?month", "/tag/", "/category", "feed"]):
            # ✅ Correzione: forza dominio principale
            links.add(urljoin("https://www.artbooms.com", href.split("?")[0]))

    return sorted(links)


# ---------------------------------------------------------
# Parsing di un singolo articolo Squarespace
# ---------------------------------------------------------
def parse_article(url):
    """
    Estrae i metadati principali da un articolo Squarespace:
    titolo, descrizione, autore, date, immagine.
    """
    html = fetch_html(url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")

    meta = {
        "url": url,
        "title": None,
        "description": None,
        "author": None,
        "published": None,
        "modified": None,
        "image": None,
    }

    def _get(name=None, prop=None, itemprop=None):
        sel = []
        if name:
            sel += soup.find_all("meta", attrs={"name": name})
        if prop:
            sel += soup.find_all("meta", attrs={"property": prop})
        if itemprop:
            sel += soup.find_all("meta", attrs={"itemprop": itemprop})
        for tag in sel:
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    # Titolo
    meta["title"] = (
        _get("og:title")
        or _get("twitter:title")
        or _get(itemprop="headline")
        or (soup.title.string.strip() if soup.title else None)
    )

    # Descrizione
    meta["description"] = (
        _get("og:description")
        or _get("description")
        or _get(itemprop="description")
    )

    # Autore (Squarespace usa itemprop)
    meta["author"] = (
        _get(itemprop="author")
        or _get("author")
        or _get(prop="article:author")
    )

    # Date (ISO8601 preferite)
    meta["published"] = (
        _get(itemprop="datePublished")
        or _get(prop="article:published_time")
    )
    meta["modified"] = (
        _get(itemprop="dateModified")
        or _get(prop="article:modified_time")
    )

    # Immagine principale
    meta["image"] = (
        _get("og:image")
        or _get("twitter:image")
        or _get(itemprop="image")
    )

    # Fallback: titolo e descrizione dal corpo
    if not meta["title"]:
        h1 = soup.find("h1")
        if h1:
            meta["title"] = h1.get_text(strip=True)
    if not meta["description"]:
        p = soup.find("p")
        if p:
            meta["description"] = p.get_text(strip=True)[:200] + "…"

    return meta
