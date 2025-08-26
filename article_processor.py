import json
import os
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
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
    return da
