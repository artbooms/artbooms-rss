from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging
import json
import os

from article_parser import parse_article

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

FEED_URL = 'https://www.artbooms.com/archivio-completo'
CACHE_FILE = 'articles_cache.json'

XML_ESCAPE_REPLACEMENTS = {
    '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '\u00A0': ' ',
}


def clean_text(text):
    if not text:
        return ''
    for bad, good in XML_ESCAPE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_articles():
    logging.info("🔄 Inizio parsing archivio...")
    res = requests.get(FEED_URL, timeout=10)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    cache = load_cache()
    updated_cache = cache.copy()
    items = []

    for li in soup.select('li.archive-item'):
        title_tag = li.select_one('a.archive-item-link')
        date_tag = li.select_one('span.archive-item-date-before')
        link_fragment = title_tag['href'] if title_tag else ''
        title = title_tag.text.strip() if title_tag else 'No title'
        date_str = date_tag.text.strip() if date_tag else ''

        try:
            pub_date_archive = datetime.datetime.strptime(date_str, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date_archive = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        full_link = 'https://www.artbooms.com' + link_fragment

        cached = cache.get(full_link)
        if cached and cached.get('pub_date') == pub_date_archive:
            logging.info(f"✅ Usato da cache: {full_link}")
            items.append(cached)
        else:
            logging.info(f"🆕 Parsing nuovo o aggiornato: {full_link}")
            detailed = parse_article(full_link)

            article_data = {
                'title': title,
                'link': full_link,
                'guid': full_link,
                'pub_date': detailed.get('pubDate') or pub_date_archive,
                'author': detailed.get('author'),
                'description': detailed.get('description'),
                'image': detailed.get('image'),
            }

            updated_cache[full_link] = article_data
