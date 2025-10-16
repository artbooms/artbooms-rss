import re
import logging
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dateutil import parser as dateparser

logger = logging.getLogger("article_parser")

# 🔤 Mesi italiani → inglesi per conversione date
MONTHS_IT = {
    "gennaio": "January", "febbraio": "February", "marzo": "March", "aprile": "April",
    "maggio": "May", "giugno": "June", "luglio": "July", "agosto": "August",
    "settembre": "September", "ottobre": "October", "novembre": "November", "dicembre": "December",
    "gen": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "mag": "May", "giu": "Jun",
    "lug": "Jul", "ago": "Aug", "set": "Sep", "ott": "Oct", "nov": "Nov", "dic": "Dec"
}

BASE_URL = "https://www.artbooms.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

# ------------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------------

def fetch_html(url, session=None, timeout=20):
    """Scarica HTML con headers e https normalizzato."""
    if url.startswith("http://"):
        url = url.replace("http://", "https://")
    s = session or requests.Session()
    r = s.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _parse_date_any(value):
    """Converte una stringa di data in datetime (con supporto mesi italiani)."""
    if not value:
        return None
    s = value.strip()
    for it, en in MONTHS_IT.items():
        s = re.sub(rf"\b{re.escape(it)}\b", en, s, flags=re.IGNORECASE)
    try:
        dt = dateparser.parse(s)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _norm_url(href):
    """Normalizza URL e filtra solo articoli validi."""
    if not href:
        return None
    if href.startswith("/"):
        href = urljoin(BASE_URL, href)
    if not href.startswith("https://"):
        return None
    if "/blog/" not in href:
        return None
    if "/tag/" in href or "?month=" in href:
        return None
    return href.rstrip("/")


# ------------------------------------------------------------------------
# Estrazione link archivio
# ------------------------------------------------------------------------

def extract_article_links_from_archive_html(html, base_url=BASE_URL):
    """Estrae tutti i link /blog/... validi dalla pagina archivio."""
    soup = BeautifulSoup(html, "html.parser")
    seen, urls = set(), []
    for a in soup.find_all("a", href=True):
        link = _norm_url(a["href"])
        if not link:
            continue
        if link not in seen:
            seen.add(link)
            urls.append(link)
    logger.info("[Parser] %s link articolo validi trovati nell'archivio.", len(urls))
    return urls


# ------------------------------------------------------------------------
# Estrazione dati da un singolo articolo
# ------------------------------------------------------------------------

def parse_article(url, html=None, session=None):
    """
    Estrae solo i meta SEO Squarespace:
      - titolo, descrizione, autore, date, immagine
    Esclude qualsiasi blocco corpo o contenuti social.
    """
    try:
        if html is None:
            html = fetch_html(url, session=session)
    except Exception as e:
        logger.exception("fetch_html failed for %s: %s", url, e)
        return {
            "url": url, "title": None, "description": None, "author": None,
            "published": None, "modified": None, "content_text": "", "image": None
        }

    soup = BeautifulSoup(html, "lxml")

    def meta(name=None, prop=None, item=None):
        if name:
            tag = soup.find("meta", attrs={"name": name})
        elif prop:
            tag = soup.find("meta", attrs={"property": prop})
        elif item:
            tag = soup.find("meta", attrs={"itemprop": item})
        else:
            tag = None
        return tag.get("content").strip() if tag and tag.get("content") else None

    # Titolo (preferisce meta itemprop=headline)
    title = meta(item="headline") or meta(prop="og:title")
    if title:
        title = title.replace("— ARTBOOMS", "").replace("—ARTBOOMS", "").strip()

    # Descrizione (solo meta SEO ufficiali)
    description = meta(prop="og:description") or meta(item="description")

    # Autore
    author = meta(item="author")

    # Date (pubblicazione e modifica)
    published = meta(item="datePublished")
    modified = meta(item="dateModified")

    pub_dt = _parse_date_any(published)
    mod_dt = _parse_date_any(modified) or pub_dt

    # Immagine principale
    image = (
        meta(prop="og:image")
        or meta(item="image")
        or meta(item="thumbnailUrl")
        or meta(prop="og:image:url")
    )
    if image and image.startswith("http://"):
        image = image.replace("http://", "https://")

    # Canonical (per sicurezza)
    canonical = meta(prop="og:url") or meta(item="url") or url
    if canonical.startswith("http://"):
        canonical = canonical.replace("http://", "https://")

    # Nessun corpo: Squarespace già fornisce le meta complete
    content_text = ""

    return {
        "url": canonical,
        "title": title or url,
        "description": description,
        "author": author,
        "published": pub_dt.isoformat() if pub_dt else None,
        "modified": mod_dt.isoformat() if mod_dt else None,
        "content_text": content_text,
        "image": image,
    }

