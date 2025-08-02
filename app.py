from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime

app = Flask(__name__)

FEED_URL = 'https://www.artbooms.com/archivio-completo'  # Cambia se serve

def get_articles():
    res = requests.get(FEED_URL)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    items = []
    # Trova tutti gli articoli (modifica se struttura cambia)
    for li in soup.select('li.archive-item'):
        title_tag = li.select_one('a.archive-item-link')
        date_tag = li.select_one('span.archive-item-date-before')
        link = title_tag['href'] if title_tag else ''
        title = title_tag.text.strip() if title_tag else 'No title'
        date_str = date_tag.text.strip() if date_tag else ''
        # Format date to RFC822 for RSS
        try:
            pub_date = datetime.datetime.strptime(date_str, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        items.append({'title': title, 'link': 'https://www.artbooms.com' + link, 'pub_date': pub_date})

    return items

@app.route('/rss.xml')
def rss():
    articles = get_articles()
    rss_items = ''
    for item in articles:
        rss_items += f"""
        <item>
            <title>{item['title']}</title>
            <link>{item['link']}</link>
            <pubDate>{item['pub_date']}</pubDate>
        </item>"""

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8" ?>
    <rss version="2.0">
        <channel>
            <title>Artbooms RSS Feed</title>
            <link>https://www.artbooms.com/archivio-completo</link>
            <description>Feed dinamico degli articoli di Artbooms</description>
            {rss_items}
        </channel>
    </rss>"""
    
    return Response(rss_feed, mimetype='application/rss+xml')

if __name__ == '__main__':
    app.run(debug=True)
    @app.route('/test_connection')
def test_connection():
    import requests
    try:
        r = requests.get('https://www.google.com', timeout=5)
        return f"Success! Status code: {r.status_code}"
    except Exception as e:
        return f"Errore connessione: {str(e)}"
        @app.route('/test_connection')
def test_connection():
    try:
        r = requests.get("https://www.artbooms.com/archivio-completo", timeout=10)
        return f"Connection status code: {r.status_code}"
    except Exception as e:
        return f"Connection error: {e}"

@app.route('/test_articles')
def test_articles():
    try:
        articles = get_articles()
        return "<br>".join([f"{a['title']} → {a['link']}" for a in articles])
    except Exception as e:
        return f"Parsing error: {e}"


