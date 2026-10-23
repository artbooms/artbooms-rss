# ARTBOOMS — finalizzazione del 23–24 ottobre 2026

Proposta nella PR #10, ancora da autorizzare per la produzione.

## Problemi riscontrati e correzioni

- **54 avvisi Unavailable nelle prime 22 ore UTC del 23 ottobre.** Il codice
  precedente eliminava la causa dal log: non è possibile distinguere
  retroattivamente timeout, TLS, HTTP o autenticazione. I nuovi log riportano
  soltanto operazione, categoria, HTTP e Retry-After, senza token o corpi.
  Un errore di connessione/timeout su GET può ricevere un solo ritentativo.
  Nessun ritentativo automatico aggiuntivo di POST/PATCH; HTTP e rate limit
  conservano il cooldown esistente. Gli errori TLS non sono ritentati.
- **Letture GitHub a feed invariato e coda vuota.** Il solo controllo periodico
  inattivo passa da circa 120 a 600 secondi. Nuovi byte RSS, pending, pausa,
  errori, nuova configurazione o nuovo processo richiedono lo stato autorevole.
  La memoria locale non autorizza invii: prenotazioni, lease, budget, deadline,
  CAS e ACK persistenti restano quelli esistenti. Riduzione teorica dell'80%
  dei controlli inattivi, non dell'intero traffico.
- **POST XML ripetuti a ogni avvio.** Eliminato il catch-up automatico al
  riavvio/deploy. L'esperimento XML diventa opt-in:
  `NEWS_SITEMAP_WEBSUB_ENABLED=1`. Per impostazione predefinita non genera
  richieste né pubblicizza un hub XML. Se attivato, richiede contenuti realmente
  aggiornati e pronti, metadati News obbligatori e data valida con fuso;
  chiude entrambe le risposte HTTP. Non è stata creata una seconda persistenza.
- **Cache scaricata senza compressione richiesta.** Il workflow usa
  `curl --compressed`; il JSON viene decompresso prima del merge protetto.
- **Deploy del solo timestamp di manutenzione.** Render conferma un deploy
  causato dal keepalive del 19 ottobre. Aggiunto `[skip render]` al suo commit.

Il solo header `X-Artbooms-Revision` su /healthz permetterà di confrontare
la versione realmente servita con il commit del deploy, senza cambiare il JSON.

## Misure e verifiche precedenti all'attivazione

| Misura | Risultato |
| --- | --- |
| Render 22 ottobre, giornata UTC completa | 251,00 MB |
| Render 23 ottobre, prime 22 ore UTC | 232,07 MB |
| Cache integra | 1.164.813 byte, 1.141 articoli |
| Trasferimento gzip misurato | 322.632 byte |
| Trasferimento reale con curl --compressed | 317.099 byte, JSON integro |
| Stato RSS remoto | active, revisione 9, 1.141 GUID, zero pending, due accepted |
| Commit effettivamente live prima delle modifiche | 7b536df832ff24cf1afac4cb5728293bacbd387a |

Nel campione di 100 accessi tra le 17:19 e le 22:06 UTC: 22 download cache,
21 letture RSS del client con user-agent Chrome/123 e 21 del notifier PWA.
Gli altri accessi includono health check, crawler e sitemap. È un campione,
non una ricostruzione completa del traffico. Non è stata analizzata la PWA.

Per 96 download/giorno, dimensione costante e confronto gzip: circa 80,85 MB/giorno
di payload in meno, 2,43 GB in 30 giorni, circa il 32% della baseline giornaliera
del 22 ottobre. Il risparmio effettivamente contabilizzato da Render va misurato
dopo l'attivazione. Non si somma un secondo risparmio per la compressione già
offerta al pubblico; non si attribuiscono risparmi a 304 che i client non chiedono.

## Limite Google e scelta tecnica

Il 23 ottobre l'hub registra RSS recuperato alle 09:03:53 CEST e news sitemap
alle 09:09:45 CEST. Questo verifica il recupero dell'hub, non una scansione
Googlebot verificata, sottoscrittori Google News o indicizzazione. Gli access log
disponibili non consentono la verifica retroattiva degli IP.

Google documenta WebSub per RSS/Atom e l'invio XML tramite Search Console/API:
<https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap>.
Per le news chiede una sitemap aggiornata allo stesso URL e mantiene il controllo
della frequenza di scansione:
<https://developers.google.com/search/docs/crawling-indexing/sitemaps/news-sitemap>.

La soluzione supportata entro l'infrastruttura attuale resta il WebSub RSS durevole
più la news sitemap sempre aggiornata, già sottoposta a Search Console.
Non si promette un controllo immediato della lettura XML. La chiamata in sola
lettura a GSC Wizard è stata rifiutata per assenza di abbonamento: nessun acquisto,
nuova credenziale o invio Search Console è stato effettuato.

## Collaudo e attivazione

I 256 test della prima versione della PR sono passati nel run 40028221880.
Sono aggiunti 16 casi mirati su idle, nuovi articoli, pending, pausa/ripresa,
riavvio dopo ACK, segreti nei log, recupero GET, TLS e isolamento XML.
La CI della PR esegue l'intera suite su Ubuntu 24.04, Python 3.12.15 e 3.13.4.
Il test locale Gunicorn /proc non è stato modificato per aggirare i PID virtualizzati.

RSS integrale, metadati, cache, scansione circa 120 secondi, snapshot 15 minuti,
workflow/dispatch PWA, DNS, Squarespace e formato dello stato WebSub non cambiano.

Render ha Auto-Deploy On Commit e preview PR disattivate. Dopo autorizzazione:
merge con messaggio normale senza `[skip render]`, un solo deploy automatico,
controllo dello SHA live e dell'header di /healthz, RSS/news/cache, stato e log.
Non richiamare anche il deploy manuale. Gli snapshot e il keepalive successivi
mantengono il marcatore e non devono produrre altri deploy.

Ripristino, se necessario: revert delle sole modifiche applicative conservando
il backend GitHub e lo stato corrente; nessun reset di main/cache/stato o migrazione
inversa. Compressione del workflow e marcatore keepalive possono essere conservati.
