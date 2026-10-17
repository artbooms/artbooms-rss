# Attivazione WebSub: procedura definitiva e punti di arresto

**Documento da approvare, non autorizzazione ad agire.** Nessuna operazione di produzione descritta qui è stata eseguita. Non iniziare dal merge. Il codice è predisposto; prima dell'attivazione servono una prova GitHub reale autorizzata, il controllo dello stato vivo e il blocco dei trigger di deploy.

## Sequenza semplice: chi fa cosa

1. **Tu approvi separatamente la finestra operativa e i cambi temporanei Render.** Il chiamante del deploy hook delle 09:03 UTC resta sconosciuto: non ci sono prove che sia cron-job.org. Auto-Deploy Off, da solo, non blocca il hook. La procedura richiede di autorizzare espressamente Settings → Auto-Deploy → Off e Settings → Deploy Hook → Regenerate Hook, senza comunicare il nuovo URL ad alcuna automazione. Questo invalida il vecchio URL, senza toccare il cronjob GitHub dei 15 minuti. Non usare GET/POST sul vecchio hook come test: se fosse ancora valido provocherebbe un deploy. Non usare sospensione o maintenance mode: interromperebbero l'RSS. Nessun cambiamento viene eseguito ora.
2. **Tu crei il token solo dopo autorizzazione; noi verifichiamo le API reali prima del fence.** Fine-grained PAT per il solo repository artbooms/artbooms-rss, Contents lettura/scrittura e Metadata lettura, senza Actions, Workflows o amministrazione. Usare un prompt nascosto, mai incollare il token in chat, URL, file o comandi. La prova isolata dell'appendice B deve riuscire: non invia notifiche e non tocca lo stato vivo. In caso di errore ci si ferma qui.
3. **Tu apri la Render Shell del servizio; noi controlliamo che non ci siano deploy in corso o accodati e registriamo deploy/istanza correnti.** Gli eventuali build/deploy già avviati prima del blocco devono essere terminati prima di iniziare. Nessun collega o altro sistema deve richiedere deploy/API/restart durante il trasferimento. L'RSS deve restare HTTP 200. Non modificare cronjob, PWA, cache, DNS o Squarespace.
4. **Tu incolli il controllo in sola lettura dell'appendice A. La verifica è automatica.** Deve riportare versione 2, percorso corretto, un numero plausibile di GUID (almeno i 1.139 già osservati nell'audit, salvo verifica successiva), pending, budget e scadenze. Non stampa URL, GUID o contenuto integrale. File assente, JSON corrotto, archivio vuoto, v1, deadline incoerente o dimensione incompatibile fanno fallire il comando: nessun fence, esportazione o importazione deve partire. Un successo non congela lo stato; sarà riletto sotto lock.
5. **Dopo il via libera, tu esegui il blocco C nella stessa istanza; le verifiche e l'importazione sono automatiche.** Il token è richiesto prima del fence. Lo script ripete il preflight, attende l'eventuale invio in corso, conserva il v2 integrale in un fence e in un backup privato, importa subito su GitHub in modalità paused e verifica il contenuto esatto. Non azzera GUID, contatori o scadenze. Se API/importazione non sono confermate, STOP: niente merge, variabili o deploy. Preservare backup e migration_id; non ricreare una baseline.
6. **Solo dopo importazione verificata, tu configuri Environment con Save only.** Impostare WEBSUB_BACKEND=github, WEBSUB_STATE_REPOSITORY=artbooms/artbooms-rss, WEBSUB_STATE_BRANCH=websub-state, WEBSUB_STATE_FILE=websub-state.json e WEBSUB_GITHUB_TOKEN come segreto. Non scegliere Save and deploy o Save, rebuild, and deploy. Non premere Restart service: Render riparte dal commit/config già in esecuzione, non dalle modifiche Save only. Il publisher vecchio resta sul file con fence; quello nuovo non è ancora avviato. Non cambiare cache/cursore/WEBSUB_STATE_PATH.
7. **Dopo autorizzazione al merge/deploy, noi uniamo la PR e tu avvii un solo deploy manuale del nuovo commit approvato.** Auto-Deploy resta Off durante questa fase, quindi né merge né snapshot possono anticipare il deploy. Non distribuire il vecchio commit. Noi verifichiamo SHA effettivo, backend github, remoto paused valido, completamento deploy e fine della vecchia istanza, RSS/news/health HTTP 200, archivio completo, logs e PWA. Non attivare se compare una nuova baseline locale o se i controlli falliscono.
8. **Tu esegui resume dello stesso migration_id, soltanto dopo verifica; noi osserviamo i cicli successivi.** Comando nell'appendice D. I pending conservano le scadenze originali; nessun articolo o POST artificiale. Il publisher rileva anche i GUID acquisiti durante la pausa e salva le transizioni subito, senza attendere il salvataggio dei 15 minuti. Un token errato o GitHub assente sospende solo WebSub.
9. **Dopo verifica dell'attivazione, tu ripristini Auto-Deploy On Commit previa autorizzazione.** Render può riallinearsi al commit main più recente: un eventuale ulteriore deploy deve essere atteso e verificato, ora con stato durevole. I successivi snapshot Persist cache restano marcati [skip render]. Non distribuire automaticamente il nuovo hook: il ripristino del chiamante giornaliero richiede identificarlo e una decisione separata. Il cronjob dei 15 minuti e la PWA non sono stati disabilitati o modificati.

