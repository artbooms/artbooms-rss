# Artbooms RSS

Servizio Flask per il feed RSS e la news sitemap di Artbooms. Questo progetto aggiorna il repository esistente artbooms/artbooms-rss.

## Avvio e aggiornamenti

Render avvia `gunicorn app:app`, che legge `gunicorn.conf.py`. Un solo popolatore per istanza controlla l'archivio, aggiorna gradualmente gli articoli e pubblica il feed senza richieste di scraping da parte dei lettori. Le scritture JSON/XML sono atomiche e protette da lock. Un errore temporaneo conserva i dati validi e il feed precedente.

Gli articoli nuovi hanno priorita; una quota del batch mantiene in movimento la scansione degli altri articoli anche se alcune pagine falliscono. Il ciclo attende normalmente 120 secondi dopo il lavoro precedente: questo intervallo non garantisce tempi di consegna agli aggregatori.

La sitemap legge prima la cache locale aggiornata e usa GitHub come recupero. Include al massimo 1000 elementi News, pubblicati nelle ultime 48 ore, con data originale. Se non ci sono articoli recenti conserva un URL ordinario senza metadati News.

`persist_cache.yml` e attivato dal trigger esterno gia esistente: non introduce un cron aggiuntivo. Scarica e fonde la cache con l'ultima main, protegge schede valide e correzioni manuali e salva soltanto il JSON. Se cambia la cache avvia PWA Memory, che nel proprio repository avvia OneSignal. `keepalive.yml` mantiene attivo il repository. I due workflow RSS usano Ubuntu 24.04.

## Indirizzi

- RSS canonico: https://rss.artbooms.com/rss
- Alias con lo stesso feed: /rss.xml e /feed.xml
- News sitemap: https://rss.artbooms.com/news-sitemap.xml
- Cache per persistenza: /cache/download
- Stato del processo: /healthz

URL/GUID degli articoli e identita del feed rimangono gli stessi. La firma editoriale puntuale e gestita in `editorial_authors.py`; le categorie esplicite in `editorial_taxonomy.py`.

## Aggiornare il servizio esistente

Caricare i file modificati insieme su un branch e verificare il diff prima di unirli a main. Non sovrascrivere o cancellare `cache/articles_cache.json`: e una risorsa viva. Il pacchetto distribuibile esclude quel file proprio per evitare di arretrare gli aggiornamenti. Il bootstrap puo recuperare la cache corrente da GitHub.

La configurazione Render reale distribuisce automaticamente solo main; anteprime PR disabilitate. Il workflow `verify-rss.yml` esegue soltanto test isolati sul branch codex/verifica-rss-2026-10-06: non chiama il servizio reale, non pubblica feed e non avvia notifiche.

## Collaudo su Linux

Python 3.12, dipendenze in requirements.txt e requirements-test.txt:

```sh
python -m pip install -r requirements.txt -r requirements-test.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

Le prove Gunicorn e di leadership richiedono Linux; le prove Git usano repository locali temporanei. I risultati originali e quelli della nuova revisione sono separati nel resoconto della consegna.

## Verifiche dopo il rilascio

Verificare deploy Live, RSS e alias, sitemap, una nuova pubblicazione e la persistenza successiva. Verificare la ricezione reale in Feedly e l'invio della sitemap in Search Console, con proprieta che copra rss.artbooms.com e www.artbooms.com. Il vecchio URL onrender.com richiede l'abilitazione del sottodominio nelle impostazioni Render: il codice non supera un blocco di routing.

Un RSS leggibile e una sitemap valida aiutano la scoperta e la conservazione dei contenuti; non garantiscono l'indicizzazione, l'inclusione in Google News o impressioni Discover.
