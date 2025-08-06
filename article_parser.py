import requests
from bs4 import BeautifulSoup
import datetime
import html

def parse_article(url):
    """
    Estrae contenuti base da un articolo Artbooms.
    Ritorna un dizionario con le chiavi richieste dal feed RSS.
    """
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        # Descrizione: prendi il contenuto centrale
        content = soup.select_one('div.sqs-block-content')
        if not content:
            description = "<p>Contenuto non disponibile</p>"
        else:
            for tag in content(['script', 'style', 'iframe']):
                tag.decompose()
            description = str(content)

        # Immagine (opzionale)
        image_tag = soup.find('img')
        image = image_tag['src'] if image_tag and image_tag.has_attr('src') else None

        # Data fittizia (nel formato RSS)
        pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        return {
            "description": description,
            "image": image,
            "author": None,
            "pubDate": pub_date,
        }

    except Exception as e:
        return {
            "description": f"<p>Errore parsing articolo: {html.escape(str(e))}</p>",
            "image": None,
            "author": None,
            "pubDate": datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT'),
        }
