import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import requests
import logging
from datetime import timezone

logger = logging.getLogger("article_parser")

HEADERS = {
    "User-Agent": "artbooms-rss-bot/1.0 (+https://www.artbooms.com)"
}

# =========================
# HTTP
# =========================
def fetch_html(url, session=None, timeout=20):
    s = session or requests.Session()
    r = s.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

# =========================
# Archivio: estrazione link + data
# =========================
DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b"
)

def extract_article_links_from_archive_html(html: str, base_url: str):
    """
    Estrae i link agli articoli dall'archivio, cercando anche la data
    nel formato 'Feb 10, 2016'. Restituisce una lista di URL
    ordinati cronologicamente (dal più vecchio al più nuovo).
    """
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.find_all("a", href=True)
    seen = set()
    items = []

    for a in anchors:
        href = a["href"].strip()
        if not href or href.startswith("javascript:") or href.startswith("mailto:"):
            continue

        abs_url = urljoin(base_url, href)
        if urlparse(abs_url).netloc != urlparse(base_url).netloc:
            continue
        if "/tag/" in abs_url or "?" in abs_url or not "/blog/" in abs_url:
            continue
        if abs_url.endswith("-") or abs_url in seen:
            continue

        # 🔍 cerca data nel testo vicino al link
        context = a.get_text(" ", strip=True)
        date_str = None

        # Prova nel testo stesso
        m = DATE_RE.search(context)
        if not m:
            # oppure nei genitori vicini
            parent_txt = a.find_parent().get_text(" ", strip=True) if a.find_parent() else ""
            m = DATE_RE.search(parent_txt)
        if m:
            date_str = m.group(0)

        if not date_str:
            continue  # ignora link senza data (evita errori)

        try:
            dt = dateparser.parse(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        items.append((abs_url, dt))
        seen.add(abs_url)

    # ordina cronologicamente (vecchi → nuovi)
    items.sort(key=lambda x: x[1])

    return [u for u, _ in items]

# =========================
# Parser pagina articolo
# =========================
def _first_meta(soup, attrs_list):
    for attrs in attrs_list:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            val = tag.get("content") or tag.get("value")
            if val:
                return val.strip()
    return None

def parse_article(url, html=None, session=None):
    """
    Legge un singolo articolo e restituisce:
    url (canonico), title, description, author, published, modified, content_text, image
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

    # 📍 canonical link (vero indirizzo dell’articolo)
    canonical = None
    link_tag = soup.find("link", rel="canonical")
    if link_tag and link_tag.get("href"):
        canonical = link_tag["href"].strip()
    if not canonical:
        canonical = url

    # 📰 metadati
    title = _first_meta(soup, [
        {"property": "og:title"}, {"name": "title"}, {"name": "twitter:title"}, {"itemprop": "headline"}
    ])
    if not title:
        ttag = soup.find("title")
        title = ttag.get_text(strip=True) if ttag else None

    description = _first_meta(soup, [
        {"property": "og:description"}, {"name": "description"}, {"name": "twitter:description"},
        {"itemprop": "description"}
    ])

    author = _first_meta(soup, [
        {"name": "author"}, {"property": "article:author"}, {"itemprop": "author"}
    ])
    if not author:
        a = soup.find("a", rel="author")
        if a:
            author = a.get_text(strip=True)

    pub = _first_meta(soup, [
        {"property": "article:published_time"}, {"itemprop": "datePublished"}
    ])
    mod = _first_meta(soup, [
        {"property": "article:modified_time"}, {"itemprop": "dateModified"}
    ])

    if not pub:
        time_tag = soup.find("time")
        if time_tag:
            pub = time_tag.get("datetime") or time_tag.get_text(strip=True)

    def _to_iso(s):
        if not s:
            return None
        try:
            dt = dateparser.parse(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            return None

    published = _to_iso(pub)
    modified = _to_iso(mod) or published

    main = soup.find("article") or soup.find("main") or soup.find(
        class_=re.compile(r"post|entry|article|content|sqs-block-content", re.I))
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    image_url = _first_meta(soup, [{"property": "og:image"}, {"name": "twitter:image"}, {"itemprop": "image"}])
    if not image_url:
        link_img = soup.find("link", rel="image_src")
        if link_img and link_img.get("href"):
            image_url = link_img.get("href")

    return {
        "url": canonical,  # ✅ canonical definitivo
        "title": title or canonical,
        "description": description or (content_text[:280] if content_text else None),
        "author": author,
        "published": published,
        "modified": modified,
        "content_text": content_text or "",
        "image": image_url
    }
