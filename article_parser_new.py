from urllib.parse import urljoin
import re
from bs4 import BeautifulSoup

def extract_article_links_from_archive_html(html, base_url):
    """
    Estrae tutti i link validi agli articoli dal codice HTML dell’archivio
    e li normalizza rimuovendo prefissi errati come '/archivio-completo/'.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    # Prende tutti i tag <a> con href
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        # Escludi tag e parametri
        if not href or "?" in href or "/tag/" in href:
            continue

        # Normalizza i percorsi relativi
        full_url = urljoin(base_url, href)

        # 🔧 Rimuovi '/archivio-completo' se presente
        full_url = re.sub(r"/archivio-completo/?", "/", full_url)

        # Tieni solo i link al blog veri
        if "/blog/" in full_url:
            links.add(full_url.split("?")[0].rstrip("/"))

    return sorted(links)
