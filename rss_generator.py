from bs4 import BeautifulSoup
from datetime import datetime
import os
import html
import logging
import requests

from article_parser import parse_article  # assume che article_parser.py sia nel medesimo folder

logging.basicConfig(level=logging.INFO)

FEED_TITLE = "Artbooms – Archivio Completo"
FEED_LINK = "https://www.artbooms.com/archivio-completo"
FEED_DESCRIPTION = "Feed completo delle notizie d’arte contemporanea da Artbooms"
FEED_LANGUAGE = "it-it"

XML_ESCAPE_REPLACEMENTS = {
    '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '\u00A0': ' ',
}

def clean_text(text):
    if not text:
        return ''
    for bad, good in XML_ESCAPE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return html.escape(text.strip(), quote=True)

def generate_rss_from_html(html_path):
    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')
    archive_groups = soup.find_all('li', class_='archive-group')

    items = []
    for group in archive_groups:
        items_in_group = group.find_all('li', class_='archive-item')
        for item in items_in_group:
            try:
                date_before = item.find('span', class_='archive-item-date-before').get_text(strip=True)
                link_tag = item.find('a', class_='archive-item-link')
                title = link_tag.get_text(strip=True) if link_tag else "No title"
                url = link_tag['href'] if link_tag and link_tag.get('href') else ''
                full_url = f"https://www.artbooms.com{url}"

                # Data fittizia (verrà preferita quella interna all'articolo se disponibile)
                pub_date_archive = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

                # Estrai dettagli reali dall'articolo
                try:
                    details = parse_article(full_url)
                    pub_date = details.get('pubDate') or pub_date_archive
                    author = details.get('author') or ''
                    description = details.get('description') or ''
                    image = details.get('image')
                except Exception as e:
                    logging.warning(f"Errore estrazione articolo {full_url}: {e}")
                    pub_date = pub_date_archive
                    author = ''
                    description = ''
                    image = None

                items.append({
                    'title': title,
                    'link': full_url,
                    'guid': full_url,
                    'pub_date': pub_date,
                    'author': author,
                    'description': description,
                    'image': image,
                })
            except Exception:
                continue  # salta item se qualcosa va storto

    rss_items = ""
    for item in items:
        title = clean_text(item['title'])
        link = clean_text(item['link'])
        guid = clean_text(item['guid'])
        pubDate = item['pub_date']
        author = clean_text(item.get('author') or '')
        description = clean_text(item.get('description') or '')
        image_url = item.get('image')

        rss_items += f"""
        <item>
            <title>{title}</title>
            <link>{link}</link>
            <guid isPermaLink=\"true\">{guid}</guid>
            <pubDate>{pubDate}</pubDate>"""
        if author:
            rss_items += f"\n            <dc:creator>{author}</dc:creator>"
        if description:
            rss_items += f"\n            <description>{description}</description>"
        if image_url:
            escaped_img = html.escape(image_url, quote=True)
            rss_items += f"""
            <media:content url="{escaped_img}" medium="image" />"""
        rss_items += "\n        </item>"

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>{clean_text(FEED_TITLE)}</title>
    <link>{clean_text(FEED_LINK)}</link>
    <description>{clean_text(FEED_DESCRIPTION)}</description>
    <language>{clean_text(FEED_LANGUAGE)}</language>
    <lastBuildDate>{datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
    {rss_items}
  </channel>
</rss>"""

    return rss_feed
