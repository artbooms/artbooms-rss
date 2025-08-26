# Panoramica

Questo drop‑in sostituisce/aggiorna i file del tuo repo per generare **/rss.xml** (alias **/feed**) leggendo l’archivio completo di Artbooms, rilevando automaticamente le modifiche e comunicandole via RSS.

**Caratteristiche chiave**

* Parsing da `https://www.artbooms.com/archivio-completo` (link che puntano a `/blog/...`).
* Per ogni articolo estrae: **titolo, link, autore, descrizione, data pubblicazione, data modifica**.
* Rilevamento modifiche con **ETag/Last‑Modified** + **hash contenuto**.
* Feed **RSS 2.0** con `dc:creator`, `dcterms:modified`, `atom:link rel="self"`, `lastBuildDate`, `guid` stabile.
* Endpoint: `/rss.xml`, `/feed`, `/healthz`.
* Parametro `?force=1` per rigenerare ignorando cache.

---

## 1) File da MANTENERE (o sostituire)

Copia i contenuti delle sezioni sotto nei file omonimi del repo:

* `app.py`
* `article_parser.py`
* `article_processor.py`
* `rss_generator.py`
* `worker.py` *(opzionale)*
* `requirements.txt`
* `Procfile`
* (opzionale in locale) `artbooms_archivio_completo.html`

## 2) File da ELIMINARE

* **`Dokerfile`** (era scritto con errore; non viene usato da Render).
* Se nel repo c’è anche `Dockerfile` **eliminalo**: useremo la modalità Procfile nativa di Render.

*(Mantieni `articles_cache.json` in git: la prima volta può essere assente, verrà creato.)*

## 3) Variabili d’ambiente (Render > Environment)

Impostale esattamente così per il tuo caso:

* `ARCHIVE_URL=https://www.artbooms.com/archivio-completo`
* `BASE_URL=https://www.artbooms.com`
* `SELF_FEED_URL=https://artbooms-rss.onrender.com/rss.xml`
* `DEFAULT_AUTHOR=ARTBOOMS`
* *(opzionali)* `REQUEST_TIMEOUT=20`, `MAX_CONCURRENCY=6`, `REFRESH_INTERVAL=15`

## 4) Start command (Render)

In **Start Command** metti:

```
web: gunicorn app:app --workers 2 --threads 4 --timeout 120 --preload
```

Se Render non usa automaticamente il Procfile, incolla soltanto la parte dopo `web:` nel campo “Start Command”.

## 5) Test

* Apri `/healthz` per la prova vita.
* Apri `/rss.xml` (o `/feed`).
* Usa `/rss.xml?force=1` dopo un aggiornamento dell’archivio per rigenerare subito.
* Controlla nelle **Response headers**: `ETag` e `Last-Modified` cambiano quando modifichi un articolo.

---

## app.py

```python
import os
from flask import Flask, Response, request, jsonify, make_response
from datetime import datetime
from article_processor import generate_items, load_cache
from rss_generator import build_rss

app = Flask(__name__)

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})

@app.get("/")
def root():
    return jsonify({
        "service": "artbooms-rss",
        "endpoints": ["/rss.xml", "/feed", "/healthz"],
    })

@app.get("/feed")
@app.get("/rss.xml")
def feed():
    force = request.args.get("force") == "1"
    items, meta = generate_items(force=force)
    xml_bytes, headers = build_rss(items, meta)

    resp = make_response(xml_bytes)
    resp.headers["Content-Type"] = "application/rss+xml; charset=utf-8"
    # Intestazioni HTTP per i crawler
    if headers.get("ETag"): resp.headers["ETag"] = headers["ETag"]
    if headers.get("Last-Modified"): resp.headers["Last-Modified"] = headers["Last-Modified"]
    if headers.get("Cache-Control"): resp.headers["Cache-Control"] = headers["Cache-Control"]
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
```

---

## article\_parser.py

