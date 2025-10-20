import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger("article_parser")

# ---------------------------------------------------------------------
# Parsing date tipo "Jul 29, 2025" → datetime (UTC)
# ---------------------------------------------------------------------
MONTHS_EN_SHORT = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
}

def _parse_date(s: str):
    if not s:
        return None
    s = s.strip()
    try:
        dt = dateparser.parse(s)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
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
# Fetch HTML
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
# Helper meta
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
# Estrazione link ordinati (fix date + before/after)
# ---------------------------------------------------------------------
def extract_article_links_from_archive_html(html: str, base_url: str):
    """
    Estrae tutti gli articoli dall'archivio, ordinandoli dal più vecchio al più recente.
    In caso di parità di data, mantiene l'ordine di apparizione nel file HTML.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.select("li.archive-item.archive-item--show-date")

    links_with_dates = []
    seen = set()

    for idx, item in enumerate(items):
        # Cerca la data anche nei tag "before"/"after"
        date_tag = (
            item.find("span", class_=re.compile(r"archive-item-date"))
            or item.find("span", class_=re.compile(r"archive-item-date-before"))
            or item.find("span", class_=re.compile(r"archive-item-date-after"))
        )
        link_tag = item.find("a", href=True)
        if not date_tag or not link_tag:
            continue

        date_text = date_tag.get_text(strip=True)
        dt = _parse_date(date_text)
        if not dt:
            continue

        href = link_tag["href"].strip()
        abs_url = urljoin(base_url, href)
        if "/blog/" not in abs_url or "/tag/" in abs_url or "?" in abs_url:
            continue

        abs_url = abs_url.replace("http://", "https://").rstrip("/")
        if abs_url in seen:
            continue
        seen.add(abs_url)

        if dt.year < 2016:
            continue

        links_with_dates.append((dt, idx, abs_url))

    # Ordina per data e posizione originale
    links_with_dates.sort(key=lambda x: (x[0], x[1]))
    links = [u for _, _, u in links_with_dates]

    if links:
        logger.info("Trovati %s articoli nell’archivio.", len(links))
        logger.info("Primo articolo: %s — Ultimo: %s", links[0], links[-1])
    else:
        logger.warning("Nessun articolo trovato nell’archivio.")
    return links

# ---------------------------------------------------------------------
# Parsing singolo articolo
# ---------------------------------------------------------------------
def parse_article(url, html=None, session=None):
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

    title = _first_meta(soup, [{"itemprop": "name"}])
    description = _first_meta(soup, [{"itemprop": "description"}])
    author = _first_meta(soup, [{"itemprop": "author"}])
    published = _first_meta(soup, [{"itemprop": "datePublished"}])
    modified = _first_meta(soup, [{"itemprop": "dateModified"}])
    image_url = _first_meta(soup, [{"itemprop": "thumbnailUrl"}])
    canonical = _first_meta(soup, [{"itemprop": "url"}])

    if not canonical:
        link_tag = soup.find("link", rel="canonical")
        if link_tag and link_tag.get("href"):
            canonical = link_tag["href"].strip()

    pub_dt = _parse_date(published)
    mod_dt = _parse_date(modified) or pub_dt

    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=re.compile(r"(post|entry|article|content|text|sqs-block-content)", re.I))
    )
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    logger.info("Parsed articolo: %s — titolo: %s", url, title)

    return {
        "url": (canonical or url).replace("http://", "https://").rstrip("/"),
        "title": title,
        "description": description or (content_text[:250] if content_text else ""),
        "author": author,
        "published": pub_dt.isoformat() if pub_dt else None,
        "modified": mod_dt.isoformat() if mod_dt else None,
        "content_text": content_text,
        "image": image_url,
    }
