from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import time

app = Flask(__name__)

BASE_URL = 'https://www.artbooms.com/blog?month='
BASE_ARTICLE_URL = 'https://www.artbooms.com'
MONTHS = [(datetime.datetime(2025, 8, 1) - datetime.timedelta(days=30 * i)).strftime('%m-%Y') for i in range(115)]


def clean_text(text):
    if not text:
        return ''
    replacements = {
        '​': '',
        '‌': '',
        '‍': '',
        '…': '...',
        '’': "'",
        '“': '"',
        '”': '"',
        '–': '-',
        '—': '-',
        '         '\xa0': ' ',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)


def fetch_article_details(article_url):
    try:
        res = requests.get(article_url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        title = soup.find('meta', property='og:title')
        description = soup.find('meta', property='og:description')
        image = soup.find('meta', property='og:image')
        date = soup.find('meta', itemprop='datePublished')

        article_tag = soup.find('article')
        category = ''
        if article_tag and 'class' in article_tag.attrs:
            for cls in article_tag['class']:
                if cls.startswith('category-'):
                    category = cls.replace('category-', '')

        return {
            'title': clean_text(title['content'] if title else ''),
            'description': clean_text(description['content'] if description else ''),
            'image': image['content'] if image else '',
            'pub_date': datetime.datetime.strptime(date['content'][:10], '%Y-%m-%d') if date else datetime.datetime.utcnow(),
            'category': category
        }

    except Exception as e:
        print(f"Errore fetch dettagli articolo: {e}")
        return None


def get_articles():
    items = []
    for month in MONTHS:
        try:
            url = BASE_URL + month
            res = requests.get(url, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')
            
            links = soup.select('a.archive-item-link')
            for a in links:
                relative_link = a['href']
                full_link = BASE_ARTICLE_URL + relative_link
                details = fetch_article_details(full_link)
                if details:
                    items.append({
                        'title': details['title'],
                        'link': full_link,
                        'description': details['description'],
                        'image': details['image'],
                        'category': details['category'],
                        'pub_date': details['pub_date'].strftime('%a, %d %b %Y %H:%M:%S GMT')
                    })
                time.sleep(1)  # Evita di sovraccaricare il server
        except Exception as e:
            print(f"Errore parsing mese {month}: {e}")
            continue
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
      <guid isPermaLink=\"true\">{item['link']}</guid>
      <description><![CDATA[{item['description']}]]></description>
      <pubDate>{item['pub_date']}</pubDate>
      <category>{item['category']}</category>
      <enclosure url=\"{item['image']}\" type=\"image/jpeg\" />
    </item>"""

    last_build_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    rss_feed = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\" xmlns:atom=\"http://www.w3.org/2005/Atom\">
  <channel>
    <title>Artbooms RSS Feed</title>
    <link>https://www.artbooms.com/archivio-completo</link>
    <description>Feed dinamico degli articoli di Artbooms</description>
    <language>it-it</language>
    <lastBuildDate>{last_build_date}</lastBuildDate>
    <atom:link href=\"https://artbooms-rss.onrender.com/rss.xml\" rel=\"self\" type=\"application/rss+xml\" />
    {rss_items}
  </channel>
</rss>"""

    response = Response(rss_feed.strip(), mimetype='application/rss+xml; charset=utf-8')
    response.headers['Content-Encoding'] = 'identity'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Access-Control-Allow-Origin'] = '*'

    return response


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
