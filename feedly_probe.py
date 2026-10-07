"""Temporary, independent RSS probe; no production cache or worker access."""

import json
import logging
from datetime import datetime, timezone

from flask import Response, request


ALLOWED_HOSTS = frozenset({
    "rss.artbooms.com",
    "artbooms-rss-x6pc.onrender.com",
})
logger = logging.getLogger("artbooms.feedly_probe")

# Stable diagnostic identity/date: identical bytes across workers and requests.
# This GUID is unrelated to any production article GUID.
PROBE_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>ARTBOOMS Feedly diagnostic probe</title>
    <link>https://www.artbooms.com/</link>
    <description>Temporary ARTBOOMS feed retrieval diagnostic.</description>
    <language>it-IT</language>
    <atom:link href="https://{host}/feedly-probe.xml" rel="self" type="application/rss+xml" />
    <item>
      <title>ARTBOOMS diagnostic item: Broken at Palazzo Strozzi</title>
      <link>https://www.artbooms.com/blog/broken-mostra-palazzo-strozzi</link>
      <description>A single diagnostic item linking to an existing ARTBOOMS article.</description>
      <guid isPermaLink="false">urn:uuid:0e5096a8-6041-46c7-9fd1-97af286b5ec0</guid>
      <pubDate>Wed, 07 Oct 2026 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
'''


def _authorized_host(raw_host):
    # Validate Host itself, never a client-supplied forwarding header.
    host = raw_host.lower()
    if host.endswith(":443"):
        host = host[:-4]
    return host if host in ALLOWED_HOSTS else None


def feedly_probe_view():
    raw_host = request.headers.get("Host", "")
    host = _authorized_host(raw_host)
    status = 200 if host is not None else 400
    logger.info("feedly_probe %s", json.dumps({
        "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "method": request.method,
        "host": raw_host,
        "path": request.path,
        "user_agent": request.headers.get("User-Agent", ""),
        "accept": request.headers.get("Accept", ""),
        "accept_encoding": request.headers.get("Accept-Encoding", ""),
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_forwarded_proto": request.headers.get("X-Forwarded-Proto", ""),
        "remote_addr": request.remote_addr,
        "status": status,
    }, ensure_ascii=True, separators=(",", ":")))
    if host is None:
        response = Response("Host not authorized for this diagnostic endpoint.\n", status=400,
                            content_type="text/plain; charset=utf-8")
    else:
        response = Response(PROBE_XML.format(host=host).encode("utf-8"),
                            content_type="application/rss+xml; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    return response
