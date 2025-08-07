from flask import Flask, Response, request
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import time
import math

app = Flask(__name__)

BASE_URL = 'https://www.artbooms.com'
ARCHIVE_URL = f'{BASE_URL}/archivio-completo'
ARTICLES_PER_PAGE = 5


def get_article_links():
    response = requests.get(ARCHIVE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.select('a[href*="/blog/"]')
    urls = []
    for link in links:
        href = link['href']
        if href.startswith('/blog/') and (BASE_URL + href) not in urls:
            urls.append(BASE_URL + href)
    return urls


def extract_article_data(url):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        title = soup.find("meta", property="og:title")
        title = title['content'] if title else soup.title.string.strip()

        pub_date_tag = soup.find(attrs={"itemprop": "datePublished"})
        pub_date = pub_date_tag['content'] if pub_date_tag else None

        mod_date_tag = soup.find(attrs={"itemprop": "dateModified"})
        mod_date = mod_date_tag['content'] if mod_date_tag else pub_date

        description_tag = soup.find("meta", attrs={"name": "description"})
        description = description_tag['content'] if description_tag else ''

        author_tag = soup.find(attrs={"itemprop": "author"})
        author = author_tag.get_text(strip=True) if author_tag else 'Artbooms'

        image_tag = soup.find("meta", property="og:image")
        image_url = image_tag['content'] if image_tag else ''

        return {
            "title": title,
            "link": url,
            "guid": url,
            "pubDate": pub_date,
            "modDate": mod_date,
            "description": description,
            "author": author,
            "image": image_url
        }
    except Exception as e:
        print(f"Errore nell'articolo {url}: {e}")
        return None


def format_rss_item(article):
    item = f"""
    <item>
        <title>{article['title']
