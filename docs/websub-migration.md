# Migrazione manuale dello stato WebSub su GitHub

Queste istruzioni preparano il passaggio dal file locale v2 al ramo dedicato `websub-state`. Non eseguono merge o deploy. Prima dell'esecuzione servono l'approvazione dei risultati della PR e un momento concordato per la migrazione. Il feed RSS e l'acquisizione continuano durante il fence; soltanto le notifiche WebSub restano ferme fino all'attivazione esplicita.

Lo stato iniziale **deve provenire dal file vivo del servizio**: conserva tutti i GUID osservati, gli eventi pending, i contatori GET/POST e le scadenze originali. Non usare la cache articoli, una copia vecchia del file o il feed pubblico per ricostruirlo. Se il file vivo è assente, illeggibile, corrotto o v1, la CLI si ferma. Non eliminare il file per aggirare l'errore.

## 1. Fence prima del deploy, dalla Render Shell

La shell deve appartenere all'istanza che esegue il publisher legacy. Non riavviare né deployare mentre il suo stato locale non è stato esportato e verificato su GitHub. Preparare prima la credenziale autorizzata e verificare la disponibilità di GitHub. **Il fence locale e la scrittura GitHub non sono una transazione atomica fra servizi**: un deploy/restart nella finestra può perdere il file effimero. È presente un deploy hook esterno; questa PR non lo modifica. Concordare la finestra operativa prima di procedere e non lasciare una pausa umana fra fence ed upload. I comandi seguenti sono una procedura manuale dopo approvazione, non sono stati eseguiti dall'agente.

Per poter usare la CLI prima che il nuovo codice sia distribuito, prendere **i soli due moduli revisionati da un commit preciso approvato**, salvarli in `/tmp` e mantenere `PYTHONPATH` anche sul checkout del servizio. Non utilizzare una URL di branch mobile o il testo di una PR non ancora approvata.

```bash
cd /opt/render/project/src
umask 077
export PYTHONPATH="/tmp:$PWD"
curl --fail --silent --show-error --location \
  https://raw.githubusercontent.com/artbooms/artbooms-rss/COMMIT_APPROVATO/scripts/migrate_websub_state.py \
  --output /tmp/migrate_websub_state.py
curl --fail --silent --show-error --location \
  https://raw.githubusercontent.com/artbooms/artbooms-rss/COMMIT_APPROVATO/websub_store.py \
  --output /tmp/websub_store.py
export WEBSUB_STATE_REPOSITORY=artbooms/artbooms-rss
export WEBSUB_STATE_BRANCH=websub-state
export WEBSUB_STATE_FILE=websub-state.json
```

Se `WEBSUB_STATE_PATH` è configurata, usare il suo percorso effettivo al posto dell'esempio seguente. La CLI non sceglie automaticamente un file alternativo. Il blocco usa le funzioni della CLI revisionata: chiede il token **prima** del fence, poi esegue immediatamente fence, backup privato e importazione in pausa, senza trasferimenti intermedi o attese umane.

```bash
python - <<'PY'
import getpass, json, os, sys, uuid
import migrate_websub_state as migration
from websub_store import GitHubStore
token = getpass.getpass("Token GitHub (input nascosto): ")
store = GitHubStore(os.environ["WEBSUB_STATE_REPOSITORY"],
                    os.environ["WEBSUB_STATE_BRANCH"],
                    os.environ["WEBSUB_STATE_FILE"], token)
migration_id = str(uuid.uuid4())
print("migration_id:", migration_id, flush=True)
try:
    fence = migration.fence_legacy_state("cache/articles_cache.json.websub.json", migration_id)
    document = migration.build_document(fence)
    migration.export_document(document, "/tmp/websub-migration-backup.json")
    snapshot = migration.initialize_remote(store, document, allow_initialize=True)
    print(json.dumps(migration._summary(snapshot.document, snapshot.head), sort_keys=True))
except Exception as error:
    print("Migrazione non confermata (%s): nessuna attivazione; conservare backup e ID" %
          type(error).__name__, file=sys.stderr)
    sys.exit(1)
PY
```

Conservare il `migration_id` UUID stampato dal comando: non è un segreto. Ripetere ogni comando successivo con quello stesso ID. Se il token/API non funziona, il blocco si ferma senza attivare WebSub e senza ripristinare automaticamente il locale. Se l'istanza o il deploy cambia durante il trasferimento, **non attivare**: conservare l'export e verificare quale commit remoto sia stato effettivamente confermato. Non ricostruire una baseline dal feed.

Il fence aspetta fino a 60 secondi, senza bloccare il lock di stato, per acquisire `STATE_PATH.publish.lock`. Un POST già in corso finisce prima della copia. Poi prende brevemente il lock di stato, legge il v2 valido e sostituisce atomicamente il file con `version: "migration-fence-v1"`, `migration_id`, copia integrale `legacy_state` e checksum. Rilascia entrambi i lock prima di qualunque operazione GitHub. Se l'attesa scade non scrive il fence. Il publisher e l'observer legacy rifiutano questa versione e gestiscono l'errore senza interrompere RSS o cache; non creano una nuova baseline.

```bash
python /tmp/migrate_websub_state.py status \
  --state-path cache/articles_cache.json.websub.json --migration-id ID_CONSERVATO
```

Il backup contiene GUID e stato editoriale, **non credenziali**. È creato con permessi privati, fuori dalla directory `cache`, e non sovrascrive un backup esistente. Dopo la conferma dell'upload, trasferire una copia tramite un canale privato; non incollarlo nei log, aggiungerlo alla PR o pubblicare un endpoint per scaricarlo. `/tmp` e il disco locale Render sono effimeri: completare la copia durevole prima di qualsiasi restart/deploy. Se GitHub è indisponibile, lasciare il fence e mettere immediatamente al sicuro l'export privato, poi riprendere dagli stessi file/ID; RSS continua finché l'istanza rimane disponibile.

