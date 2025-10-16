import requests
from bs4 import BeautifulSoup
from datetime import datetime
import logging

logger = logging.getLogger("article_parser")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_html(url):
    """Scarica l'HTML di una pagina."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error(f"[Parser] Errore fetch {url}: {e}")
        return ""


def extract_article_links_from_archive_html(html, base_url):
    """Estrae tutti i link /blog/... dall'archivio, escludendo tag e month."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/blog/" in href and "?month" not in href and "/tag/" not in href:
            if href.startswith("/"):
                href = base_url.rstrip("/") + href
            if href not in links:
                links.append(href)
    return links


def parse_article(url):
    """Estrae i meta tag principali da un articolo Squarespace."""
    html = fetch_html(url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    meta = {}

    def get_meta(*names):
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"itemprop": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    meta["url"] = url
    meta["title"] = (
        get_meta("og:title", "itemprop", "headline")
        or (soup.title.string.strip() if soup.title else "")
    )
    meta["description"] = get_meta("og:description", "itemprop", "description") or ""
    meta["image"] = get_meta("og:image", "itemprop", "image") or ""
    meta["author"] = get_meta("itemprop", "author") or ""
    meta["published"] = get_meta("itemprop", "datePublished") or ""
    meta["modified"] = get_meta("itemprop", "dateModified") or ""

    # Normalizza date ISO
    for key in ("published", "modified"):
        val = meta.get(key)
        if val:
            try:
                meta[key] = datetime.fromisoformat(val.replace("Z", "+00:00")).isoformat()
            except Exception:
                pass

    logger.info(f"[Parser] Estratto meta per {url}: {meta['title']}")
    return meta


if __name__ == "__main__":
    test = "https://www.artbooms.com/blog/vivian-suter-palais-tokyo-parigi"
    print(parse_article(test))

   
