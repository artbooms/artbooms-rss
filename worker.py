import os
import time
import logging
from article_processor import generate_items

logger = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO)

INTERVAL_MIN = int(os.environ.get("REFRESH_INTERVAL_MIN", "15"))

if __name__ == "__main__":
    logger.info("Worker started: interval %s min", INTERVAL_MIN)
    while True:
        try:
            generate_items(force=False)
        except Exception:
            logger.exception("Errore worker generate_items")
        time.sleep(INTERVAL_MIN * 60)