### Limite operativo da non nascondere

Rigenerare il hook e disabilitare l'auto-deploy impedisce i due trigger noti (vecchio URL e nuovi commit), non un crash hardware o un restart deciso dalla piattaforma/amministratore. Il primo trasferimento fra filesystem effimero legacy e GitHub non è una transazione atomica. Se cambia istanza prima della verifica dell'importazione, STOP: niente attivazione; conservare il backup e verificare lo stato remoto/locale. Il vecchio codice non riconosce il backend GitHub: un suo rimpiazzo prima del deploy nuovo potrebbe ricreare una baseline locale. Non si può promettere l'eliminazione di questo rischio con il solo codice ancora non distribuito. Senza il blocco autorizzato e una finestra controllata la migrazione non è pronta per iniziare. Il fence della vecchia istanza e il remoto paused impediscono i publisher concorrenti nel trasferimento pianificato.

## Appendice A — preparazione e controllo davvero in sola lettura

Dopo aver approvato uno SHA preciso della PR, sostituire COMMIT_APPROVATO con quello SHA. La preparazione scarica soltanto gli strumenti in /tmp; non sostituisce file del servizio. Il comando check-legacy seguente legge soltanto: -B impedisce anche la scrittura di bytecode Python.

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
python -B /tmp/migrate_websub_state.py check-legacy
```

Il percorso rispetta WEBSUB_STATE_PATH; altrimenti CACHE_PATH + .websub.json. Se necessario usare --state-path PERCORSO_VERIFICATO. Non indovinare un altro file se manca quello configurato. Output: versione, dimensioni originali e dell'importazione, checksum, identità file, conteggi, retry totali e scadenze minime/massime (timestamp UTC Unix). Nessun lock, file creato, rete, export, fence o lista GUID. Tre letture al massimo in caso di sostituzioni concorrenti; poi errore e STOP. Il fence rilegge e valida nuovamente il v2 prima di scrivere.

## Appendice B — prova GitHub reale, solo dopo autorizzazione

**Non ancora eseguita.** Le letture ref/commit/contents con API 2022-11-28 sono state provate realmente con HTTP 200. Creazione ramo, scritture e conflitti del backend sono stati finora verificati con trasporto simulato; la credenziale di produzione non è stata creata o collaudata. Il blocco seguente crea esclusivamente un ramo di prova root, senza workflow o cache, tiene mode paused e non chiama alcun publisher/hub. Richiede un'autorizzazione separata alla creazione di questo oggetto, oltre al token. Non eseguirlo oggi senza tale autorizzazione.

```bash
python -B - <<'PY'
import getpass, uuid
from websub_store import GitHubStore, Conflict
suffix = uuid.uuid4().hex
branch = 'websub-state-check-' + suffix
print('Ramo isolato da annotare:', branch, flush=True)
token = getpass.getpass('Token GitHub (input nascosto): ')
a = GitHubStore('artbooms/artbooms-rss', branch, 'websub-state.json', token)
b = GitHubStore('artbooms/artbooms-rss', branch, 'websub-state.json', token)
doc = {'format': 1, 'topic': 'https://rss.artbooms.com/rss', 'state':
       {'version': 2, 'observed_guids': [], 'pending': []}, 'completed': {},
       'mode': 'paused', 'migration_id': str(uuid.uuid4()), 'revision': 0}
