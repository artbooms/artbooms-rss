from flask import Flask, make_response
import requests
from bs4 import BeautifulSoup
import datetime
import html
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

FEED_URL = 'https://www.artbooms.com/archivio-completo'

def clean_text(text):
    if not text:
        return ''
    replacements = {
        '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', ' ': ' ',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)

def get_articles():
    res = requests.get(FEED_URL)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    items = []
    for li in soup.select('li.archive-item'):
        title_tag = li.select_one('a.archive-item-link')
        date_tag = li.select_one('span.archive-item-date-before')
        link = title_tag['href'] if title_tag else ''
        title = title_tag.text.strip() if title_tag else 'No title'
        date_str = date_tag.text.strip() if date_tag else ''

        try:
            pub_date = datetime.datetime.strptime(date_str, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
        except Exception:
            pub_date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

        full_link = 'https://www.artbooms.com' + link
        description = ''
        image_url = ''

        try:
            article_res = requests.get(full_link)
            article_soup = BeautifulSoup(article_res.text, 'html.parser')

            first_paragraph = article_soup.select_one('div.sqs-block-content p')
