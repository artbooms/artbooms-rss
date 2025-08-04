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
    for a in soup.select(".archive-group a"):
        href = a.get("href")
        if href:
            links.append(BASE_URL + href)
    return links

def get_article_links(month_url):
    res = requests.get(month_url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")
    return [BASE_URL + a.get("href") for a in soup.select("article a") if a.get("href")]

def parse_article(url):
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")

    title_tag = soup.select_one("meta[property='og:title']")
    desc_tag = soup.select_one("meta[property='og:description']")
    image_tag = soup.select_one("meta[property='og:image']")
    date_tag = soup.select_one("meta[itemprop='datePublished']")
    category_tag = soup.select_one("article")

    title = clean_text(title_tag["content"]) if title_tag else ""
    description = clean_text(desc_tag["content"]) if desc_tag else ""
    image = image_tag["content"] if image_tag else ""
    pub_date = date_tag["content"] if date_tag and date_tag.has_attr("content") else ""
    category_match = re.search(r'category-([\w-]+)', category_tag["class"][0]) if category_tag else None
    category = category_match.group(1) if category_match else "Art"

    return {
        "title": title,
        "description": description,
        "link": url,
        "image": image,
        "pubDate": pub_date,
        "category": category
    }

@app.route("/rss.xml")
def rss():
    items = []
    month_links = get_month_links()

    # Facoltativamente limita i mesi recenti per evitare out-of-memory
    for month_url in month_links[:6]:  # <-- cambia qui il numero se vuoi più/meno mesi
        article_links = get_article_links(month_url)
        for link in article_links:
            try:
                article = parse_article(link)
                items.append(f"""
<item>
    <title><![CDATA[{article['title']}]]></title>
    <link>{article['link']}</link>
    <description><![CDATA[{article['description']}]]></description>
    <pubDate>{article['pubDate']}</pubDate>
    <category>{article['category']}</category>
    <enclosure url="{article['image']}" type="image/jpeg" />
</item>""")
            except Exception:
                continue

    rss_feed = '<?xml version="1.0" encoding="UTF-8"?>\n'
    rss_feed += '<rss version="2.0">\n<channel>\n'
    rss_fee_
