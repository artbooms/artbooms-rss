import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import requests
import logging

logger = logging.getLogger("article_parser")

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
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _first_meta(soup, attrs_list):
    for attrs in attrs_list:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            val = tag.get("content") or tag.get("value")
            if val:
                return val.strip()
    return None

def extract_article_links_from_archive_html(html: str, base_url: str):
    """Estrae SOLO i link di articoli /blog/... (nessuna pagina, tag, month, ecc.)."""
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.find_all("a", href=True)
    urls, seen = [], set()
    for a in anchors:
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:")):
            continue
        abs_url = urljoin(base_url, href)
        u = urlparse(abs_url)
        if not u.netloc.endswith(urlparse(base_url).netloc):
            continue
        path = u.path or "/"
        # 🔒 accetta solo veri articoli
        if not path.startswith("/blog/"):
            continue
        if any(x in abs_url for x in ["/tag/", "/category/", "?month=", "#", "?"]):
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            urls.append(abs_url)
    logger.info("[Parser] %s link articolo validi trovati nell'archivio.", len(urls))
    return urls

def fetch_html(url, session=None, timeout=15):
    s = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    }
    r = s.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_article(url, html=None, session=None):
    """Estrae titolo, descrizione, autore, date e immagine dall’articolo."""
    try:
        if html is None:
            html = fetch_html(url, session=session)
    except Exception as e:
        logger.exception("fetch_html failed for %s: %s", url, e)
        return {"url": url, "title": None, "description": None, "author": None,
                "published": None, "modified": None, "content_text": "", "image": None}

    soup = BeautifulSoup(html, "lxml")

    title = _first_meta(soup, [{"property":"og:title"}, {"name":"title"}, {"name":"twitter:title"}])
    if not title:
        ttag = soup.find("title")
        title = ttag.get_text(strip=True) if ttag else None
    if title:
        title = title.replace("— ARTBOOMS", "").replace(" — ARTBOOMS", "").strip()

    description = _first_meta(soup, [
        {"property":"og:description"}, {"name":"description"}, {"name":"twitter:description"},
        {"itemprop":"description"}
    ])

    author = _first_meta(soup, [
        {"name":"author"}, {"property":"article:author"}, {"name":"article:author"},
        {"itemprop":"author"}
    ])
    if not author:
        a = soup.find("a", rel="author")
        if a:
            author = a.get_text(strip=True)

    pub = _first_meta(soup, [{"property":"article:published_time"}, {"name":"pubdate"},
                             {"itemprop":"datePublished"}, {"name":"date"}])
    mod = _first_meta(soup, [{"property":"article:modified_time"}, {"itemprop":"dateModified"}, {"name":"last-modified"}])

    if not pub:
        time_tag = soup.find("time")
        if time_tag:
            pub = time_tag.get("datetime") or time_tag.get_text(strip=True)

    pub_dt = _parse_date(pub)
    mod_dt = _parse_date(mod) or pub_dt

    main = soup.find("article") or soup.find("main") or soup.find(class_=re.compile(r"post|entry|article|content|sqs-block-content", re.I))
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    image_url = _first_meta(soup, [{"property":"og:image"}, {"name":"twitter:image"}, {"itemprop":"image"}])
    if not image_url:
        link_img = soup.find("link", rel="image_src")
        if link_img and link_img.get("href"):
            image_url = link_img.get("href")
    if image_url and image_url.startswith("http://"):
        image_url = image_url.replace("http://", "https://")

    return {
        "url": url,
        "title": title or url,
        "description": description or (content_text[:280] if content_text else None),
        "author": author,
        "published": pub_dt.isoformat() if pub_dt else None,
        "modified": mod_dt.isoformat() if mod_dt else None,
        "content_text": content_text or "",
        "image": image_url
    }