a.initialize(doc)
first, stale = a.load(), b.load()
next_doc = dict(first.document, revision=1)
written = a.commit(first, next_doc)
try:
    b.commit(stale, dict(stale.document, revision=2))
except Conflict:
    pass
else:
    raise RuntimeError('Conflitto CAS non rilevato: STOP')
current = a.load()
assert current.document == next_doc and current.head == written.head
assert a.commit(current, current.document).head == current.head
assert a.load().head == current.head
print('OK: lettura, scrittura, CAS e no-op verificati; nessun POST hub')
PY
```

Un errore richiede STOP prima del fence; non proseguire aggiungendo permessi a caso. Eliminare poi **solo il ramo di prova annotato** dalla pagina Branches GitHub, previo controllo del nome. Non cancellare o riscrivere websub-state. La prova reale non simula timeout GitHub, crash o ACK hub: quelle condizioni restano coperte dai test offline. Nessuna notifica artificiale è necessaria.

## Appendice C — trasferimento vivo, dopo tutti i controlli

Solo nella finestra già protetta e con preflight riuscito. Nessuna pausa umana fra fence e upload; se la shell cade non rilanciare da zero con un ID diverso. Il backup è privato e non può essere riscritto: se esiste già, verificare la migrazione precedente.

```bash
export WEBSUB_STATE_REPOSITORY=artbooms/artbooms-rss
export WEBSUB_STATE_BRANCH=websub-state
export WEBSUB_STATE_FILE=websub-state.json
python -B - <<'PY'
import getpass, json, os, sys, uuid
import migrate_websub_state as migration
from websub_store import GitHubStore
preflight = migration.check_legacy_state()
print(json.dumps(preflight, sort_keys=True), flush=True)
token = getpass.getpass('Token GitHub (input nascosto): ')
store = GitHubStore(os.environ['WEBSUB_STATE_REPOSITORY'],
                    os.environ['WEBSUB_STATE_BRANCH'], os.environ['WEBSUB_STATE_FILE'], token)
migration_id = str(uuid.uuid4())
print('migration_id:', migration_id, flush=True)
try:
    fence = migration.fence_legacy_state(preflight['path'], migration_id)
    document = migration.build_document(fence)
    migration.export_document(document, '/tmp/websub-migration-backup.json')
    snapshot = migration.initialize_remote(store, document, allow_initialize=True)
    print(json.dumps(migration._summary(snapshot.document, snapshot.head), sort_keys=True))
except Exception as error:
    print('Trasferimento non confermato (%s): STOP, nessun deploy o attivazione' %
          type(error).__name__, file=sys.stderr)
    sys.exit(1)
PY
```

Il lock publisher aspetta un POST già in corso; poi il lock di stato dura solo per la copia/fence atomica, non durante HTTP. Il vecchio observer e publisher rifiutano migration-fence-v1 senza ricreare stato né interrompere RSS/cache. Il documento remoto è il v2 integrale, revision 0, paused, completed vuoto. Non esistono inizializzazione automatica, fallback locale o sovrascrittura di uno stato differente/più recente. Preservare il backup fuori da Render appena importato; non pubblicarlo in PR, log, chat o endpoint. /tmp non è durevole.

Se l'upload ha avuto una risposta incerta ma il backup e l'istanza sono conservati, riprendere soltanto con lo stesso backup/ID e la stessa destinazione. Non ripetere il blocco iniziale con un UUID nuovo:

```bash
python -B /tmp/migrate_websub_state.py upload \
  --input /tmp/websub-migration-backup.json --initialize-branch
