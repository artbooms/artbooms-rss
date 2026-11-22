"""Correlate declared Google requests with Render/Cloudflare, without network I/O.

These headers are evidence to investigate, not authentication or a crawler bypass.
Only declared Google requests get additional IP fields. Never log cookies,
authorization, query strings or arbitrary headers in the additional context.
"""
import ipaddress
import re

from gunicorn.glogging import Logger


def _token(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", value) else "-"


def _ip(value):
    if not isinstance(value, str) or len(value) > 64 or "%" in value:
        return "-"
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return "-"


def _forwarded_ips(value):
    if not isinstance(value, str) or len(value) > 1024:
        return "-"
    # Keep the end added by the proxy even if a client supplied a long prefix.
    values = [_ip(part) for part in value.split(",")[-8:]]
    return ",".join(part for part in values if part != "-") or "-"


def google_context(atoms):
    agent = atoms.get("a", "")
    if not isinstance(agent, str):
        return ""
    if "Google-InspectionTool" in agent:
        family = "inspection"
    elif "FeedFetcher-Google" in agent:
        family = "feedfetcher"
    elif "Googlebot" in agent:
        family = "googlebot"
    else:
        return ""
    values = {
        "google_declared": family,
        "identity": "unverified",
        "host": _token(atoms.get("{host}i")),
        "rndr_id": _token(atoms.get("{rndr-id}i")),
        "cf_ray": _token(atoms.get("{cf-ray}i")),
        "cf_ip": _ip(atoms.get("{cf-connecting-ip}i")),
        "xff": _forwarded_ips(atoms.get("{x-forwarded-for}i")),
        "peer_ip": _ip(atoms.get("h")),
        "app_bytes": _token(str(atoms.get("B", "-"))),
    }
    return " " + " ".join(f"{key}={value}" for key, value in values.items())


class CrawlerLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        try:
            atoms["google_context"] = google_context(atoms)
        except Exception:
            # Observability must never suppress the existing access record.
            atoms["google_context"] = " google_trace_error=1"
        return atoms
