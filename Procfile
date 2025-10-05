# Processo web (serve il feed RSS)
web: gunicorn app:app --workers 2 --threads 4 --timeout 120 --preload

# Processo worker (popola la cache gradualmente in background)
worker: python worker.py