python -B /tmp/migrate_websub_state.py status
```

Uno stato remoto differente o più recente viene rifiutato, mai sostituito.

## Appendice D — controllo, attivazione e rollback

Nella nuova Render Shell, dopo il deploy corretto e con le variabili configurate:

```bash
python -B scripts/migrate_websub_state.py status
python -B scripts/migrate_websub_state.py resume \
  --migration-id ID_CONSERVATO --confirm-activation
python -B scripts/migrate_websub_state.py status
```

Eseguire resume soltanto dopo aver verificato status paused, ID e conteggi attesi, backend github e vecchia istanza terminata/fenced. Non stampare token. Una pausa conserva tutto e permette soltanto il completamento dell'ACK di un invio già in corso:

```bash
python -B scripts/migrate_websub_state.py pause --migration-id ID_CONSERVATO
```

**Rollback prima del nuovo deploy e prima di qualsiasi attivazione/transizione:** remoto ancora paused/revision 0, identico al fence. Nella stessa istanza legacy:

```bash
python -B /tmp/migrate_websub_state.py rollback-fence \
  --state-path PERCORSO_VERIFICATO --migration-id ID_CONSERVATO
```

Prima registra cancelled/revision 1 su GitHub con CAS e lo verifica, poi ripristina il v2 originale sotto lock. Crash fra le due fasi: ripetere con lo stesso ID. Se GitHub manca o lo stato è avanzato, nessun ripristino cieco: fence conservato. Ripristinare impostazioni/automazioni richiede una decisione separata, perché riabilitare l'auto-deploy può distribuire il main più recente.

**Dopo il nuovo deploy o l'attivazione:** non ripristinare una release che ignora GitHub, non fare force-push, non cancellare il ramo e non reimportare backup vecchi. Mettere WebSub in pausa e mantenere l'adattatore durevole mentre si ripristina solo l'eventuale codice applicativo guasto. RSS/news/cache non sono stati riscritti da questa PR. Un ritorno letterale al vecchio publisher effimero non è un rollback garantito senza perdita di stato.

## Scadenze e capacità

- Pending già registrati: deadline originale immutabile. Dopo oltre 72 ore di indisponibilità GitHub, alla ripresa quelli scaduti ricevono status expired, senza POST e senza riaccodamento del GUID. Questo significa che la notifica non è consegnata; non si presenta il recupero del servizio come recupero di quella notifica.
- GUID nuovi mai registrati durante il guasto: se ancora nel feed/cache/sorgente canonica, diventano un evento al recupero, con 72 ore da quel momento. Il primo istante locale perso non è ricostruibile. Nessuna nuova baseline storica.
- GET che termina alla deadline non autorizza POST; POST iniziato prima della deadline può salvare la risposta positiva dopo. Lease e budget persistiti non vengono resettati.
- completed è alleggerito solo su una scrittura già necessaria, almeno sette giorni dopo sia la ricevuta sia la deadline originale. GUID osservati, pending, retry e ricevute recenti restano intatti; la storia Git conserva le ricevute archiviate. Nessuna scrittura ogni due minuti per fare pulizia.
- Segnalazioni preventive nei log a 600 e 800 KiB, al massimo una volta all'ora salvo aumento di livello. Limite 900 KiB invariato; un superamento ferma solo WebSub, senza troncare dati o limitare l'RSS. I GUID osservati continuano a crescere: gli avvisi richiedono controllo futuro, la compattazione non elimina quel limite. Un remoto già fuori limite non viene corretto distruttivamente.

Le frequenze restano circa 120 secondi per acquisizione/feed e 15 minuti per snapshot cache/dispatch PWA. WebSub salva le proprie transizioni sincronicamente nel background, senza attendere quello snapshot.

Fonti ufficiali: [deploy e restart Render](https://render.com/docs/deploys), [hook e rigenerazione](https://render.com/docs/deploy-hooks), [Save only e variabili](https://render.com/docs/configure-environment-variables), [Git refs e CAS non forzato](https://docs.github.com/en/rest/git/refs#update-a-reference).