## 2. Verifica dell'importazione e ripresa, sempre in pausa

Il blocco precedente importa già lo stato. Per controllare il remoto, o riprendere dopo un errore temporaneo, usare la stessa destinazione e lo stesso backup dal checkout completo approvato oppure dalla Render Shell preparata. Esempio dal checkout:

```bash
export WEBSUB_STATE_REPOSITORY=artbooms/artbooms-rss
export WEBSUB_STATE_BRANCH=websub-state
export WEBSUB_STATE_FILE=websub-state.json
python scripts/migrate_websub_state.py upload \
  --input /PERCORSO_PRIVATO/websub-migration-backup.json --initialize-branch
python scripts/migrate_websub_state.py status
```

La CLI chiede il token con `getpass`, a input nascosto, se `WEBSUB_GITHUB_TOKEN` non è già una variabile segreta dell'ambiente. Non passare token come argomento, non salvarlo in file, cron, history o comandi `export TOKEN=...`. Usare una credenziale limitata al repository e ai permessi Contents necessari; la sua configurazione nel servizio è un'azione manuale separata e autorizzata. Il blocco iniziale mantiene il token soltanto nella memoria del processo Python.

`--initialize-branch` autorizza soltanto la creazione esplicita del ramo dedicato se assente. Il ramo nasce da un commit root con il solo file di stato, senza workflow né cache. L'envelope è `format: 1`, topic RSS canonico, `state` v2 integrale, `completed: {}`, `mode: "paused"`, stesso `migration_id`, `revision: 0`. La CLI rilegge il contenuto per verificarlo. Un ramo esistente viene accettato solo se identico; un errore, un'autorizzazione mancante o uno stato diverso/più recente non provoca mai reset o sovrascrittura. Dopo una risposta di creazione persa, ripetere la stessa importazione con lo stesso backup/ID, senza cambiare ramo per aggirare il controllo.

## 3. Configurazione e attivazione separate

Dopo l'importazione verificata, configurare manualmente `WEBSUB_BACKEND=github` e le tre variabili di destinazione precedenti nel servizio, più `WEBSUB_GITHUB_TOKEN` come segreto. Il deploy richiede ancora approvazione esplicita. Il nuovo codice deve trovare il remoto esistente; un remoto assente o non valido deve lasciare WebSub fermo senza ricreare la baseline. Dopo `resume`, il publisher confronta anche i nuovi GUID entrati nel feed durante il fence con l'archivio importato, conservando tutti i budget originali.

Solo dopo questi controlli e l'autorizzazione all'attivazione:

```bash
python scripts/migrate_websub_state.py resume \
  --migration-id ID_CONSERVATO --confirm-activation
python scripts/migrate_websub_state.py status
```

`resume` cambia solo la modalità con compare-and-swap e incrementa la revisione; non azzera pending, scadenze, lease, contatori o completamenti. Un conflitto richiede una nuova lettura, non un retry cieco. Il protocollo WebSub non offre una chiave d'idempotenza publisher: un POST accettato seguito da timeout resta ambiguo; la migrazione non promette consegna exactly-once.

## Comandi di gestione e rollback

```bash
python scripts/migrate_websub_state.py pause --migration-id ID_CONSERVATO
python scripts/migrate_websub_state.py status
python scripts/migrate_websub_state.py export --output /PERCORSO_PRIVATO/websub-backup-nuovo.json
```

`pause` è persistente e conserva lo stato; un invio già autorizzato/in corso può completarsi. `status` stampa soltanto ID, modalità, revisione, head e conteggi. L'export remoto conserva il documento attuale e serve da backup, non da sorgente per reinizializzare un ramo avanzato.

**Prima dell'attivazione soltanto**, se il remoto è ancora paused/revision 0, senza nuove transizioni e con stato identico al fence, è possibile annullare:

Il comando deve essere eseguito nella shell della stessa istanza legacy con accesso al backend revisionato. Se il nuovo codice non è ancora distribuito, scaricare anche **il solo modulo di archiviazione dallo stesso commit approvato** in `/tmp`, senza sostituire i file del servizio:

```bash
cd /opt/render/project/src
curl --fail --silent --show-error --location \
  https://raw.githubusercontent.com/artbooms/artbooms-rss/COMMIT_APPROVATO/websub_store.py \
  --output /tmp/websub_store.py
PYTHONPATH="/tmp:$PWD" python /tmp/migrate_websub_state.py rollback-fence \
  --state-path PERCORSO_DEL_FILE_VIVO --migration-id ID_CONSERVATO
```

Usare la medesima destinazione GitHub non segreta del punto 2; il token viene chiesto dal prompt nascosto. Prima registra su GitHub, con CAS, `cancelled: true`, `mode: "paused"`, revisione 1; il nuovo backend e `resume` rifiutano la riattivazione. Solo dopo conferma remota ripristina il v2 integrale sotto gli stessi lock. Un crash fra le due fasi si recupera ripetendo il comando con lo stesso ID. Se GitHub non risponde o lo stato è avanzato, il fence resta conservato.

Dopo l'attivazione o qualunque nuova transizione, **non ripristinare il file locale**, non fare force-push/reset del ramo e non reimportare snapshot vecchi. Per il rollback applicativo mantenere codice capace di usare il backend GitHub, mettere il remoto in pausa e conservare tutta la storia. Non tornare a una release che usa soltanto il file effimero locale. Credenziali, backend e eventuale riattivazione richiedono la rispettiva autorizzazione manuale; non esporre endpoint di amministrazione pubblici.
