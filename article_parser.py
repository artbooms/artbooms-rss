import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger("article_parser")

# ---------------------------------------------------------------------
# Conversione mesi italiani (per sicurezza)
# ---------------------------------------------------------------------
MONTHS_IT = {
    "gennaio":"January","febbraio":"February","marzo":"March","aprile":"April",
    "maggio":"May","giugno":"June","luglio":"July","agosto":"August",
    "settembre":"September","ottobre":"October","novembre":"November","dicembre":"December",
    "gen":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","mag":"May","giu":"Jun",
    "lug":"Jul","ago":"Aug","set":"Sep","ott":"Oct","nov":"Nov","dic":"Dec"
}

def _normalizza_date_str(s: str):
    if not s:
        return s
    s = s.strip()
    for it, en in MONTHS_IT.items():
        s = re.sub(r'\b' + re.escape(it) + r'\b', en, s, flags=re.IGNORECASE)
    return s

def _parse_date(s):
    if not s:
        return None
    s = _normalizza_date_str(s)
    try:
        dt = dateparser.parse(s)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _first_meta(soup, attrs_list):
    """Trova il primo meta valido nella lista."""
    for attrs in attrs_list:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            val = tag.get("content") or tag.get("value")
            if val:
                return val.strip()
    return None

# ---------------------------------------------------------------------
# Estrazione link dall’archivio
# ---------------------------------------------------------------------
def extract_article_links_from_archive_html(html: str, base_url: str):
    """
    Estrae i link degli articoli dalla pagina archivio.
    Rileva i tag <li class="archive-item"> e li ordina per data (anno, mese, giorno).
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.select("li.archive-item")

    links_with_dates = []
    for item in items:
        link_tag = item.find("a", href=True)
        date_tag = item.find("span", class_=re.compile("archive-item-date"))
        if not link_tag or not date_tag:
            continue

        date_text = date_tag.get_text(strip=True)
        dt = _parse_date(date_text)
        if not dt:
            continue

        abs_url = urljoin(base_url, link_tag["href"].strip())
        links_with_dates.append((dt, abs_url))

    # Ordina per data crescente (dal più vecchio al più recente)
    links_with_dates.sort(key=lambda x: x[0])

    links = [u for _, u in links_with_dates]
    logger.info("Trovati %s articoli nell’archivio.", len(links))

    # 👇 stampa anche un piccolo riepilogo per debug
    if links:
        logger.info("Primo articolo: %s — Ultimo: %s", links[0], links[-1])
    else:
        logger.warning("Nessun articolo trovato nell’archivio.")

    return links

# ---------------------------------------------------------------------
# Fetch HTML
# ---------------------------------------------------------------------
def fetch_html(url, session=None, timeout=15):
    s = session or requests.Session()
    headers = {"User-Agent": "artbooms-rss-bot/1.0 (+https://www.artbooms.com)"}
    r = s.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text

# ---------------------------------------------------------------------
# Parsing dettagliato di ogni articolo
# ---------------------------------------------------------------------
def parse_article(url, html=None, session=None):
    try:
        if html is None:
            html = fetch_html(url, session=session)
    except Exception as e:
        logger.exception("fetch_html failed for %s: %s", url, e)
        return {"url": url, "title": None, "description": None, "author": None,
                "published": None, "modified": None, "content_text": "", "image": None}

    soup = BeautifulSoup(html, "lxml")

    title = _first_meta(soup, [{"itemprop": "name"}, {"property": "og:title"}, {"name": "title"}])
    if not title:
        ttag = soup.find("title")
        title = ttag.get_text(strip=True) if ttag else url

    description = _first_meta(soup, [{"itemprop": "description"}, {"property": "og:description"}])
    author = _first_meta(soup, [{"itemprop": "author"}, {"name": "author"}])
    published = _first_meta(soup, [{"itemprop": "datePublished"}])
    modified = _first_meta(soup, [{"itemprop": "dateModified"}])
    image_url = _first_meta(soup, [{"itemprop": "thumbnailUrl"}, {"property": "og:image"}])
    canonical = _first_meta(soup, [{"itemprop": "url"}])

    pub_dt = _parse_date(published)
    mod_dt = _parse_date(modified) or pub_dt

    main = soup.find("article") or soup.find("main") or soup.find(class_=re.compile(r"(post|entry|article|content)", re.I))
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    return {
        "url": canonical or url,
        "title": title,
        "description": description or content_text[:250],
        "author": author,
        "published": pub_dt.isoformat() if pub_dt else None,
        "modified": mod_dt.isoformat() if mod_dt else None,
        "content_text": content_text,
        "image": image_url
    }
