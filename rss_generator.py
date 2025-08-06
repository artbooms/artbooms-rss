      from bs4 import BeautifulSoup
from datetime import datetime
import html

def clean_text(text):
    """Rende il testo sicuro per XML e rimuove caratteri non validi"""
    if not text:
        return ''
    replacements = {
        '…': '...', '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', ' ': ' ',  # spazio non-breaking
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return html.escape(text, quote=True)

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
                title = link_tag.get_text(strip=True)
                url = link_tag['href']
                full_url = f"https://www.artbooms.com{url}"

                # Tenta il parsing della data; fallback a ora UTC se fallisce
                try:
                    pub_date = datetime.strptime(date_before, '%b %d, %Y').strftime('%a, %d %b %Y %H:%M:%S GMT')
                except Exception:
                    pub_date = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')

                items.append((clean_text(title), clean_text(full_url), pub_date))
            except Exception:
                continue  # salta item se qualcosa va storto

    rss_items = "\n".join([
        f"""<item>
  <title>{title}</title>
  <link>{link}</link>
  <guid isPermaLink="true">{link}</guid>
  <pubDate>{pub_date}</pubDate>
</item>""" for title, link, pub_date in items
    ])

    rss_feed = f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
  <title>Artbooms – Archivio Completo</title>
  <link>https://www.artbooms.com/archivio-completo</link>
  <description>Feed completo delle notizie d’arte contemporanea da Artbooms</description>
  <language>it-it</language>
  <lastBuildDate>{datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
  {rss_items}
</channel>
</rss>"""

    return rss_feed

# ESEMPIO USO (facoltativo per test locali):
# if __name__ == "__main__":
#     rss = generate_rss_from_html("pagina_locale.html")
#     with open("output_rss.xml", "w", encoding="utf-8") as f:
#         f.write(rss)
