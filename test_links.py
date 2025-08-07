import requests
from bs4 import BeautifulSoup

FEED_URL = 'https://www.artbooms.com/archivio-completo'

def test_links():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'}
    res = requests.get(FEED_URL, headers=headers)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    links = []
    for li in soup.select('li.archive-item'):
        a = li.select_one('a.archive-item-link')
        if a:
            link = 'https://www.artbooms.com' + a['href']
            links.append(link)

    print(f"Trovati {len(links)} link, ecco i primi 5:")
    for link in links[:5]:
        print(link)

    # Testa la richiesta sul primo link
    if links:
        try:
            test_res = requests.get(links[0], headers=headers, timeout=10)
            print(f"Status primo link: {test_res.status_code}")
        except Exception as e:
            print(f"Errore richiesta primo link: {e}")

if __name__ == "__main__":
    test_links()
