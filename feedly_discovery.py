"""Temporary static RSS autodiscovery page, independent of production feeds."""
import json
import logging
from datetime import datetime, timezone

from flask import Response, request

logger = logging.getLogger("artbooms.feedly_discovery")
DISCOVERY_HTML = '''<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>ARTBOOMS Feed Discovery Test</title>
  <link rel="alternate"
        type="application/rss+xml"
        title="ARTBOOMS Feed Discovery Test"
        href="https://artbooms-rss-x6pc.onrender.com/feedly-probe.xml">
</head>
<body>
  ARTBOOMS feed discovery test
</body>
</html>
'''


def feedly_discovery_view():
    response = Response(DISCOVERY_HTML.encode("utf-8"),
                        content_type="text/html; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    logger.info("feedly_discovery %s", json.dumps({
        "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "method": request.method,
        "host": request.headers.get("Host", ""),
        "path": request.path,
        "user_agent": request.headers.get("User-Agent", ""),
        "accept": request.headers.get("Accept", ""),
        "accept_encoding": request.headers.get("Accept-Encoding", ""),
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_forwarded_proto": request.headers.get("X-Forwarded-Proto", ""),
        "remote_addr": request.remote_addr,
        "status": response.status_code,
    }, ensure_ascii=True, separators=(",", ":")))
    return response
