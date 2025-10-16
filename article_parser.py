import re
import logging
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dateutil import parser as dateparser

logger = logging.getLogger("article_parser")

# ------------------------------------------------------------------------
# Configurazione
# ------------------------------------------------------------------------

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
# Funzione mancante (fix ImportError)
# ------------------------------------------------------------------------

def fetch_html(url, session=None, timeout=25):
    """Scarica l'HTML di una pagina con headers e HTTPS forzato."""
    if url.startswith("http://"):
        url = url.replace("http://", "https://")
    s = session or requests.Session()
    r = s.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

# ------------------------------------------------------------------------
# Utility interne
# ------------------------------------------------------------------------

def _parse_date_any(value):
    """Converte stringa di data (es. 'Feb 10, 2016') in datetime."""
    if not value:
        return None
    try:
        dt = dateparser.parse(value)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _norm_url(href):
    """Normalizza e filtra solo gli URL validi degli articoli."""
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
# Estrazione link dall’archivio completo
# ------------------------------------------------------------------------

def extract_article_links_from_archive_html(html, base_url=BASE_URL):
    """
    Estrae i link degli articoli dall'archivio completo di Artbooms,
    riconoscendo le date nel formato 'Mon DD, YYYY' e scartando i titoli
    di mese/anno tipo 'August 2017'. Restituisce i link ordinati dal più
    vecchio al più nuovo.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Cerca i blocchi di testo che contengono date tipo "Feb 10, 2016"
    possible_dates = soup.find_all(
        string=re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b")
    )

    items = []
    seen = set()

    for date_text in possible_dates:
        date_str = date_text.strip()

        # Ignora "August 2017" o simili (solo mese e anno)
        if re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}$", date_str):
            continue

        dt = _parse_date_any(date_str)
        if not dt:
            continue

        parent = date_text.find_parent()
        next_link = None

        # Cerca il link /blog/ più vicino dopo la data
        for sib in parent.find_all_next("a", href=True):
            href = _norm_url(sib["href"])
            if href:
                next_link = href
                break

        if next_link and next_link not in seen:
            seen.add(next_link)
            items.append({"date": dt, "url": next_link})
            logger.info("[DEBUG] Collegata data %s → %s", date_str, next_link)

    items.sort(key=lambda x: x["date"])
    ordered_links = [i["url"] for i in items]
    logger.info("[Parser] %d articoli trovati e ordinati cronologicamente.", len(ordered_links))
    return ordered_links

# ------------------------------------------------------------------------
# Estrazione meta SEO da singolo articolo
# ------------------------------------------------------------------------

def parse_article(url, html=None, session=None):
    """
    Estrae i meta SEO Squarespace:
      - titolo
      - descrizione
      - autore
      - date (pubblicazione, modifica)
      - immagine principale
    """
    try:
        if html is None:
            html = fetch_html(url, session=session)
    except Exception as e:
        logger.exception("fetch_html failed for %s: %s", url, e)
        return {
            "url": url,
            "title": None,
            "description": None,
            "author": None,
            "published": None,
            "modified": None,
            "content_text": "",
            "image": None,
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

    # Titolo e descrizione
    title = meta(item="headline") or meta(prop="og:title")
    if title:
        title = title.replace("— ARTBOOMS", "").replace("—ARTBOOMS", "").strip()

    description = meta(prop="og:description") or meta(item="description")

    # Autore
    author = meta(item="author")

    # Date
    published = meta(item="datePublished")
    modified = meta(item="dateModified")

    pub_dt = _parse_date_any(published)
    mod_dt = _parse_date_any(modified) or pub_dt

    # Immagine
    image = (
        meta(prop="og:image")
        or meta(item="image")
        or meta(item="thumbnailUrl")
        or meta(prop="og:image:url")
    )
    if image and image.startswith("http://"):
        image = image.replace("http://", "https://")

    # Canonical URL
    canonical = meta(prop="og:url") or meta(item="url") or url
    if canonical.startswith("http://"):
        canonical = canonical.replace("http://", "https://")

    return {
        "url": canonical,
        "title": title or url,
        "description": description,
        "author": author,
        "published": pub_dt.isoformat() if pub_dt else None,
        "modified": mod_dt.isoformat() if mod_dt else None,
        "content_text": "",
        "image": image,
    }
