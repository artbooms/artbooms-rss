import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
import html

def maybe_shrink_squarespace(url, target_width=800):
    # Riduce le immagini Squarespace a un formato più leggero (es. 800w)
    if "squarespace" in url and "format=" in url:
        return re.sub(r'format=\\d+w', f'format={target_width}w', url)
    if "squarespace" in url:
        if "?" in url:
            base, _ = url.split("?", 1)
            return base + f"?format={target_width}w"
        return url + f"?format={target_width}w"
    return url

def parse_article(url):
    """
    Estrae titolo, link, guid, pubDate, autore, descrizione e immagine da un articolo.
    Restituisce un dict con questi campi (alcuni possono essere None).
    """
    result = {
        "title": None,
        "link": url,
        "guid": url,
        "pubDate": None,
        "author": None,
        "description": None,
        "image": None,
    }

    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        # TITLE
        og_title = soup.select_one("meta[property='og:title']")
        if og_title and og_title.get("content"):
            title = og_title["content"].replace("— ARTBOOMS", "").strip()
        else:
            title = soup.title.string.strip() if soup.title else url
        result["title"] = title

        # LINK / GUID
        canonical = soup.select_one("link[rel='canonical']")
        link = canonical["href"].strip() if canonical and canonical.get("href") else url
        result["link"] = link
        result["guid"] = link

        # PUB DATE
        date_tag = soup.select_one('[itemprop="datePublished"]')
        if date_tag and date_tag.get("content"):
            raw = date_tag["content"]
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                result["pubDate"] = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
            except Exception:
                # fallback: lascia raw o ignora
                pass

        # AUTHOR
        author = None
        itemprop_author = soup.select_one('[itemprop="author"]')
        if itemprop_author and itemprop_author.get("content"):
            author = itemprop_author["content"].strip()
        else:
            meta_author = soup.select_one('meta[name="author"]')
            if meta_author and meta_author.get("content"):
                author = meta_author["content"].strip()
            else:
                author_elem = soup.select_one('[class*="author"], [class*="byline"], [class*="written-by"]')
                if author_elem:
                    author = author_elem.get_text(" ", strip=True)
                else:
                    text = soup.get_text(" ", strip=True)
                    m = re.search(r'\b[Bb]y\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', text)
                    if m:
                        author = m.group(1).strip()
        if author:
            result["author"] = author

        # DESCRIPTION: itemprop > og:description > meta[name=description] > primo paragrafo > headline
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
        if desc:
            if len(desc) > 220:
                cut = desc[:220]
                if " " in cut:
                    cut = cut.rsplit(" ", 1)[0]
                desc = cut + "…"
            result["description"] = desc

        # IMAGE: og:image / twitter:image / fallback img
        image_url = None
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get("content"):
            image_url = og_image["content"].strip()
        else:
            twitter_image = soup.select_one('meta[name="twitter:image"]')
            if twitter_image and twitter_image.get("content"):
                image_url = twitter_image["content"].strip()
            else:
                article_container = soup.select_one('article')
                img = None
                if article_container:
                    img = article_container.find('img')
                if not img:
                    img = soup.find('img')
                if img and img.get("src"):
                    image_url = img["src"].strip()
        if image_url:
            image_url = maybe_shrink_squarespace(image_url)
            result["image"] = image_url

    except Exception as e:
        # Non bloccare: ritorna ciò che ha estratto finora
        logging_msg = f"Errore in parse_article({url}): {e}"
        try:
            import logging
            logging.warning(logging_msg)
        except ImportError:
            pass  # nel caso non sia disponibile

    return result
