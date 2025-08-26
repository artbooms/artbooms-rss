# Panoramica

Questo drop‑in genera **/rss.xml** (alias **/feed**) leggendo l’archivio completo di Artbooms e aggiorna automaticamente le voci quando cambiano.

**Novità richieste**

* Scansione **incrementale** (non apre tutti i link insieme): batch configurabile.
* Per ogni articolo: **titolo, link canonico, autore, descrizione, immagine, data pubblicazione, data modifica**.
* Rilevamento modifiche con **ETag/Last‑Modified** + **hash del contenuto**.
* Feed **RSS 2.0** con `atom:link rel="self"`, `dc:creator`, `dcterms:modified`, `enclosure` per l’immagine.
* Endpoint: `/rss.xml`, `/feed`, `/healthz`; parametro `?force=1` per rigenerare.

---

## 1) File da MANTENERE (o sostituire)

Copia i contenuti delle sezioni sotto nei file omonimi del repo (sostituisci tutto):

* `app.py`
* `article_parser.py`
* `article_processor.py`
* `rss_generator.py`
* `worker.py` *(opzionale)*
* `requirements.txt`
* `Procfile`

## 2) File da ELIMINARE

* `Dokerfile` e/o `Dockerfile` (useremo Procfile su Render).

*(Il file `articles_cache.json` verrà creato/aggiornato automaticamente.)*

## 3) Variabili d’ambiente (Render > Environment)

Imposta così per il tuo caso:

* `ARCHIVE_URL=https://www.artbooms.com/archivio-completo`
* `BASE_URL=https://www.artbooms.com`
* `SELF_FEED_URL=https://artbooms-rss.onrender.com/rss.xml`
* `DEFAULT_AUTHOR=ARTBOOMS`
* `REQUEST_TIMEOUT=20`
* `MAX_CONCURRENCY=6`
* `REFRESH_INTERVAL=15`  # minuti (solo worker)
* `BATCH_SIZE=12`         # numero max di articoli processati per richiesta
* `STALE_HOURS=12`        # dopo quante ore un articolo è considerato "da ricontrollare"

## 4) Start command (Render)

Se Render non legge il Procfile, metti nel campo “Start Command”:

```
gunicorn app:app --workers 2 --threads 4 --timeout 120 --preload
```

## 5) Test

* Apri `/healthz`.
* Apri `/rss.xml` (o `/feed`).
* Usa `/rss.xml?force=1` per forzare una rigenerazione.

---

## app.py

