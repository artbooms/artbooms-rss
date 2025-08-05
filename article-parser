import requests
from bs4 import BeautifulSoup
from datetime import datetime
import html

def maybe_shrink_squarespace(url):
    # Riduce le immagini Squarespace a un formato più leggero (800w)
    if "squarespace" in url and "?" in url:
        base, _ = url.split("?", 1)
        return base + "?format=800w"
    elif "squarespace" in url:
        return url + "?format=800w"
    return url

def parse_article(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.content, "html.parser")

    # TITLE
    title = soup.select_one("meta[property='og:title']")
    if title:
        title = title["content"].replace("— ARTBOOMS", "").strip()
    else:
        title = soup.title.string.strip() if soup.title else url

    # LINK
    canonical = soup.select_one("link[rel='canonical']")
    link = canonical["href"] if canonical else url

    # GUID
    guid = link

    # PUB DATE
    date = soup.select_one('[itemprop="datePublished"]')
    if date and date.get("content"):
        pub_date = date["content"]
        try:
            dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
            pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        except Exception:
            pub_date = None
    else:
        pub_date = None

    # AUTHOR
    author_tag = soup.select_one('[itemprop="author"]')
    author = author_tag["content"] if author_tag and author_tag.get("content") else "ARTBOOMS"

    # DESCRIPTION
    desc = None
    itemprop_desc = soup.select_one('[itemprop="description"]')
    if itemprop_desc and itemprop_desc.get("content"):
        desc = itemprop_desc["content"].strip()
    else:
        og_desc = soup.select_one('meta[property="og:description"]')
        if og_desc and og_desc.get("content"):
            desc = og_desc["content"].strip()
        else:
            meta_desc = soup.select_one('meta[name="description"]')
            if meta_desc and meta_desc.get("content"):
                desc = meta_desc["content"].strip()
            else:
                article_container = soup.select_one('article')
                p = article_container.find('p') if article_container else soup.find('p')
                if p:
                    desc = p.get_text(" ", strip=True)
    if not desc:
        headline = soup.select_one('[itemprop="headline"]')
        if headline and headline.get("content"):
            desc = headline["content"].strip()
    if desc and len(desc) > 220:
        desc = desc[:217] + "..."

    # IMAGE
    og_image = soup.select_one('meta[property="og:image"]')
    image = og_image["content"].strip() if og_image and og_image.get("content") else None
    if image:
        image = maybe_shrink_squarespace(image)

    return {
        "title": title,
        "link": link,
        "guid": guid,
        "pubDate": pub_date,
        "author": author,
        "description": desc,
        "image": image
    }

def format_rss_item(article):
    return f"""
<item>
    <title>{html.escape(article['title'])}</title>
    <link>{html.escape(article['link'])}</link>
    <guid isPermaLink=\"true\">{html.escape(article['guid'])}</guid>
    {f"<pubDate>{article['pubDate']}</pubDate>" if article['pubDate'] else ""}
    <dc:creator>{html.escape(article['author'])}</dc:creator>
    <description>{html.escape(article['description'])}</description>
    {f'<media:content url=\"{html.escape(article["image"])}\" medium=\"image\" />' if article['image'] else ""}
</item>
"""
