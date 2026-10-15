# WebSub: prova isolata e decisione di persistenza

Questa modifica aggiunge esclusivamente collaudo e CI. Non configura storage,
non cambia dipendenze, runtime Render, articoli, cache, cursore o PWA.

## Collaudo

`python -m unittest discover -s tests -p 'test_*.py' -v`

Il nuovo test `test_websub_simulation.py` legge l'archivio completo senza
scriverlo. Crea in cartelle temporanee una copia della cache e una copia di
Ruth Asawa con URL e data di prova distinti. Acquisizione, salvataggio cache,
costruzione RSS, endpoint Flask e news sitemap usano il codice effettivo.
La lettura dell'archivio Squarespace e il parser sono simulati; i loro test
esistenti rimangono nella suite. GET del feed pubblico e POST all'hub sono
simulati e qualsiasi connessione socket o HTTP non prevista viene bloccata.

Le prove coprono disponibilità pubblica prima del POST, conferma, timeout,
503/429, Retry-After, crash prima/durante/dopo invii e scadenza. Ogni deploy
simulato elimina la cartella runtime, ripristina una copia esplicita della
cache degli articoli e conserva una directory separata per lo stato WebSub.
GUID, pending, contatori e scadenze non vengono rigenerati. Una conferma
persistita impedisce nuovi invii; un timeout ambiguo può produrre un retry
limitato. Non è una garanzia di consegna esattamente una volta.

Questo prova il comportamento **se lo storage sopravvive**. Non dimostra che
il filesystem attuale di Render sia durevole, né che un backup vecchio sia
un ripristino sicuro. PWA e lo script di dispatch restano coperti dai test
di persistenza esistenti, con curl simulato locale e senza dispatch reali.

## Versioni

Produzione verificata dai log: Python **3.13.4**, scelto dal default Render.
La CI prova Python 3.12.15, 3.13.16, 3.14.8 e 3.15.0 su Ubuntu 24.04 e
26.04, più 3.13.4 su 24.04. Il manifest ufficiale setup-python non pubblica
3.13.4 per 26.04. Non si cambia il Python di produzione.

Le dipendenze sono quelle fissate in requirements. lxml 5.2.2 non ha wheel
PyPI per CPython 3.13 e successivi: la CI installa i soli header di compilazione
XML mancanti nei runner, poi esegue pip check e registra interprete, pacchetti
e versioni native. Una wheel lxml già in cache Render non dimostra che una
ricostruzione pulita funzionerà. Gli esiti della matrice, compresi eventuali
fallimenti sulle nuove versioni, devono restare visibili.

## Decisione di storage ancora necessaria

Il deploy hook del 15 ottobre alle 11:03 CEST ha ricreato la baseline. Il solo
WEBSUB_STATE_PATH non risolve la perdita di un filesystem temporaneo.

La proposta preferita, se è richiesta continuità durante i deploy, è un piccolo
Postgres dedicato al solo stato WebSub: documento v2 con revisione, prenotazioni
atomiche e coordinamento fra vecchia e nuova istanza. Indicazione di costo:
circa $6,30/mese (compute $6 + esempio storage 1 GB $0,30), da confermare nel
preventivo dell'account prima della creazione. Nessuna risorsa è autorizzata o
creata da questa PR.

Un disco da 1 GB costa indicativamente $0,25/mese e richiederebbe meno codice,
ma introduce interruzioni del servizio a ogni deploy. GitHub può essere uno
store durevole autentico solo con un protocollo sincrono, ramo dedicato,
aggiornamenti condizionali e prenotazioni condivise; la copia ogni 15 minuti
può perdere pending o ripristinare eventi già confermati e non è equivalente.

Prima di migrare occorre leggere lo stato vivo e trasferirlo in modo coerente,
senza bloccare il normale RSS e senza azzerare tentativi o scadenze. Gli accessi
disponibili nell'audit non permettono ancora di leggere il file privato.
Nessun nuovo deploy per il solo collaudo deve precedere questa valutazione.

Fonti: [filesystem Render](https://render.com/docs/deploys#ephemeral-filesystem),
[limiti dei dischi](https://render.com/docs/disks#disk-limitations-and-considerations),
[versioni Python Render](https://render.com/docs/python-version),
[manifest setup-python](https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json),
[scenari di costo ufficiali](https://render.com/articles/production-rails-hosting-guide#comparing-representative-cost-scenarios).
