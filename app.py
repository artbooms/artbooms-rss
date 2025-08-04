from flask import Flask, Response
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

BASE_URL = "https://www.artbooms.com"
ARCHIVE_URL = f"{BASE_URL}/blog/archive"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

REPLACE_MAP = {
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00a0": " ",
    "\u2014": "-",
    "\u2013": "-",
}

def clean_text(text):
    for bad, good in REPLACE_MAP.items():
        text = text.replace(bad, good)
    return text.strip()

def get_month_links():
    res = requests.get(ARCHIVE_URL, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")
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
    date_tag = soup.select_one("time")
    category_tag = soup.select_one(".sqs-block-html")

    title = clean_text(title_tag["content"]) if title_tag else ""
    description = clean_text(desc_tag["content"]) if desc_tag else ""
    image = image_tag["content"] if image_tag else ""
    pub_date = date_tag["datetime"] if date_tag and date_tag.has_attr("datetime") else ""
    category = category_tag.get_text(strip=True).split("\n")[0] if category_tag else ""

    return {
        "title": title,
        "description": description,
        "link": url,
        "image": image,
        "pubDate": pub_date,
        "category": category,
    }

@app.route("/rss.xml")
def rss():
    items = []
    month_links = get_month_links()
    for month_url in month_links:
        article_links = get_article_links(month_url)
        for link in article_links:
            try:
                article = parse_article(link)
                item = (
                    "<item>"
                    f"<title><![CDATA[{article['title']}]]></title>"
                    f"<link>{article['link']}</link>"
                    f"<description><![CDATA[{article['description']}]]></description>"
                    f"<pubDate>{article['pubDate']}</pubDate>"
                    f"<category>{article['category']}</category>"
                    f'<enclosure url="{article["image"]}" type="image/jpeg" />'
                    "</item>"
                )
                items.append(item)
            except Exception:
                continue

    rss_feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<rss version='2.0'>\n"
        "<channel>\n"
        "<title>Artbooms</title>\n"
        f"<link>{BASE_URL}</link>\n"
        "<description>Artbooms RSS Feed</description>\n"
        "<language>it-it</language>\n"
        + "".join(items)
        + "\n</channel>\n</rss>"
    )

    return Response(rss_feed, mimetype="application/rss+xml")

if __name__ == "__main__":
    app.run(debug=True)
