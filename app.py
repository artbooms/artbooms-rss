from flask import Flask, Response
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

app = Flask(__name__)

BASE_URL = "https://www.artbooms.com"
ARCHIVE_URL = f"{BASE_URL}/blog/archive"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

REPLACE_MAP = {
    '\u2019': "'",
    '\u201c': '"',
    '\u201d': '"',
    '\u00a0': ' ',
    '\u2014': '-',
    '\u2013': '-',
}

def clean_text(text):
    for bad, good in REPLACE_MAP.items():
        text = text.replace(bad, good)
    return text.strip()

def get_month_links():
    res = requests.get(ARCHIVE_URL, headers=HEADERS)
    soup = BeautifulSoup(res.text, 'html.parser')
    links = []
    print("DEBUG: Controllo archive page per mesi...")
    for a in soup.select(".archive-group a"):
        href = a.get("href")
        print("DEBUG: trovato href mese:", href)
        if href:
            links.append(BASE_URL + href)
    print("DEBUG: Link mesi trovati:", links)
    return links

def get_article_links(month_url):
    res = requests.get(month_url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")
    article_links = []
    print(f"DEBUG: Controllo articoli per mese: {month_url}")
    for a in soup.select("article a"):
        href = a.get("href")
        print("DEBUG: trovato href articolo:", href)
        if href:
            article_links.append(BASE_URL + href)
    print("DEBUG: Link articoli trovati:", article_links)
    return article_links

def parse_article(url):
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")

    title_tag = soup.select_one("meta[property='og:title']")
    desc_tag = soup.select_one("meta[property='og:description']")
    image_tag = soup.select_one("meta[property='og:image']")
    date_tag = soup.select_one("time")
    category_tag = soup.select_one(".sqs-block-html")

    title = clean_text(title_tag["content"]) if title_tag else ""
    description = clean_text(desc_tag["content"]) if desc_tag else ""
    image = image_tag["content"] if image_tag else ""
    pub_date = date_tag["datetime"] if date_tag and date_tag.has_attr("datetime") else ""
    category = category_tag.get_text(strip=True).split("\n")[0] if ca
