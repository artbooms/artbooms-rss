import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

MONTHS_IT = {
    "gennaio":"January","febbraio":"February","marzo":"March","aprile":"April",
    "maggio":"May","giugno":"June","luglio":"July","agosto":"August",
    "settembre":"September","ottobre":"October","novembre":"November","dicembre":"December",
    "gen":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","mag":"May","giu":"Jun",
    "lug":"Jul","ago":"Aug","set":"Sep","ott":"Oct","nov":"Nov","dic":"Dec"
}

DATE_RX = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|" + 
    "|".join(MONTHS_IT.keys()) + 
    r")[\s\-]+\d{1,2},?[\s\-]+\d{4}\b",
    re.I
)

def normalize_date(txt: str):
    if not txt:
        return None
    low = txt.strip()
    for it,en in MONTHS_IT.items():
        low = re.sub(rf"\b{re.escape(it)}\b", en, low, flags=re.I)
    try:
        return dateparser.parse(low, dayfirst=False)
    except Exception:
        return None

def extract_article_links(archive_html: str, base_url: str):
    soup = BeautifulSoup(archive_html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if any(domain in href for domain in ["facebook.com","instagram.com","x.com","twitter.com","pinterest.com","feeds.feedburner.com"]):
            continue
        if "/blog/" in href:
            full = urljoin(base_url, href)
            if full not in links:
                links.append(full)
    return links

def parse_article(html: str, url: str, default_author: str = "ARTBOOMS"):
    soup = BeautifulSoup(html, "lxml")
    title = None
    og = soup.find("meta", {"property":"og:title"})
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        h1 = soup.find("h1")
        if h1: title = h1.get_text(strip=True)

    description = None
    ogd = soup.find("meta", {"property":"og:description"})
    if ogd and ogd.get("content"):
        description = ogd["content"].strip()
    if not description:
        p = soup.find("p")
        if p: description = p.get_text(" ", strip=True)

    author = default_author
    meta_author = soup.find("meta", attrs={"name": re.compile(r"author", re.I)})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"].strip()
    else:
        by = soup.find(attrs={"class": re.compile(r"author|byline", re.I)})
        if by:
            txt = by.get_text(" ", strip=True)
            if txt: author = txt

    pub = None
    mod = None
    for key in [{"property":"article:published_time"},{"itemprop":"datePublished"},{"name":"pubdate"},{"name":"date"}]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content"):
            pub = normalize_date(tag["content"]) or pub
    for key in [{"property":"article:modified_time"},{"property":"og:updated_time"},{"itemprop":"dateModified"},{"name":"lastmod"},{"name":"modified"}]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content"):
            mod = normalize_date(tag["content"]) or mod

    if not pub:
        header = soup.find("h1")
        block_text = " ".join((header.find_parent().get_text(" ", strip=True) if header and header.find_parent() else soup.get_text(" ", strip=True))[:1200].split())
        m = DATE_RX.search(block_text)
        if m:
            pub = normalize_date(m.group(0))

    if (not mod) and pub:
        mod = pub

    main = soup.find("article") or soup.find("main") or soup.find("div", class_=re.compile(r"post|entry|article|content|sqs-block-content", re.I))
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)
    image_tag = soup.find("meta", {"property":"og:image"})
    image_url = image_tag["content"] if image_tag and image_tag.get("content") else None

    return {
        "url": url,
        "title": title or url,
        "description": description or (content_text[:280] if content_text else None),
        "author": author,
        "published": pub.isoformat() if pub else None,
        "modified": mod.isoformat() if mod else None,
        "content_text": content_text or "",
        "image": image_url
    }
