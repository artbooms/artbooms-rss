import time
import logging
from article_processor import generate_items

logger = logging.getLogger("worker")

# quanto attendere tra un batch e l'altro (in secondi)
INTERVAL = 60  # 1 minuto
# quante iterazioni prima di fermarsi (per sicurezza)
MAX_RUNS = 500

def run():
    """
    Worker di background che richiama generate_items() a intervalli regolari.
    Così la cache si popola poco alla volta, senza dover ricaricare /rss.
    """
    logger.info("Worker avviato: popolamento graduale del feed in corso...")
    runs = 0
    while runs < MAX_RUNS:
        runs += 1
        try:
            items, meta = generate_items(force=False)
            logger.info(f"[{runs}] Cache aggiornata ({len(items)} articoli)")
        except Exception as e:
            logger.exception("Errore durante il popolamento incrementale: %s", e)
        time.sleep(INTERVAL)

    logger.info("Worker terminato (raggiunto limite iterazioni)")

if __name__ == "__main__":
    run()
