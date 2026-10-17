# WebSub: backend GitHub e collaudo

Il backend opzionale `WEBSUB_BACKEND=github` conserva lo stato nel file
`websub-state.json` del ramo dedicato `websub-state` di `artbooms/artbooms-rss`.
Non usa gli snapshot della cache, non scrive su main e non crea workflow.
La creazione del ramo e l'attivazione sono operazioni esplicite della
[procedura di migrazione](websub-migration.md), da approvare prima del deploy.
Il default `file` mantiene il comportamento esistente fino alla migrazione.

## Protocollo e limiti

`websub_store.py` legge HEAD e contenuto a revisione immutabile; ogni modifica
crea un commit figlio dell'HEAD letto e aggiorna il ref senza force. Due figli
concorrenti non possono entrambi sostituire il ref. Il publisher rilegge e
ricalcola al massimo tre volte dopo un conflitto. Dopo un timeout del PATCH
controlla HEAD e al massimo otto antenati: un esito non verificabile blocca
l'invio senza dichiarare una conferma. Nessun reset automatico o baseline da 404.

Il documento conserva v2 originale, GUID osservati, pending, contatori e
scadenze, più revisione, modalità, lease e ricevute terminali. Prima di ogni
GET pubblico salva il controllo e una lease di 180 secondi; dopo il GET valido
salva separatamente il tentativo POST e riconferma la proprietà prima del POST.
La risposta positiva viene dichiarata consegnata solo dopo l'ACK durevole.
Le istanze condividono la prenotazione durante deploy sovrapposti. Ricevute
accettate e GUID osservati non vengono eliminati o riaccodati.

Restano i budget esistenti: 40 controlli feed, 8 POST, scadenza fissa di 72 ore,
backoff distinto per feed e hub. Un controllo feed fallito non consuma POST.
Retry-After e reset della quota GitHub rinviano il background. Nessun ciclo
illimitato; un evento esaurito conserva una ricevuta. WebSub non offre chiavi
d'idempotenza: timeout del POST, o risposta hub ricevuta ma ACK non salvabile,
possono comportare un retry limitato. Nessuna garanzia exactly-once; una
conferma già durevole non viene ritrasmessa.

L'observer RSS passa solo i byte al background: nessuna rete GitHub da endpoint
né attesa del backend per rispondere a RSS/sitemap. Se GitHub manca, WebSub
resta sospeso, RSS e cache proseguono; alla ripresa riconcilia i GUID del feed
completo con l'archivio durevole importato. Anche dopo perdita del filesystem
riconosce solo i GUID nuovi. Gli eventi persistiti conservano budget/deadline.
Un GUID mai persistito durante un guasto è recuperabile se resta nel feed,
cache o sorgente canonica; il primo istante d'osservazione locale non è
recuperabile dopo la perdita del processo. Non si promette altrimenti.

A feed invariato: un GET del ref per ciclo di circa 120 secondi, zero commit.
Le scritture avvengono solo per eventi/transizioni; il ramo nasce senza
workflow. Le ricevute terminali `completed` possono essere rimosse dal solo
documento corrente dopo sette giorni oltre sia la conferma sia la deadline
originale, esclusivamente durante una transizione già da salvare. La storia
Git le conserva; GUID osservati, pending, budget, scadenze e ACK recenti non
vengono rimossi. La deduplicazione dipende dai GUID permanenti, non dalla
presenza indefinita della ricevuta nel documento corrente. Segnalazione nei
log a 600 e 800 KiB, con limite orario salvo aumento di livello.
Limite file 900 KiB: superato, il publisher si ferma senza troncare
stato o toccare RSS. La crescita dei GUID resta permanente, quindi anche con
questa pulizia gli avvisi richiedono un controllo futuro. GitHub mantiene
quote e disponibilità proprie. Token
scaduto o senza permessi sospende WebSub, senza fallback/reset locali.

## Credenziali

`WEBSUB_GITHUB_TOKEN`: fine-grained PAT limitato al repository, Contents
read/write e Metadata read, senza Actions/Workflows/administration. Nessun
fallback al generico GITHUB_TOKEN. Solo HTTPS api.github.com, niente redirect,
timeout e log senza token/header/body. Contents write può tecnicamente
scrivere altri rami dello stesso repository: GitHub non offre uno scope PAT
per singolo ramo. La restrizione `websub-state*` è applicativa, non uno scope
del token. Non salvare il token nel ramo. Il repository pubblico conserverà
URL e contatori, non segreti o dati privati degli abbonati.

## Collaudo riproducibile

`python -m unittest discover -s tests -p 'test_*.py' -v`

La simulazione legge la cache reale senza scriverla e usa soltanto copie.
L'articolo distinto usa HTML offline con scanner/parser reali, acquisizione,
merge cache, RSS completo, endpoint Flask e news sitemap reali. Trasporti
Squarespace/feed/hub/GitHub sono simulati; rete esterna non prevista bloccata.
La cancellazione del runtime non cancella il fake Git Data remoto. Sono
provati due istanze, crash, lease, timeout prima/dopo PATCH, ACK ambiguo,
guasti GitHub, ripartenza, deduplica, budget e migrazione. I test del vecchio
file verificano il default. Restano i controlli cache/cursore/correzioni
manuali, arte, news 48 ore/fallback e dispatch PWA. Nessuna pubblicazione reale.

`check-legacy` è il nuovo preflight Render Shell, locale e strettamente in
sola lettura: percorso, file stabile, JSON/v2, GUID/pending/retry/scadenze e
dimensione dell'importazione. Niente lock, rete, export o dati integrali in
stdout; fallisce prima del fence per stato invalido o dimensione eccessiva.
Le API reali ref/commit/contents sono state lette con HTTP 200 e versione
2022-11-28. Le scritture del backend e i conflitti sono tuttora testati con
Git Data simulato: la prova reale segregata richiede autorizzazione futura,
come indicato nella procedura. Non è già stata eseguita.

## CI e ambiente

Python Render verificato nei build log: **3.13.4**. La matrice è limitata a
Ubuntu 24/26 con 3.12.15, patch esatta 3.13.4 su 24 e 3.13.16 su 26: quattro job,
senza ripetere 3.14/3.15. setup-python non offre 3.13.4 su 26. Nessuna modifica
Python, dipendenze, build command o librerie native di produzione.

I run precedenti restano consultabili: 38824572402 ha superato sei ambienti,
incluso 3.13.4/24 (176 test), ma fallito la compilazione lxml 5.2.2 su 26/3.13+
prima dei test. Il primo profilo statico, 38825071440, ha fallito il link PIC.
Questi fallimenti non sono test passati. Il profilo CI 26/3.13 usa ora la build
statica ufficiale lxml 5.2.2 con libxml2 2.12.6, libxslt 1.1.39, zlib 1.3.2,
libiconv 1.19 e PIC. È un profilo del runner, non dimostra compatibilità con
le nuove librerie di sistema 26 né modifica Render. Esiti/versioni effettivi
dell'ultima revisione sono nei check e artefatti della PR.

Fonti: [Git refs](https://docs.github.com/en/rest/git/refs#update-a-reference),
[quote API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api),
[filesystem Render](https://render.com/docs/deploys#ephemeral-filesystem),
[skip deploy](https://render.com/docs/deploys#skipping-an-auto-deploy),
[deploy hook](https://render.com/docs/deploy-hooks),
[profilo lxml](https://raw.githubusercontent.com/lxml/lxml/lxml-5.2.2/pyproject.toml),
[build statica](https://lxml.de/5.2/build.html#building-an-egg-or-wheel).