```python
import os
from flask import Flask, Response, request, jsonify, make_response
from datetime import datetime, timezone
from article_processor import generate_items
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

# Mappa mesi italiani -> inglese per dateparser
MONTHS_IT = {
    "gennaio":"January","febbraio":"February","marzo":"March","aprile":"April","maggio":"May","giugno":"June",
    "luglio":"July","agosto":"August","settembre":"September","ottobre":"October","novembre":"November","dicembre":"December",
    "gen":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","mag":"May","giu":"Jun","lug":"Jul","ago":"Aug","set":"Sep","ott":"Oct","nov":"Nov","dic":"Dec",
}

DATE_RX = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|" + "|".join(MONTHS_IT.keys()) + r")[\s\-]+\d{1,2},?[\s\-]+\d{4}\b", re.I)


def _normalize_date(txt: str):
    if not txt:
        return None
    s = txt.strip()
    for it, en in MONTHS_IT.items():
        s = re.sub(rf"\b{re.escape(it)}\b", en, s, flags=re.I)
    try:
        return dateparser.parse(s)
    except Exception:
        return None


def extract_article_links(archive_html: str, base_url: str):
    """Dalla pagina archivio prende i link agli articoli (contengono '/blog/')."""
    soup = BeautifulSoup(archive_html, "lxml")
    seen = set()
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        if any(x in href for x in ["facebook.com","instagram.com","x.com","twitter.com","pinterest.com","feeds.feedburner.com"]):
            continue
        if "/blog/" in href:
            full = urljoin(base_url, href)
            if full not in seen:
                seen.add(full)
                out.append(full)
    return out


def parse_article(html: str, url: str, default_author: str = "ARTBOOMS"):
    soup = BeautifulSoup(html, "lxml")

    # Canonical
    canonical = soup.find("link", rel="canonical")
    link = canonical.get("href").strip() if canonical and canonical.get("href") else url

    # Titolo
    title = None
    ogt = soup.find("meta", {"property": "og:title"})
    if ogt and ogt.get("content"): title = ogt["content"].strip()
    if not title and soup.title: title = soup.title.get_text(strip=True)

    # Descrizione
    description = None
    ogd = soup.find("meta", {"property": "og:description"})
    if ogd and ogd.get("content"): description = ogd["content"].strip()
    if not description:
        meta_desc = soup.find("meta", attrs={"name":"description"})
        if meta_desc and meta_desc.get("content"): description = meta_desc["content"].strip()

    # Immagine (priorità: og:image > itemprop image > thumbnailUrl > link rel=image_src)
    image = None
    for selector in [
        ("meta", {"property":"og:image"}, "content"),
        ("meta", {"itemprop":"image"}, "content"),
        ("meta", {"itemprop":"thumbnailUrl"}, "content"),
        ("link", {"rel":"image_src"}, "href"),
    ]:
        tag = soup.find(selector[0], attrs=selector[1])
        if tag and tag.get(selector[2]):
            image = tag.get(selector[2]).strip()
            break

    # Autore
    author = default_author
    meta_author = soup.find("meta", attrs={"itemprop":"author"}) or soup.find("meta", attrs={"name":"author"})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"].strip()

    # Date
    pub = None
    mod = None
    for key in [
        {"itemprop":"datePublished"},
        {"property":"article:published_time"},
        {"name":"pubdate"},
    ]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content") and not pub:
            pub = _normalize_date(tag["content"]) or pub

    for key in [
        {"itemprop":"dateModified"},
        {"property":"article:modified_time"},
        {"property":"og:updated_time"},
        {"name":"lastmod"},
    ]:
        tag = soup.find("meta", attrs=key)
        if tag and tag.get("content") and not mod:
            mod = _normalize_date(tag["content"]) or mod

    # Fallback near H1
    if not pub:
        h1 = soup.find("h1")
        block = (h1.find_parent().get_text(" ", strip=True) if h1 and h1.find_parent() else soup.get_text(" ", strip=True))
        m = DATE_RX.search(block)
        if m:
            pub = _normalize_date(m.group(0))
    if not mod and pub:
        mod = pub

    # Testo per hashing
    main = soup.find("article") or soup.find("main") or soup.find("div", class_=re.compile(r"post|entry|article|content|sqs-block-content", re.I))
    content_text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    return {
        "url": link or url,
        "title": title or url,
        "description": description or (content_text[:280] if content_text else None),
        "author": author,
        "image": image,
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from article_parser import extract_article_links, parse_article

ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
DEFAULT_AUTHOR = os.environ.get("DEFAULT_AUTHOR", "ARTBOOMS")
CACHE_PATH = os.environ.get("CACHE_PATH", "articles_cache.json")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "6"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "12"))
STALE_HOURS = int(os.environ.get("STALE_HOURS", "12"))

HEADERS = {
    "User-Agent": "artbooms-rss/1.0 (+https://www.artbooms.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _now():
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


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def _fetch(url, etag=None, last_modified=None):
    headers = dict(HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    return r


def _scan_archive():
    r = _fetch(ARCHIVE_URL)
    r.raise_for_status()
    links = extract_article_links(r.text, BASE_URL)
    return links


def _select_batch(cache, links):
    # assicura che ogni link esista in cache
    for url in links:
        cache["items"].setdefault(url, {})

    # ordina: prima non visitati, poi più vecchi
    def score(u, it):
        checked = it.get("checked_at")
        if not checked:
            return datetime(1970,1,1, tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(checked)
        except Exception:
            return datetime(1970,1,1, tzinfo=timezone.utc)

    items = sorted(((u, cache["items"][u]) for u in links), key=lambda t: score(t[0], t[1]))

    # filtra per staleness
    stale_before = _now() - timedelta(hours=STALE_HOURS)
    batch = []
    for url, it in items:
        if len(batch) >= BATCH_SIZE:
            break
        checked = it.get("checked_at")
        if (not checked) or (datetime.fromisoformat(checked) < stale_before):
            batch.append(url)
    # se sfortunatamente nulla è stale, processa comunque i primi N
    if not batch:
        batch = [u for u,_ in items[:BATCH_SIZE]]
    return batch


def _process_one(url, cache_item, force=False):
    etag = None if force else cache_item.get("etag") if cache_item else None
    lastmod = None if force else cache_item.get("last_modified") if cache_item else None

    try:
        r = _fetch(url, etag=etag, last_modified=lastmod)
    except Exception:
        return cache_item, False

    if r.status_code == 304 and cache_item:
        cache_item["checked_at"] = _now().isoformat()
        return cache_item, False

    if r.status_code != 200:
        return cache_item, False

    item = parse_article(r.text, url, default_author=DEFAULT_AUTHOR)
    text_hash = _hash_text(item.get("content_text"))
    changed = text_hash != (cache_item.get("content_hash") if cache_item else None)

    headers = r.headers
    item.update({
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "content_hash": text_hash,
        "checked_at": _now().isoformat(),
    })
    return item, changed


def generate_items(force=False):
    cache = _load_cache()
    cache.setdefault("items", {})

    links = _scan_archive()
    batch = links if force else _select_batch(cache, links)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as ex:
        futures = {ex.submit(_process_one, url, cache["items"].get(url), force): url for url in batch}
        for fut in as_completed(futures):
            url = futures[fut]
            new_item, _ = fut.result()
            if new_item:
                cache["items"][url] = new_item

    cache["last_scan"] = _now().isoformat()
    _save_cache(cache)

    # lista completa per il feed (anche gli articoli non appena aggiornati restano in feed con i dati noti)
    items_list = list(cache["items"].values())

    def to_dt(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime(1970,1,1, tzinfo=timezone.utc)

    items_list.sort(key=lambda x: to_dt(x.get("modified") or x.get("published") or "1970-01-01T00:00:00+00:00"), reverse=True)

    meta = {
        "self_url": os.environ.get("SELF_FEED_URL", ""),
        "title": os.environ.get("FEED_TITLE", "ARTBOOMS – Archivio completo"),
        "description": os.environ.get("FEED_DESCRIPTION", "Tutti gli articoli di Artbooms con aggiornamenti automatici"),
        "language": os.environ.get("FEED_LANGUAGE", "it-IT"),
    }

    return items_list, meta
```

