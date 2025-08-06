import requests
from bs4 import BeautifulSoup
import datetime
import html

def parse_article(url):
    """
    Analizza l'articolo di Artbooms dato un URL.
    Ritorna un dizionario con dati per il feed RSS.
    """
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        # Titolo alternativo, se serve
        title_tag = soup.find('title')
        title = title_tag.text.strip() if title_tag else "Articolo Artbooms"

        # Descrizione: testo del contenuto principale
        main_content = soup.select_one('div.sqs-block-content')
        if not main_content:
            description = "<p>Contenuto non disponibile</p>"
        else:
            for tag in main_content(['script', 'iframe', 'style']):
                tag.decompose()
            description = str(main_content)

        # Autore non disponibile — metti None o vuoto
        author = None

        # Data fittizia per ora
        pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        # Prova a trovare un'immagine, se c'è
        image_tag = soup.find('img')
        image = image_tag['src'] if image_tag and image_tag.has_attr('src') else None

        return {
            "title": title,
            "link": url,
            "guid": url,
            "pubDate": pub_date,
            "author": author,
            "description": description,
            "image": image
        }

    except Exception as e:
        return {
            "title": "Errore nel parsing",
            "link": url,
            "guid": url,
            "pubDate": datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT'),
            "author": None,
            "description": f"<p>Errore durante il parsing: {html.escape(str(e))}</p>",
            "image": None
        }
