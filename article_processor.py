import json
import os
import logging
import re
import time
from urllib.parse import urljoin
from article_parser import fetch_html, extract_article_links_from_archive_html, parse_article

logger = logging.getLogger("article_processor")

CACHE_PATH = "cache/articles_cache.json"
ARCHIVE_URL = "https://www.artbooms.com/archivio-completo"
BASE_URL = "https://www.artbooms.com"
MAX_BATCH = 3


def load_cache():
    """Carica la cache locale."""
    if not os.path.exists(CACHE_PATH):
        return {"items": []}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"items": data}
        if "items" not in data:
            data["items"] = []
        return data
    except Exception as e:
        logger.error("Errore caricando la cache: %s", e)
        return {"items": []}


def save_cache(data):
    """Salva la cache su disco ordinata per data di pubblicazione (vecchi → nuovi)."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    items = data.get("items", [])
    items.sort(key=lambda x: x.get("published") or "")
    data["items"] = items
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("[Cache] Salvata cache con %s articoli.", len(items))


def _find_month_links(html: str):
    """
    Estrae link mese tipo /blog?month=MM-YYYY (relativi o assoluti), per QUALSIASI anno.
    Ritorna URL assoluti.
    """
    # match month=01-2025, 1-2025 (tollerante su 1 o 2 cifre per il mese)
    month_hrefs = re.findall(r'href=["\']([^"\']*month=\d{1,2}-\d{4})["\']', html, flags=re.IGNORECASE)
    # tieni solo quelli della sezione blog
    month_hrefs = [h for h in month_hrefs if "/blog" in h.lower()]
    # normalizza in assoluti + dedup mantenendo ordine
    abs_months = [urljoin(BASE_URL, h) for h in month_hrefs]
    seen = set()
    months = [u for u in abs_months if not (u in seen or seen.add(u))]
    return months


def _month_sort_key(url: str):
    """
    Converte ...month=MM-YYYY in (YYYY, MM) per un ordinamento cronologico crescente.
    Se non riconosciuto, restituisce una chiave alta così va in fondo.
    """
    m = re.search(r"month=(\d{1,2})-(\d{4})", url)
    if not m:
        return (9999, 99)
    mm, yyyy = m.groups()
    try:
        return (int(yyyy), int(mm))
    except Exception:
        return (9999, 99)


def _extract_article_links_from_month(month_url: str):
    """
    Dato un link mese, estrae i link reali degli articoli:
    - devono contenere /blog/
    - niente query (?)
    - niente /tag/
    Restituisce URL assoluti, deduplicati.
    """
    mhtml = fetch_html(month_url)
    raw = extract_article_links_from_archive_html(mhtml, month_url)
    # filtra articoli validi
    articles = []
    for l in raw:
        L = l.lower()
        if "/blog/" not in L:
            continue
        if "?" in L:          # evitiamo nuovamente query
            continue
        if "/tag/" in L:
            continue
        articles.append(urljoin(BASE_URL, l))

    # dedup preservando ordine
    seen = set()
    return [u for u in articles if not (u in seen or seen.add(u))]


def extract_all_articles():
    """
    Legge l'archivio completo, trova i link mese e, per ciascun mese, estrae i link articolo.
    Mantiene l'ordine mese → mese (2016 → ... → oggi) SENZA hardcodare anni.
    """
    html = fetch_html(ARCHIVE_URL)
    month_links = _find_month_links(html)
    logger.info("[Parser] %s link mese trovati nell'archivio.", len(month_links))

    # Se per qualsiasi motivo non troviamo mesi, fallback: prendi eventuali link articoli dalla pagina principale.
    if not month_links:
        raw = extract_article_links_from_archive_html(html, ARCHIVE_URL)
        fallback = []
        for l in raw:
            L = l.lower()
            if "/blog/" in L and "?" not in L and "/tag/" not in L:
                fallback.append(urljoin(BASE_URL, l))
        seen = set()
        uniq = [u for u in fallback if not (u in seen or seen.add(u))]
        logger.warning("[Parser] Nessun link mese trovato — fallback con %s link articoli diretti.", len(uniq))
        return uniq

    # Ordina i mesi cronologicamente (vecchi → nuovi)
    month_links_sorted = sorted(month_links, key=_month_sort_key)

    all_articles = []
    seen = set()
    for mlink in month_links_sorted:
        try:
            arts = _extract_article_links_from_month(mlink)
            for a in arts:
                if a not in seen:
                    all_articles.append(a)
                    seen.add(a)
            logger.info("[Parser] %s articoli trovati in %s", len(arts), mlink)
            time.sleep(0.5)  # gentilezza verso Squarespace
        except Exception as e:
            logger.warning("Errore estraendo articoli da %s: %s", mlink, e)

    logger.info("[Parser] Totale articoli estratti da tutti i mesi: %s", len(all_articles))
    return all_articles


def fetch_article_dates(links):
    """Ordina i link in base alla data di pubblicazione reale (fallback alla coda)."""
    results = []
    for link in links:
        try:
            art = parse_article(link)
            pub = art.get("published")
            if pub:
                results.append((link, pub))
            else:
                # senza published: mandalo in fondo
                results.append((link, "9999-12-31T00:00:00Z"))
        except Exception as e:
            logger.warning("Errore leggendo data per %s: %s", link, e)
    results.sort(key=lambda x: x[1])  # vecchi → nuovi
    return [r[0] for r in results]


def generate_items():
    """Scarica articoli e aggiorna la cache con controllo 'modified' (batch da MAX_BATCH)."""
    try:
        logger.info("[Processor] Scarico archivio da %s", ARCHIVE_URL)
        all_links = extract_all_articles()
        logger.info("[Parser] %s link articolo validi trovati in totale.", len(all_links))
    except Exception as e:
        logger.error("Errore scaricando archivio: %s", e)
        return

    if not all_links:
        logger.warning("Nessun articolo trovato, interrompo il ciclo.")
        return

    cache = load_cache()
    cached = {a["url"]: a for a in cache.get("items", [])}
    new_articles = []

    # Ordina gli URL per data reale (vecchi → nuovi)
    ordered_links = fetch_article_dates(all_links)

    for link in ordered_links:
        art = parse_article(link)
        if not art or not art.get("title"):
            continue

        cached_art = cached.get(link)
        if not cached_art:
            logger.info("[Processor] NUOVO articolo: %s", link)
            new_articles.append(art)
        else:
            old_mod = cached_art.get("modified")
            new_mod = art.get("modified")
            if new_mod and old_mod and new_mod != old_mod:
                logger.info("[Processor] Articolo AGGIORNATO: %s (modified)", link)
                cached[link] = art
                new_articles.append(art)

        if len(new_articles) >= MAX_BATCH:
            break

    if not new_articles:
        logger.info("Nessun nuovo o aggiornato articolo trovato.")
        return

    # Aggiorna cache (sostituisci/aggiungi per URL)
    for art in new_articles:
        cached[art["url"]] = art

    cache["items"] = list(cached.values())
    save_cache(cache)

    logger.info("[Processor] Aggiunti/aggiornati %s articoli (totale %s).",
                len(new_articles), len(cache["items"]))