---

## rss\_generator.py

```python
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element, SubElement
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib

NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'dcterms': 'http://purl.org/dc/terms/',
    'media': 'http://search.yahoo.com/mrss/'
}
for p, uri in NS.items():
    ET.register_namespace(p, uri)


def _as_dt(s):
    if isinstance(s, datetime):
        return s
    if not s:
        return None
    try:
        s = s.replace('Z', '+00:00')
        return datetime.fromisoformat(s)
    except Exception:
        return None


def build_rss(items, meta):
    rss = Element('rss', attrib={'version': '2.0', 'xmlns:atom': NS['atom'], 'xmlns:dc': NS['dc'], 'xmlns:dcterms': NS['dcterms'], 'xmlns:media': NS['media']})
    ch = SubElement(rss, 'channel')

    SubElement(ch, 'title').text = meta.get('title')
    SubElement(ch, 'link').text = 'https://www.artbooms.com'
    SubElement(ch, 'description').text = meta.get('description')

    self_url = meta.get('self_url')
    if self_url:
        SubElement(ch, f"{{{NS['atom']}}}link", attrib={'href': self_url, 'rel': 'self', 'type': 'application/rss+xml'})

    last_mod = None

    for it in items:
        item = SubElement(ch, 'item')
        link = it.get('url')
        SubElement(item, 'title').text = it.get('title') or link
        SubElement(item, 'link').text = link
        SubElement(item, 'guid', attrib={'isPermaLink': 'true'}).text = link

        if it.get('description'):
            SubElement(item, 'description').text = it.get('description')
        if it.get('author'):
            SubElement(item, f"{{{NS['dc']}}}creator").text = it.get('author')

        pub = _as_dt(it.get('published'))
        mod = _as_dt(it.get('modified')) or pub
        if pub:
            SubElement(item, 'pubDate').text = format_datetime(pub)
        if mod:
            SubElement(item, f"{{{NS['dcterms']}}}modified").text = format_datetime(mod)
            if (last_mod is None) or (mod > last_mod):
                last_mod = mod

        # immagine come enclosure e media:content
        img = it.get('image')
        if img:
            SubElement(item, 'enclosure', attrib={'url': img, 'type': 'image/jpeg'})
            SubElement(item, f"{{{NS['media']}}}content", attrib={'url': img, 'medium': 'image'})

    # lastBuildDate
    build = last_mod or datetime.utcnow().replace(tzinfo=timezone.utc)
    SubElement(ch, 'lastBuildDate').text = format_datetime(build)

    xml_bytes = ET.tostring(rss, encoding='utf-8')
    etag = hashlib.sha256(xml_bytes).hexdigest()
    headers = {
        'ETag': f'W/"{etag}"',
        'Last-Modified': format_datetime(build),
        'Cache-Control': 'public, max-age=300'
    }
    return xml_bytes, headers
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
        except Exception:
            pass
        time.sleep(INTERVAL * 60)
```

---

## requirements.txt

```
Flask==3.0.3
gunicorn==22.0.0
requests==2.32.3
beautifulsoup4==4.12.3
lxml==5.2.2
python-dateutil==2.9.0.post0
tenacity==8.3.0
```

---

## Procfile

```
web: gunicorn app:app --workers 2 --threads 4 --timeout 120 --preload
```

---

## Note

* **Indentazione**: nei blocchi sopra non ci sono spazi/tab prima di `import` o a livello modulo. Copia‑incolla *così com’è*.
* **Incrementale**: per ogni richiesta al feed processiamo max `BATCH_SIZE` articoli (di default 12). Al resto si arriva dalle richieste successive o con il `worker`.
* **Immagini**: incluse sia come `enclosure` che `media:content` (MRSS) per compatibilità ampia con i reader.
