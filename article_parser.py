import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# Mappa mesi italiani -> inglese per dateparser
MONTHS_IT = {
    "gennaio":"January","febbraio":"February","marzo":"March","aprile":"April","maggio":"May","giugno":"June",
    "luglio":"July","agosto":"August","settembre":"September","ottobre":"October","novembre":"November","dicembre":"December",
    "gen":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","mag":"May","giu":"Jun","lug":"Jul","ago":"Aug","set":"Sep","ott":"Oct","nov":"Nov","dic":"Dec",
}

DATE_RX = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|" + "|".join(MONTHS_IT.keys()) + r")[\s\-]+\d{1,2},?[\s\-]+\d{4}\b", re.I)


def _normalize_date(txt: str):
    if not txt:
        return None
    s = txt.strip()
    for it, en in MONTHS_IT.items():
        s = re.sub(rf"\b{re.escape(it)}\b", en, s, flags=re.I)
    try:
        return dateparser.parse(s)
    except Exception:
        return None


def extract_article_links(archive_html: str, base_url: str):
    """Dalla pagina archivio prende i link agli articoli (contengono '/blog/')."""
    soup = BeautifulSoup(archive_html, "lxml")
    seen = set()
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        if any(x in href for x in ["facebook.com","instagram.com","x.com","twitter.com","pinterest.com","feeds.feedburner.com"]):
            continue
        if "/blog/" in href:
            full = urljoin(base_url, href)
            if full not in seen:
                seen.add(full)
                out.append(full)
    return out


def parse_article(html: str, url: str, default_author: str = "ARTBOOMS"):
    soup = BeautifulSoup(html, "lxml")

    # Canonical
    canonical = soup.find("link", rel="canonical")
    link = canonical.get("href").strip() if canonical and canonical.get("href") else url

    # Titolo
    title = None
    ogt = soup.find("meta", {"property": "og:title"})
    if ogt and ogt.get("content"): title = ogt["content"].strip()
    if not title and soup.title: title = soup.title.get_text(strip=True)

    # Descrizione
    description = None
    ogd = soup.find("meta", {"property": "og:description"})
    if ogd and ogd.get("content"): description = ogd["content"].strip()
    if not description:
        meta_desc = soup.find("meta", attrs={"name":"description"})
        if meta_desc and meta_desc.get("content"): description = meta_desc["content"].strip()

    # Immagine (priorità: og:image > itemprop image > thumbnailUrl > link rel=image_src)
    image = None
    for selector in [
        ("meta", {"property":"og:image"}, "content"),
        ("meta", {"itemprop":"image"}, "content"),
        ("meta", {"itemprop":"thumbnailUrl"}, "content"),
        ("link", {"rel":"image_src"}, "href"),
    ]:
        tag = soup.find(selector[0], attrs=selector[1])
        if tag and tag.get(selector[2]):
            image = tag.get(selector[2]).strip()
            break

    # Autore
    author = default_author
    meta_author = soup.find("meta", attrs={"itemprop":"author"}) or soup.find("meta", attrs={"name":"author"})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"].strip()

    # Date
    pub = None
    mod = None
    for key in [
        {"itemprop":"datePublished"},
        {"property":"article:published_time"},
        {"name":"pubdate"},
    ]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content") and not pub:
            pub = _normalize_date(tag["content"]) or pub

    for key in [
        {"itemprop":"dateModified"},
        {"property":"article:modified_time"},
        {"property":"og:updated_time"},
        {"name":"lastmod"},
    ]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content") and not mod:
            mod = _normalize_date(tag["content"]) or mod

    # Fallback near H1
    if not pub:
        h1 = soup.find("h1")
        block = (h1.find_parent().get_text(" ", strip=True) if h1 and h1.find_parent() else soup.get_text(" ", strip=True))
        m = DATE_RX.search(block)
        if m:
            pub = _normalize_date(m.group(0))
    if not mod and pub:
        mod = pub

    # Testo per hashing
    main = soup.find("article") or soup.find("main") or soup.find("div", class_=re.compile(r"post|entry|article|content|sqs-block-content", re.I))
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    return {
        "url": link or url,
        "title": title or url,
        "description": description or (content_text[:280] if content_text else None),
        "author": author,
        "image": image,
        "published": pub.isoformat() if pub else None,
        "modified": mod.isoformat() if mod else None,
        "content_text": content_text or "",
    }
