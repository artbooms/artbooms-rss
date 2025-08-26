import json
import os
import hashlib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from article_parser import extract_article_links, parse_article

ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://www.artbooms.com/archivio-completo")
BASE_URL = os.environ.get("BASE_URL", "https://www.artbooms.com")
DEFAULT_AUTHOR = os.environ.get("DEFAULT_AUTHOR
