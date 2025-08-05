from flask import Flask, Response
import requests

app = Flask(__name__)

ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"

@app.route("/rss.xml")
def rss():
    res = requests.get(ARCHIVE_URL, timeout=10)
    html = res.text[:5000]  # solo i primi 5000 caratteri per evitare overload
    print("DEBUG: pagina archivio HTML\n", html)
    return Response("Debug page in log", status=200)

if __name__ == "__main__":
    app.run(debug=True)
