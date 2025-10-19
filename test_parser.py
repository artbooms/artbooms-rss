from article_parser import extract_article_links_from_archive_html, parse_article, fetch_html

ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
BASE_URL = "https://www.artbooms.com"

print("Scarico archivio completo...")
html = fetch_html(ARCHIVE_URL)
links = extract_article_links_from_archive_html(html, BASE_URL)
print(f"Trovati {len(links)} link")
if links:
    print("Primo:", links[0])
    print("Ultimo:", links[-1])

print("\n--- Test singolo articolo ---")
url = links[0] if links else "https://www.artbooms.com/blog/vivian-suter-palais-tokyo-parigi"
item = parse_article(url)
for k, v in item.items():
    print(f"{k}: {str(v)[:120]}")