```python
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

MONTHS_IT = {
    "gennaio":"January","febbraio":"February","marzo":"March","aprile":"April","maggio":"May","giugno":"June",
    "luglio":"July","agosto":"August","settembre":"September","ottobre":"October","novembre":"November","dicembre":"December",
    "gen":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","mag":"May","giu":"Jun","lug":"Jul","ago":"Aug","set":"Sep","ott":"Oct","nov":"Nov","dic":"Dec",
}

DATE_RX = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|" + "|".join(MONTHS_IT.keys()) + r")[\s\-]+\d{1,2},?[\s\-]+\d{4}\b", re.I)


def normalize_date(txt: str):
    if not txt:
        return None
    low = txt.strip()
    for it,en in MONTHS_IT.items():
        low = re.sub(rf"\b{re.escape(it)}\b", en, low, flags=re.I)
    try:
        return dateparser.parse(low, dayfirst=False)
    except Exception:
        return None


def extract_article_links(archive_html: str, base_url: str):
    """Raccoglie tutti i link agli articoli dall'archivio completo di Artbooms.
    Criterio: href che contiene "/blog/" e che non punta a social esterni.
    """
    soup = BeautifulSoup(archive_html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if any(domain in href for domain in ["facebook.com", "instagram.com", "x.com", "twitter.com", "pinterest.com", "feeds.feedburner.com"]):
            continue
        if "/blog/" in href:
            full = urljoin(base_url, href)
            if full not in links:
                links.append(full)
    return links


def parse_article(html: str, url: str, default_author: str = "ARTBOOMS"):
    soup = BeautifulSoup(html, "lxml")

    # Titolo
    title = None
    og = soup.find("meta", {"property": "og:title"})
    if og and og.get("content"): title = og["content"].strip()
    if not title:
        h1 = soup.find("h1")
        if h1: title = h1.get_text(strip=True)

    # Descrizione
    description = None
    ogd = soup.find("meta", {"property": "og:description"})
    if ogd and ogd.get("content"): description = ogd["content"].strip()
    if not description:
        p = soup.find("p")
        if p: description = p.get_text(" ", strip=True)

    # Autore
    author = default_author
    meta_author = soup.find("meta", attrs={"name": re.compile(r"author", re.I)})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"].strip()
    else:
        by = soup.find(attrs={"class": re.compile(r"author|byline", re.I)})
        if by:
            txt = by.get_text(" ", strip=True)
            if txt: author = txt

    # Date
    pub = None
    mod = None

    for key in [
        {"property":"article:published_time"},
        {"itemprop":"datePublished"},
        {"name":"pubdate"},
        {"name":"date"},
    ]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content"):
            pub = normalize_date(tag["content"]) or pub

    for key in [
        {"property":"article:modified_time"},
        {"property":"og:updated_time"},
        {"itemprop":"dateModified"},
        {"name":"lastmod"},
        {"name":"modified"},
    ]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content"):
            mod = normalize_date(tag["content"]) or mod

    # Fallback: cerca un testo data vicino all'h1
    if not pub:
        header = soup.find("h1")
        block_text = " ".join((header.find_parent().get_text(" ", strip=True) if header and header.find_parent() else soup.get_text(" ", strip=True))[:1200].split())
        m = DATE_RX.search(block_text)
        if m:
            pub = normalize_date(m.group(0))

    if (not mod) and pub:
        mod = pub

    # Contenuto per hashing (primi blocchi)
    main = soup.find("article") or soup.find("main") or soup.find("div", class_=re.compile(r"post|entry|article|content|sqs-block-content", re.I))
    content_text = None
    if main:
        content_text = main.get_text(" ", strip=True)
    else:
        content_text = soup.get_text(" ", strip=True)

    return {
        "url": url,
        "title": title or url,
        "description": description or (content_text[:280] if content_text else None),
        "author": author,
        "published": pub.isoformat() if pub else None,
        "modified": mod.isoformat() if mod else None,
        "content_text": content_text or "",
    }
```

---

## article\_processor.py

```python
import json
import os
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from article_parser import extract_article_links, parse_article

ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
DEFAULT_AUTHOR = os.environ.get("DEFAULT_AUTHOR", "ARTBOOMS")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "6"))

HEADERS = {
    "User-Agent": "artbooms-rss/1.0 (+https://www.artbooms.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _now_utc():
    return datetime.utcnow().replace(tzinfo=timezone.utc)


def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return {"items": {}, "last_scan": None}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": {}, "last_scan": None}


def _save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def fetch(url, etag=None, last_modified=None):
    headers = dict(HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    return r


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _scan_archive(force=False):
    r = fetch(ARCHIVE_URL)
    r.raise_for_status()
    links = extract_article_links(r.text, BASE_URL)
    return links


def _process_one(url: str, cache_item: dict | None, force=False):
    etag = cache_item.get("etag") if cache_item else None
    lastmod = cache_item.get("last_modified") if cache_item else None

    try:
        r = fetch(url, etag=None if force else etag, last_modified=None if force else lastmod)
    except Exception as e:
        # in caso di errore, riusa il cache_item
        return cache_item, False

    if r.status_code == 304 and cache_item:
        return cache_item, False

    if r.status_code != 200:
        # fallback: non aggiornare
        return cache_item, False

    item = parse_article(r.text, url, default_author=DEFAULT_AUTHOR)

    text_hash = _hash_text(item.get("content_text"))
    changed = text_hash != (cache_item.get("content_hash") if cache_item else None)

    headers = r.headers
    item.update({
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "content_hash": text_hash,
    })

    return item, changed


def generate_items(force=False):
    cache = _load_cache()

    links = _scan_archive(force=force)
    # Inizializza
    cache.setdefault("items", {})

    changed_any = False

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as ex:
        futures = {}
        for url in links:
            futures[ex.submit(_process_one, url, cache["items"].get(url), force)] = url
        for fut in as_completed(futures):
            url = futures[fut]
            new_item, changed = fut.result()
            if new_item:
                cache["items"][url] = new_item
                changed_any = changed_any or changed

    cache["last_scan"] = _now_utc().isoformat()
    _save_cache(cache)

    # Prepara lista ordinata per il feed
    items_list = list(cache["items"].values())

    def to_dt(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime(1970,1,1, tzinfo=timezone.utc)

    items_list.sort(key=lambda x: to_dt(x.get("published") or x.get("modified") or "1970-01-01T00:00:00+00:00"), reverse=True)

    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS – Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms con aggiornamenti automatici"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
        "build_time": _now_utc(),
    }

    # ETag calcolato sull'intero feed (verrà ricalcolato in rss_generator)
    return items_list, meta


def load_cache():
    return _load_cache()
```

