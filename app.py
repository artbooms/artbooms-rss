from flask import Flask
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

BASE_URL = "https://www.artbooms.com"
ARCHIVE_URL = f"{BASE_URL}/blog/archive"

@app.route("/")
def debug_archive():
    res = requests.get(ARCHIVE_URL)
    html = res.text

    print("DEBUG: INIZIO HTML ARCHIVIO")
    print(html[:5000])  # stampiamo i primi 5000 caratteri
    print("DEBUG: FINE HTML ARCHIVIO")

    return "Controlla i log su Render: HTML stampato nel terminale"

if __name__ == "__main__":
    app.run()
