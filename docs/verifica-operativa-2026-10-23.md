# ARTBOOMS: verifiche e riduzione banda, 23 ottobre 2026

Questa proposta contiene due modifiche circoscritte ai workflow. Non modifica
il codice dei publisher, lo stato WebSub, gli articoli o le frequenze di scansione.
Merge e modifiche alla produzione richiedono l'autorizzazione dell'editrice.

## Verifiche pubbliche in sola lettura

Alle 23:32–23:38 CEST del 23 ottobre:

- RSS pubblico HTTP 200, 1.141 articoli, GUID unici coincidenti con la cache valida;
  parsing RSS riuscito, metadati hub/self presenti.
- News sitemap pubblica HTTP 200, articolo del 23 ottobre alle 09:02 +02:00,
  header Link self/hub presenti. XML valido contro entrambi gli schemi locali
  Sitemap e Google News, importati insieme come nella suite esistente.
- Stato RSS remoto letto tramite GitHub, senza scritture: `mode: active`, revisione
  9, 1.141 GUID osservati, zero pending, due ricevute `accepted`.
- Render applica già gzip/Brotli quando il client li richiede.

Questi controlli non attestano una scansione Google News o l'efficacia del topic
WebSub sperimentale della sitemap. Il codice della PR #9 effettua un solo
tentativo, senza stato persistente o retry: non è una coda durevole come l'RSS.

## Modifiche proposte

1. `persist_cache.yml`: `curl --compressed` per il download della cache.
   Curl negozia la compressione e scrive JSON già decompresso, compatibile con
   l'attuale merge protetto. Non cambia frequenza, retry, contenuti o dispatch PWA.
2. `keepalive.yml`: aggiunta di `[skip render]` al messaggio del commit settimanale,
   secondo la convenzione già utilizzata dagli snapshot della cache. Il workflow
   resta attivo; il solo aggiornamento del timestamp non deve causare un deploy.

Riferimento ufficiale per `--compressed`:
<https://curl.se/docs/manpage.html#--compressed>.

## Misura riproducibile del trasferimento

Cache integra: 1.164.813 byte e 1.141 articoli. Un GET con gzip ha trasferito
322.632 byte; un GET con `curl --compressed` ha trasferito 317.099 byte e scritto
1.164.813 byte di JSON valido, con gli stessi 1.141 URL del repository.
Il secondo valore dipende anche dagli algoritmi supportati dal client.

Il confronto gzip corrisponde a circa il 72,3% in meno per quel payload. A 96
download/giorno e dimensione costante per 30 giorni: circa 3,35 GB senza
compressione contro 0,93 GB con gzip, circa 2,43 GB risparmiati. È una stima dei
soli payload di questo workflow, non del consumo complessivo o della fattura Render.

## Collaudo e limiti ancora aperti

- Suite esistente: 256 test eseguiti localmente, 255 riusciti e un errore nel
  controllo `/proc/<pid>/status` del test Gunicorn di failover. Ripetendo solo
  il modulo, lo stesso controllo fallisce prima di verificare la morte del leader.
  L'ambiente locale espone PID del processo e `/proc/self` differenti. Non è stato
  modificato il test; il suo risultato su Ubuntu GitHub Actions resta necessario.
- Il download reale con `--compressed` ha già verificato decompressione, JSON
  leggibile, numero articoli e insieme degli URL.
- I controlli Render su deploy, log e consumi richiedono l'identificazione dello
  workspace esposto dal connector (`My Workspace`, info@artbooms.com).
- Il messaggio operativo ricevuto si interrompe all'inizio del punto 2: occorre
  acquisirne il seguito prima di dichiarare conclusa l'intera finalizzazione.

## Applicazione e ripristino

Prima del merge, verificare i risultati CI e ottenere l'autorizzazione alla
produzione. Unire soltanto i file della PR, preservando gli snapshot successivi
di `main`. Questi interventi riguardano i workflow: non richiedono un nuovo deploy
del servizio Python. Il merge va marcato `[skip render]` per evitarlo.

Dopo il merge, controllare il successivo normale run di persistenza: download,
merge protetto e dispatch devono mantenere il comportamento esistente. Nessun
dispatch manuale o POST agli hub serve per provare il risparmio.

Ripristino: rimuovere `--compressed` se un client non supportasse la risposta
negoziata. La convenzione `[skip render]` del keepalive può essere mantenuta.
Non riportare indietro cache o stato WebSub e non rifare la migrazione.
