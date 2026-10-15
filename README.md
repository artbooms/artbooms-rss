# Artbooms RSS

Servizio Flask per il feed RSS e la news sitemap di Artbooms. Questo progetto aggiorna il repository esistente artbooms/artbooms-rss.

## Avvio e aggiornamenti

Render avvia `gunicorn app:app`, che legge `gunicorn.conf.py`. Un solo popolatore per istanza controlla l'archivio, aggiorna gradualmente gli articoli e pubblica il feed senza richieste di scraping da parte dei lettori. Le scritture JSON/XML sono atomiche e protette da lock. Un errore temporaneo conserva i dati validi e il feed precedente.

Gli articoli nuovi hanno priorita; una quota del batch mantiene in movimento la scansione degli altri articoli anche se alcune pagine falliscono. Il ciclo attende normalmente 120 secondi dopo il lavoro precedente: questo intervallo non garantisce tempi di consegna agli aggregatori.

La sitemap legge prima la cache locale aggiornata e usa GitHub come recupero: un nuovo articolo acquisito compare alla richiesta successiva, senza attendere la persistenza remota. Include al massimo 1000 elementi News, pubblicati nelle ultime 48 ore, con data originale. Se non ci sono articoli recenti conserva un URL ordinario senza metadati News.

Ogni articolo RSS ha una sola categoria generale, `arte`. Anche il campo opzionale `news:keywords` contiene `arte`: e ammesso dallo [schema XML ufficiale](https://www.google.com/schemas/sitemap-news/0.9/sitemap-news.xsd), ma la documentazione Google attuale non ne garantisce l'utilizzo per la classificazione.

## WebSub

Il feed dichiara l'[hub Google](https://pubsubhubbub.appspot.com/) con `atom:link rel="hub"`, mantenendo il self canonico. Dopo una scrittura XML valida e atomica, il servizio registra solo i GUID nuovi in uno stato separato. App e publisher usano lo stesso resolver: `WEBSUB_STATE_PATH` se impostato e non vuoto, altrimenti `CACHE_PATH + ".websub.json"`. La variabile sceglie un percorso, senza creare o configurare storage durabile. Il popolatore leader controlla con un GET che il feed pubblico risponda 200, dichiari hub/self e contenga tutti i GUID dell'evento; soltanto allora invia un POST form con `hub.mode=publish` e `hub.url=https://rss.artbooms.com/rss`. Le richieste RSS non contattano l'hub.

Timeout di connessione/lettura: 3/10 secondi. Ogni evento ha una scadenza fissa di 72 ore e due budget persistiti: fino a 40 GET totali del feed (anche quelli riusciti) e fino a 8 POST all'hub. I GET falliti non consumano POST. I controlli del feed attendono almeno 2, 5, 15, 30, 60, 120 minuti, poi 120 minuti; i retry dell'hub attendono almeno 15 minuti, 1, 2, 4, 8, 12 e 24 ore. Con feed disponibile gli otto POST coprono circa 51 ore e 15 minuti. Il ciclo esistente del popolatore puo allungare queste attese. `Retry-After` puo prolungarle, senza anticipare i retry o oltrepassare la scadenza dell'evento.

Le prenotazioni GET e POST vengono salvate separatamente prima delle rispettive richieste. Contatori e scadenza non si azzerano dopo un riavvio. Timeout, HTTP 408/425/429 e 5xx dell'hub sono ritentabili; gli altri errori 4xx e i redirect sono terminali e loggati. Eventi scaduti o esauriti vengono rimossi dai pending mantenendo i GUID osservati: nessun nuovo ciclo automatico per gli stessi articoli. Solo eventi mai controllati/inviati e creati da meno di cinque minuti possono essere accorpati, senza estendere la scadenza. Lock separati impediscono invii concorrenti. Le risposte 2xx sono accettazioni dell'hub, senza garanzia di consegna ai lettori. Guasti dell'hub o dello stato vengono registrati e non modificano cache, XML o risposte HTTP dell'RSS.

Al primo avvio senza stato, l'archivio costituisce la baseline senza notifiche. Uno stato conservato mantiene eventi in attesa, tentativi e GUID gia osservati attraverso i riavvii sullo stesso filesystem. Lo schema v1 migra atomicamente a v2 una sola volta, mantenendo GUID, ID e contatore precedente: poiche v1 mescolava GET e POST, quel contatore viene trattato conservativamente come POST gia consumati. Un pending v1 con tre tentativi non confermati resta entro il nuovo limite complessivo di otto, senza azzeramenti. I pending gia rimossi da v1 non possono essere ricostruiti dai soli GUID osservati.

Un deploy che perda i file locali ricrea la baseline senza ritrasmettere l'archivio; gli eventi non confermati del vecchio stato non sono recuperabili. Anche un restart Render puo sostituire l'istanza e perdere il filesystem temporaneo. Cambiare `WEBSUB_STATE_PATH` non trasferisce automaticamente il JSON precedente: un eventuale trasferimento richiede una procedura coordinata per evitare di ripristinare ACK arretrati. Uno stato corrotto viene conservato e disabilita gli invii finche non viene ripristinato. La persistenza storica della cache resta invariata.

WebSub non fornisce una chiave di idempotenza publisher: un timeout dopo l'accettazione o un crash prima del salvataggio della conferma puo produrre un POST duplicato nel retry limitato. I normali controlli, ricostruzioni, riavvii e cambiamenti dei metadati di GUID esistenti non generano nuovi eventi.

### Alternative di persistenza da approvare separatamente

La correzione del percorso e i retry non modificano Render, GitHub Actions o `persist_cache.yml`. Quest'ultimo salva soltanto la cache degli articoli. Un percorso nel [filesystem temporaneo Render](https://render.com/docs/deploys#ephemeral-filesystem) non garantisce la conservazione dello stato ai deploy.

| Alternativa | Beneficio | Limite/rischio |
| --- | --- | --- |
| Disco persistente dedicato al solo JSON WebSub | Minima estensione del codice, cache degli articoli separata | [Render limita a una istanza e interrompe brevemente ogni deploy](https://render.com/docs/disks). Aggiungere il disco avvia un deploy; snapshot arretrati possono ripetere POST. |
| Datastore esterno dedicato al solo stato WebSub | GUID, pending e contatori durabili senza un disco nel servizio RSS | Nuova infrastruttura/costi, credenziali e codice per transazioni e claim atomici tra istanze sovrapposte; guasti dello store devono restare isolati dall'RSS. |
| Snapshot GitHub periodici separati | Riutilizza una piattaforma esistente | Finestra di perdita fra snapshot, ACK arretrati e duplicati possibili, regole di merge complesse. Richiede modifiche ai workflow; commit su main possono avviare auto-deploy. |

Per mantenere deploy senza interruzioni, un datastore dedicato con transazioni/claim e l'alternativa preferibile da valutare in un intervento separato. Un [Key Value con persistenza](https://render.com/docs/key-value#data-persistence) ha comunque una finestra di possibile perdita delle ultime scritture. Nessuna alternativa elimina l'ambiguita di un POST gia accettato senza ACK salvato, in assenza di idempotenza dell'hub.

## Scoperta della news sitemap

Il sottodominio esterno e consentito con la [cross-submission ufficiale Google](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap#cross-submit): proprieta Search Console verificate che coprano entrambi gli host e invio della sitemap, oppure un riferimento nel robots.txt del sito che ospita gli articoli. La sola accessibilita dell'XML su rss.artbooms.com non dimostra questa associazione.

La verifica pubblica del 15 ottobre 2026 ha trovato solo la sitemap Squarespace in `www.artbooms.com/robots.txt`. La registrazione Search Console resta da verificare separatamente con autorizzazione; questa modifica non cambia Search Console o Squarespace. Il canale ufficiale per segnalare una sitemap XML e Search Console o la sua [API sitemaps.submit](https://developers.google.com/webmaster-tools/v1/sitemaps/submit), senza garanzia di lettura immediata. WebSub notifica l'RSS/Atom, non la news sitemap. Nessun vecchio ping Google o Indexing API viene utilizzato.

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

La configurazione Render reale distribuisce automaticamente solo main; anteprime PR disabilitate. Il workflow `verify-rss.yml` esegue soltanto test isolati sui branch codex/verifica-rss-2026-10-06 e codex/websub-arte-2026-10-15: non chiama il servizio reale, non pubblica feed e non avvia notifiche. La cronologia completa rende disponibile la baseline fissata dai test di migrazione.

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
