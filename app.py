from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging

app = Flask(__name__)
FEED_URL = 'https://www.artbooms.com/archivio-completo'

def clean_text(text):
    if not text:
        return ''
    replacements = {
        '\u200b': '', '\u200c': '', '\u200d': '',
        '…': '...', '’': "'", '“': '"', '”': '"',
        '–': '-', '—': '-', '\xa0': ' ',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)

def extract_article_details(url):
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        description = ''
        image_url = ''

        desc_tag = soup.find('meta', attrs={'name': 'description'})
        if desc_tag and desc_tag.get('content'):
            description = clean_text(desc_tag['content'])

        img_tag = soup.find('meta', property='og:image')
        if img_tag and img_tag.get('content'):
            image_url = img_tag['content']

        return description, image_url
    except Exception as e:
        logging.warning(f"Errore scraping articolo {url}: {e}")
        return '', ''

def guess_category(title):
    title = title.lower()
    if 'fotografia' in title:
        return 'fotografia'
    elif 'design' in title:
        return 'design'
    else:
        return 'arte'

def get_articles():
    try:
        res = requests.get(FEED_URL, timeout=10)
        res.raise_for_status()
    except Exception as e:
        logging.error(f"Errore fetching FEED_URL: {e}")
        return []

    soup = BeautifulSoup(res.text, 'html.parser')
    items = []

    for li in soup.select('li.archive-item'):
        try:
            title_tag = li.select_one('a.archive-item-link')
            date_tag = li.select_one('span.archive-item-date-before')
            link = title_tag['href'] if title_tag else ''
            title = title_tag.text.strip() if title_tag else 'No title'
            date_str = date_tag.text.strip() if date_tag else ''

            try:
                pub_date = datetime.datetime.strptime(date_str, '%b %d, %Y')
            except Exception:
                pub_date = datetime.datetime.utcnow()

            pub_date_str = pub_date.strftime('%a, %d %b %Y %H:%M:%S GMT')
            full_link = 'https://www.artbooms.com' + link

            description, image_url = extract_article_details(full_link)
            category = guess_category(title)

            items.append({
                'title': title,
                'link': full_link,
                'pub_date': pub_date_str,
                'description': description,
                'image_url': image_url,
                'category': category,
            })
        except Exception as e:
            logging.warning(f"Errore parsing articolo: {e}")
            continue

    return items

@app.route('/rss.xml')
def rss():
    articles = get_articles()
    rss_items = ""
    for item in articles:
        rss_items += f"""    <item>
      <title>{clean_text(item['title'])}</title>
      <link>{clean_text(item['link'])}</link>
      <guid isPermaLink="true">{clean_text(item['link'])}</guid>
      <pubDate>{item['pub_date']}</pubDate>"""
        if item['description']:
            rss_items += f"""
      <description>{item['description']}</description>"""
        if item['category']:
            rss_items += f"""
      <category>{item['category']}</category>"""
        rss_items += f"""
      <source url="https://www.artbooms.com">Artbooms</source>"""
        if item['image_url']:
            rss_items += f"""
      <media:content url="{item['image_url']}" medium="image" />"""
        rss_items += "\n    </item>"

    last_build_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Artbooms RSS Feed</title>
    <link>https://www.artbooms.com/archivio-completo</link>
    <description>Notizie di arte contemporanea da Artbooms</description>
    <language>it-it</language>
    <lastBuildDate>{last_build_date}</lastBuildDate>
    <atom:link href="https://artbooms-rss.onrender.com/rss.xml" rel="self" type="application/rss+xml" />
{rss_items}
  </channel>
</rss>"""

    response = Response(rss_feed.strip(), mimetype='application/rss+xml; charset=utf-8')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Access-Control-Allow-Origin'] = '*'

    return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
