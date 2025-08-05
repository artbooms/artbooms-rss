from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

ARCHIVE_URL = "https://www.artbooms.com/archivio-testuale"

MONTHS = {
    'Gennaio': '01',
    'Febbraio': '02',
    'Marzo': '03',
    'Aprile': '04',
    'Maggio': '05',
    'Giugno': '06',
    'Luglio': '07',
    'Agosto': '08',
    'Settembre': '09',
    'Ottobre': '10',
    'Novembre': '11',
    'Dicembre': '12'
}

@app.route("/rss.xml")
def rss():
    try:
        res = requests.get(ARCHIVE_URL, timeout=10)
        res.raise_for_status()
    except Exception as e:
        logging.error(f"Errore nel recupero dell'archivio: {e}")
        return Response("Errore nel recupero dell'archivio", status=500)

    soup = BeautifulSoup(res.text, 'html.parser')
    logging.debug("DEBUG: HTML ARCHIVIO PARSATO")

    articles = []
    groups = soup.find_all("li", class_="archive-group")

    for group in groups:
        # Estrai il mese e l'anno
        month_link = group.find("a", class_="archive-group-name-link")
        if not month_link:
            continue

        try:
            month_year = month_link.get_text(strip=True)
            month_name, year = month_year.split()
            month = MONTHS.get(month_name)
            if not month:
                continue
        except Exception as e:
            logging.warning(f"Errore parsing mese/anno: {e}")
            continue

        # Estrai i singoli articoli
        items = group.find_all("a", class_="archive-item-link")
        for item in items:
            title = item.get_text(strip=True)
            link = item['href']
            if not link.startswith("http"):
                link = "https://www.artbooms.com" + link

            # Estrai la data dal testo vicino
            parent = item.find_parent("li")
            date_span = parent.find("span", class_="archive-item-publish-date")
            if date_span:
                day_str = date_span.get_text(strip=True).replace(',', '')
                try:
                    day = int(day_str.split()[1])
                except:
                    day = 1  # fallback

                pub_date = datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
            else:
                pub_date = datetime.strptime(f"{year}-{month}-01", "%Y-%m-%d")

            articles.append({
                "title": title,
                "link": link,
                "pub_date": pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
            })

    # Costruzione RSS
    items_xml = ""
    for article in articles:
        items_xml += f"""
        <item>
            <title>{article['title']}</title>
            <link>{article['link']}</link>
            <pubDate>{article['pub_date']}</pubDate>
        </item>
        """

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version='2.0'>
<channel>
<title>Artbooms</title>
<link>https://www.artbooms.com</link>
<description>Artbooms RSS Feed</description>
<language>it-it</language>
{items_xml}
</channel>
</rss>"""

    return Response(rss_feed, mimetype='application/rss+xml')
