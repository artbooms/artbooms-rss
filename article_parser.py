import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger("article_parser")

# ---------------------------------------------------------------------
# Parsing date "Mon DD, YYYY" (anno, mese, giorno) con fallback robusto
# ---------------------------------------------------------------------
MONTHS_EN_SHORT = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
}

def _parse_date(s: str):
    """Parsa 'Jun 24, 2025' o ISO; restituisce datetime con tz UTC, oppure None."""
    if not s:
        return None
    s = s.strip()
    # 1) prova con dateutil
    try:
        dt = dateparser.parse(s)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
    # 2) fallback su pattern testuale "Mon DD, YYYY"
    m = re.match(r"^([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4})$", s)
    if m:
        mon, day, year = m.groups()
        mon_num = MONTHS_EN_SHORT.get(mon.capitalize())
        if mon_num:
            try:
                return datetime(int(year), mon_num, int(day), tzinfo=timezone.utc)
            except Exception:
                return None
    return None

# ---------------------------------------------------------------------
# Fetch HTML (user-agent “neutro”)
# ---------------------------------------------------------------------
def fetch_html(url, session=None, timeout=20):
    s = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }
    r = s.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text

# ---------------------------------------------------------------------
# Helper: primo <meta ... content="..."> che combacia con attrs della lista
# ---------------------------------------------------------------------
def _first_meta(soup, attrs_list):
    for attrs in attrs_list:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            val = tag.get("content") or tag.get("value")
            if val:
                return val.strip()
    return None

# ---------------------------------------------------------------------
# Estrazione link dall’archivio (ordine cronologico: vecchi → nuovi)
# ---------------------------------------------------------------------
def extract_article_links_from_archive_html(html: str, base_url: str):
    """
    Dalla pagina archivio, estrae <li class="archive-item archive-item--show-date">
    e costruisce (data, url) in base al testo data (es. 'Feb 10, 2016').
    Filtra solo URL '/blog/...', esclude '/tag/' e query '?month='.
    Ordina per (anno, mese, giorno) crescente: dal più vecchio al più nuovo.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.select("li.archive-item.archive-item--show-date")

    links_with_dates = []
    seen = set()

    for item in items:
        # data: prendi il primo span con classe compatibile
        date_tag = item.find("span", class_=re.compile(r"archive-item-date"))
        link_tag = item.find("a", href=True)
        if not date_tag or not link_tag:
            continue

        date_text = date_tag.get_text(strip=True)
        dt = _parse_date(date_text)
        if not dt:
            continue

        # URL assoluto
        href = link_tag["href"].strip()
        abs_url = urljoin(base_url, href)

        # Filtri: solo /blog/, niente /tag/ e niente query (?...)
        if "/blog/" not in abs_url:
            continue
        if "/tag/" in abs_url:
            continue
        if "?" in abs_url:
            # esclude categorie mensili tipo ?month=...
            continue

        # normalizza https, rimuovi trailing slash
        abs_url = abs_url.replace("http://", "https://").rstrip("/")

        if abs_url in seen:
            continue
        seen.add(abs_url)

        # opzionale: limita agli anni >= 2016 (come da tuo archivio)
        if dt.year < 2016:
            continue

        links_with_dates.append((dt, abs_url))

    # Ordina: (anno, mese, giorno) crescente
    links_with_dates.sort(key=lambda x: x[0])
    links = [u for _, u in links_with_dates]

    logger.info("Trovati %s articoli nell’archivio.", len(links))
    if links:
        logger.info("Primo articolo: %s — Ultimo: %s", links[0], links[-1])
    else:
        logger.warning("Nessun articolo trovato nell’archivio.")

    return links

# ---------------------------------------------------------------------
# Parsing di ogni articolo (usa SOLO itemprop che hai indicato)
# ---------------------------------------------------------------------
def parse_article(url, html=None, session=None):
    """
    Ritorna un dict con chiavi:
      url, title, description, author, published, modified, content_text, image
    NB: 'author' sarà emesso come <dc:creator> dal generatore RSS.
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

    # Metadati strutturati (Squarespace)
    title = _first_meta(soup, [{"itemprop": "name"}])
    description = _first_meta(soup, [{"itemprop": "description"}])
    author = _first_meta(soup, [{"itemprop": "author"}])
    published = _first_meta(soup, [{"itemprop": "datePublished"}])
    modified = _first_meta(soup, [{"itemprop": "dateModified"}])
    image_url = _first_meta(soup, [{"itemprop": "thumbnailUrl"}])
    canonical = _first_meta(soup, [{"itemprop": "url"}])

    # Fallback minimi
    if not title:
        ttag = soup.find("title")
        title = ttag.get_text(strip=True) if ttag else url

    pub_dt = _parse_date(published)
    mod_dt = _parse_date(modified) or pub_dt

    # Testo principale (fallback ragionevole)
    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=re.compile(r"(post|entry|article|content|text|sqs-block-content)", re.I))
    )
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    return {
        "url": (canonical or url).replace("http://", "https://").rstrip("/"),
        "title": title,
        "description": description or (content_text[:250] if content_text else ""),
        "author": author,  # ← il feed lo renderà come <dc:creator>
        "published": pub_dt.isoformat() if pub_dt else None,
        "modified": mod_dt.isoformat() if mod_dt else None,
        "content_text": content_text,
        "image": image_url,
    }