---

## rss\_generator.py

```python
import hashlib
from datetime import datetime, timezone
from email.utils import format_datetime
from feedgen.feed import FeedGenerator


def _as_dt(s):
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def build_rss(items: list[dict], meta: dict):
    fg = FeedGenerator()
    fg.title(meta.get('title'))
    fg.description(meta.get('description'))
    if meta.get('self_url'):
        fg.link(href=meta['self_url'], rel='self')
    fg.link(href='https://www.artbooms.com', rel='alternate')
    fg.language(meta.get('language', 'it-IT'))

    last_modified = None

    for it in items:
        fe = fg.add_entry()
        fe.id(it.get('url'), permalink=True)
        fe.link(href=it.get('url'))
        fe.title(it.get('title') or it.get('url'))
        if it.get('description'):
            fe.description(it['description'])
        if it.get('author'):
            fe.author({'name': it['author']})

        pub = _as_dt(it.get('published'))
        mod = _as_dt(it.get('modified')) or pub
        if pub:
            fe.pubDate(format_datetime(pub))
        if mod:
            # estensioni Dublin Core / DCTerms
            fe.dc({'creator': it.get('author')})
            fe.extensions()['content'] = {}  # ensure namespace exists
            fe._FeedEntry__setitem('dcterms:modified', format_datetime(mod))
            if (last_modified is None) or (mod > last_modified):
                last_modified = mod

    # lastBuildDate del feed
    build_time = meta.get('build_time') or datetime.utcnow().replace(tzinfo=timezone.utc)
    fg.lastBuildDate(format_datetime(last_modified or build_time))

    rss_bytes = fg.rss_str(pretty=True)
    etag = hashlib.sha256(rss_bytes).hexdigest()

    headers = {
        'ETag': f'W/"{etag}"',
        'Last-Modified': format_datetime(last_modified or build_time),
        'Cache-Control': 'max-age=300',
    }

    return rss_bytes, headers
```

---

## worker.py (opzionale)

```python
import os
import time
from article_processor import generate_items

INTERVAL = int(os.environ.get('REFRESH_INTERVAL', '15'))  # minuti

if __name__ == '__main__':
    while True:
        try:
            generate_items(force=False)
        except Exception as e:
            pass
        time.sleep(INTERVAL * 60)
```

---

## requirements.txt

```
Flask==3.0.3
gunicorn==21.2.0
requests==2.32.3
beautifulsoup4==4.12.3
lxml==5.2.2
feedgen==0.9.0
python-dateutil==2.9.0.post0
tenacity==8.3.0
```

---

## Procfile

```
web: gunicorn app:app --workers 2 --threads 4 --timeout 120 --preload
```

---

## Note operative e troubleshooting

* **Se Render non parte** e vedi errore tipo *ModuleNotFoundError*: verifica che `requirements.txt` sia nella root e che lo step “Build” lo installi.
* **Se il feed è vuoto**: è possibile che l’HTML dell’archivio cambi; in `article_parser.extract_article_links` adattare il filtro (ora cerca `"/blog/"`).
* **Se mancano date**: abbiamo fallback che pesca la data vicino all’H1; se vuoi, puoi impostare manualmente un parsing più stretto.
* **Cache**: `articles_cache.json` viene aggiornato ad ogni scansione; puoi svuotarlo per forzare una scansione completa (o usare `?force=1`).
* **Validazione**: opzionale, puoi usare un validatore RSS esterno per vedere eventuali warning.
