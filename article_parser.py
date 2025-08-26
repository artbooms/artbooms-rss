import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

MONTHS_IT = {
    "gennaio":"January","febbraio":"February","marzo":"March","aprile":"April","maggio":"May","giugno":"June",
    "luglio":"July","agosto":"August","settembre":"September","ottobre":"October","novembre":"November","dicembre":"December",
    "gen":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","mag":"May","giu":"Jun","lug":"Jul","ago":"Aug","set":"Sep","ott":"Oct","nov":"Nov","dic":"Dec",
}

DATE_RX = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|"
    + "|".join(MONTHS_IT.keys()) + r")[\s\-]+\d{1,2},?[\s\-]+\d{4}\b",
    re.I
)

def normalize_date(txt: str):
    if not txt:
        return None
    low = txt.strip()
    for it, en in MONTHS_IT.items():
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
            if full
