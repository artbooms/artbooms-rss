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
        image_tag = soup.find('meta', property='og:image')
        if image_tag and image_tag.has_attr('content'):
            image = image_tag['content']
        else:
            image = None

        # Autore (opzionale)
        author_tag = soup.find('meta', itemprop='author')
        author = author_tag['content'] if author_tag and author_tag.has_attr('content') else None

        # DatePublished e DateModified (opzionali)
        date_modified_tag = soup.find('meta', itemprop='dateModified')
        date_modified = date_modified_tag['content'] if date_modified_tag and date_modified_tag.has_attr('content') else None

        date_published_tag = soup.find('meta', itemprop='datePublished')
        date_published = date_published_tag['content'] if date_published_tag and date_published_tag.has_attr('content') else None

        # Fallback pubDate in formato RSS (se non trovato da meta)
        pub_date = None
        if date_published:
            try:
                # converte ISO 8601 in formato RSS RFC 2822
                dt = datetime.datetime.fromisoformat(date_published.replace('Z', '+00:00'))
                pub_date = dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
            except Exception:
                pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
        else:
            pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        return {
            "description": description,
            "image": image,
            "author": author,
            "pubDate": pub_date,
            "dateModified": date_modified,
        }

    except Exception as e:
        return {
            "description": f"<p>Errore parsing articolo: {html.escape(str(e))}</p>",
            "image": None,
            "author": None,
            "pubDate": datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT'),
            "dateModified": None,
        }
