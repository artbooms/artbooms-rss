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
