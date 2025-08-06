import requests
from bs4 import BeautifulSoup
import datetime
import html

def parse_article(url):
    """
    Analizza l'articolo di Artbooms dato un URL.
    Ritorna il contenuto HTML pulito da includere nel feed RSS.
    """
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        # Esempio: prendi solo il contenuto dell’articolo vero e proprio
        main_content = soup.select_one('div.sqs-block-content')
        if not main_content:
            return "<p>Contenuto non disponibile</p>"

        # Rimuovi script, iframe, ecc. per sicurezza
        for tag in main_content(['script', 'iframe', 'style']):
            tag.decompose()

        return str(main_content)

    except Exception as e:
        return f"<p>Errore durante il parsing: {html.escape(str(e))}</p>"
