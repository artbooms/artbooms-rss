import re
from urllib.parse import urljoin, urlparse
from datetime import timezone
import logging
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

logger = logging.getLogger("article_parser")

# User-Agent identificativo
HEADERS = {
    "User-Agent": "artbooms-rss-bot/1.0 (+https://www.artbooms.com)"
}

# ------------------------------------------------------------
# Funzioni di supporto
# ------------------------------------------------------------
def fetch_html(url: str, session: requests.Session | None = None, timeout: int = 20) -> str:
    """Scarica HTML da un URL."""
    s = session or requests.Session()
    r = s.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

def _parse_abbr_en_date(s: str):
    """Parsa date tipo 'Jun 24, 2025' → datetime (UTC)."""
    if not s:
        return None
    s = s.strip()
    try:
        dt = dateparser.parse(s)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _first_meta(soup: BeautifulSoup, attrs_list: list[dict]) -> str | None:
    """Trova il primo meta che corrisponde agli attributi dati."""
    for attrs in attrs_list:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            val = tag.get("content") or tag.get("value")
            if val:
                return val.strip()
    return None

def _to_iso(s: str | None) -> str | None:
    """Converte date in stringhe ISO."""
    if not s:
        return None
    try:
        dt = dateparser.parse(s)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None

# ------------------------------------------------------------
# Estrazione link dall'archivio Artbooms
# ------------------------------------------------------------
def extract_article_links_from_archive_html(html: str, base_url: str) -> list[str]:
    """
    Legge la pagina archivio Artbooms e restituisce la lista ordinata dei link agli articoli
    (dal più vecchio al più recente), seguendo la data visibile in <span class="archive-item-date-before">
    """
    soup = BeautifulSoup(html, "lxml")
    items = []
    seen = set()

    for li in soup.select("li.archive-item.archive-item--show-date"):
        # leggi la data
        date_el = li.select_one("span.archive-item-date-before") or li.select_one("span.archive-item-date-after")
        if not date_el:
            continue
        date_str = date_el.get_text(strip=True)
        dt = _parse_abbr_en_date(date_str)
        if not dt:
            continue  # se non riesce a interpretare la data, salta

        # leggi il link
        a = li.select_one("a.archive-item-link[href]")
        if not a:
            continue
        href = a["href"].strip()
        abs_url = urljoin(base_url, href)

        # accetta solo link HTTPS validi con /blog/
        if not abs_url.startswith("https://"):
            abs_url = "https://" + abs_url.lstrip("http://")
        if "/blog/" not in abs_url or abs_url in seen:
            continue

        seen.add(abs_url)
        items.append((abs_url, dt))

    # ordina per data crescente (vecchi → nuovi)
    items.sort(key=lambda x: x[1])
    return [url for url, _ in items]

# ------------------------------------------------------------
# Parsing dei singoli articoli
# ------------------------------------------------------------
def parse_article(url: str, html: str | None = None, session: requests.Session | None = None) -> dict:
    """
    Restituisce:
      url, title, description, author, published, modified, content_text, image
    usa principalmente i meta itemprop:
      - itemprop="name" / "headline"
      - itemprop="url"
      - itemprop="description"
      - itemprop="thumbnailUrl" / "image"
      - itemprop="author"
      - itemprop="datePublished"
      - itemprop="dateModified"
    """
    s = session or requests.Session()
    try:
        if html is None:
            html = fetch_html(url, session=s)
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
            "image": None
        }

    soup = BeautifulSoup(html, "lxml")

    # URL canonico
    canonical = None
    ln = soup.find("link", rel="canonical")
    if ln and ln.get("href"):
        canonical = ln["href"].strip()
    if not canonical:
        canonical = _first_meta(soup, [{"itemprop": "url"}]) or url

    # Titolo
    title = _first_meta(soup, [{"itemprop": "name"}, {"itemprop": "headline"}])
    if not title:
        title = _first_meta(soup, [{"property": "og:title"}])
        if not title:
            ttag = soup.find("title")
            title = ttag.get_text(strip=True) if ttag else canonical

    # Descrizione
    description = _first_meta(soup, [{"itemprop": "description"}])
    if not description:
        description = _first_meta(soup, [{"property": "og:description"}])
        if not description:
            description = None

    # Immagine
    image_url = _first_meta(soup, [{"itemprop": "thumbnailUrl"}, {"itemprop": "image"}])
    if not image_url:
        image_url = _first_meta(soup, [{"property": "og:image"}])
        if not image_url:
            link_img = soup.find("link", rel="image_src")
            if link_img and link_img.get("href"):
                image_url = link_img["href"]

    # Autore
    author = _first_meta(soup, [{"itemprop": "author"}])
    if not author:
        author = _first_meta(soup, [{"name": "author"}])

    # Date
    pub = _first_meta(soup, [{"itemprop": "datePublished"}])
    mod = _first_meta(soup, [{"itemprop": "dateModified"}])
    published = _to_iso(pub)
    modified = _to_iso(mod) or published

    # Contenuto testuale (fallback)
    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=re.compile(r"(post|entry|article|content|sqs-block-content)", re.I))
    )
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    return {
        "url": canonical,
        "title": title or canonical,
        "description": (description or (content_text[:240] if content_text else None)),
        "author": author,
        "published": published,
        "modified": modified,
        "content_text": content_text or "",
        "image": image_url
    }
