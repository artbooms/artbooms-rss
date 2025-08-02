# Usa un'immagine base con Python
FROM python:3.10-slim

# Imposta la directory di lavoro
WORKDIR /app

# Copia i file del progetto
COPY . /app

# Installa le dipendenze
RUN pip install --no-cache-dir flask requests beautifulsoup4 gunicorn

# Esponi la porta
EXPOSE 8080

# Comando per avviare l'app con gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
