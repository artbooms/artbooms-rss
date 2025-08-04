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

        # Meta description
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        if desc_tag and desc_tag.get('content'):
            description = clean_text(desc_tag['content'])

        # og:image
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
    rss_items = ''
    for item in articles:
        rss_items += f"""
    <item>
      <title>{clean_text(item['title'])}</title>
      <link>{clean_text(item['link'])}</link>
      <guid isPermaLink="true">{clean_text(item['link'])}</guid>
      <pubDate>{item['pub_date']}</pub_]()
