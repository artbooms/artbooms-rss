# Correlating declared Google requests

The existing access record is retained. Only user agents declaring `Googlebot`,
`FeedFetcher-Google` or `Google-InspectionTool` receive an appended context:
host, Render request ID, Cloudflare Ray ID, validated IP headers, proxy peer and
application response byte count. Browser and other crawler requests keep their
previous record. This changes logging only: no network calls, DNS lookups,
request authentication, bypasses, routing, XML, conditional responses or WebSub.

`identity=unverified` is intentional. A user-agent string is not proof of Google.
Header values must be interpreted with the provider's documented proxy chain;
the proxy peer is not necessarily the crawler. Verify a candidate client IP
against Google's published IP ranges or forward-confirmed reverse DNS before
counting it as authenticated Google traffic. Separate InspectionTool and our
declared-agent probes from naturally occurring Googlebot requests.

Additional fields exclude query strings, cookies, authorization, arbitrary
headers and referrers. They are validated and bounded. IP-bearing logs stay in
the private Render workspace. Do not commit raw production logs into this public
repository. Export the relevant private evidence before its retention expires;
the service compute plan does not itself determine workspace log retention.

These are **application** logs. A request blocked by Cloudflare or Render before
Gunicorn will still be absent. A correlation ID for a successful request does
not prove the absence of other blocked requests. Provider edge events are needed
to decide that question. The 22 November investigation found no recorded 403 or
429 in GSC's available 90-day crawl report, but that is not a complete edge log.

To roll back logging alone, remove `logger_class` and `%(google_context)s` from
`gunicorn.conf.py`. The standalone module can remain unused. Preserve the
independent fresh-discovery correction already deployed in PR #12.

References: [Gunicorn logging configuration](https://gunicorn.org/reference/settings/),
[Render request IDs and log access](https://render.com/docs/logging),
[Verifying Google crawlers](https://developers.google.com/crawling/docs/crawlers-fetchers/verify-googlebot).
