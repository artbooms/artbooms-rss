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

    last_mod_
