# News publication: readiness and bounded recovery

RSS and News sitemap read the same local article cache. An acquired article is
available in both without waiting for the GitHub cache workflow. The News
window remains 48 hours from the original publication instant.

The XML sitemap topic remains an **experimental** use of Google's WebSub hub.
An accepted notification and a subsequent hub GET are not evidence of Google
News subscribing to the topic, parsing the sitemap or distributing an article.
Neither a separate hub project nor simultaneous POSTs force Google's processing
schedule. Official sitemap submission remains Search Console/its API.

Previously a failed XML-topic attempt was forgotten after the changed-article
batch. Subsequent unchanged cycles did not retry. A failed RSS rebuild after a
successful cache write could also lose the XML-topic notification opportunity.
The new process-local queue keeps only changed, already published articles
inside the News window. It waits until their GUIDs are present in local RSS;
the notifier then checks that **all** supplied URLs have complete News metadata
in the public sitemap before posting the XML topic.

Transient failures can recover on later background cycles with no new article
change. Limits: eight attempts per queued update, at least 120 seconds between
attempts, a 30-minute recovery window, original News expiry, and at most 1000
pending URLs. No per-request network, periodic unchanged-topic pings or startup
archive replay. Disable clears pending entries. RSS durable state, retries and
acknowledgements are independent and unchanged.

The XML queue is in process memory. A process replacement loses unconfirmed
entries; this deliberately does not claim the separate durable delivery
guarantees of RSS. It improves transient failure handling without adding a
datastore, credentials, infrastructure or paid service. It does not provide a
delivery receipt from Google News.

RSS again emits `dcterms:modified` where a valid source modification timestamp
exists. Feed invalidation includes that field. Modification-only changes do not
change publication instants, GUIDs, ordering, the News window or new-GUID WebSub
publication detection. Missing source dates are omitted, never fabricated.

Validation covers a failed XML POST followed by a successful unchanged cycle,
initial feed-build failure and repair, no publication from HTTP handlers,
expired/future articles, readiness of every URL, retry limits, preservation of
all original fields and modification-only RSS updates without replay.
