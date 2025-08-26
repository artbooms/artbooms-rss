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
            fe.dc({'creator': it.get('author')})
            fe.extensions()['content'] = {}
            fe._FeedEntry__setitem('dcterms:modified', format_datetime(mod))
            if (last_modified is None) or (mod > last_modified):
                last_modified = mod

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
