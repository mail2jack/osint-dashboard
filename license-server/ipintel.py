"""IP intelligence — best-effort enrichment for the license server.

Gathers as much public data as possible about the connecting client IP:

  Tier 0  request metadata     handled in app.py (User-Agent, Accept-Language, …)
  Tier 1  PTR reverse-DNS      socket.gethostbyaddr — free, offline
  Tier 2  RDAP                 registry bootstrap via rdap.org (RIPE/ARIN/…)
                               — free, offline: netname, org, country, address range
  Tier 3  ip-api.com           geolocation, ISP, ASN, proxy/hosting/mobile flags
                               — free, no key, HTTP only
                               (opt-out via LICENSE_GEO_SOURCE=off)

Everything is best-effort: a failed lookup never raises and never blocks
license issuance. Lookups are cached per IP in SQLite so repeat telemetry
heartbeats are free.

Env vars (optional):
    LICENSE_GEO_SOURCE        "ip-api" (default) | "off" — disable third-party tier
    LICENSE_GEO_TIMEOUT       seconds per external call (default 4)
    LICENSE_GEO_TTL_DAYS      cache TTL for successful lookups (default 30)
    LICENSE_GEO_NEGATIVE_TTL  cache TTL for failed lookups, seconds (default 3600)
"""

import ipaddress
import json
import os
import socket
import threading
import time
import urllib.request
from datetime import datetime, timezone

GEO_SOURCE = os.environ.get("LICENSE_GEO_SOURCE", "ip-api").strip().lower()
HTTP_TIMEOUT = float(os.environ.get("LICENSE_GEO_TIMEOUT", "4"))
GEO_TTL_SECONDS = int(float(os.environ.get("LICENSE_GEO_TTL_DAYS", "30")) * 86400)
NEGATIVE_TTL_SECONDS = int(os.environ.get("LICENSE_GEO_NEGATIVE_TTL", "3600"))

IPAPI_FIELDS = (
    "status,message,country,countryCode,region,regionName,city,zip,lat,lon,"
    "timezone,isp,org,as,asname,reverse,mobile,proxy,hosting"
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_lookupable(ip: str) -> bool:
    """Only real public addresses are worth enriching.

    Private/reserved/multicast/loopback addresses are skipped and cached as
    {"private": True} so they never hit an external service.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_global and not addr.is_multicast


def _http_json(url: str):
    """GET a JSON document, never raise. RDAP + ip-api share this."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "iveras-license/1.0",
                "Accept": "application/rdap+json, application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
        if not body.strip():
            return None
        return json.loads(body)
    except Exception:
        return None


def _lookup_ptr(ip: str) -> str | None:
    """Reverse-DNS hostname, bounded by a timeout thread."""
    result: list[str] = []

    def _run() -> None:
        try:
            name = socket.gethostbyaddr(ip)[0]
            result.append(name[:200])
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(HTTP_TIMEOUT)
    return result[0] if result else None


def _rdap_org(doc: dict) -> str:
    """First organisation/FN from RDAP vcard entities."""
    for entity in doc.get("entities") or []:
        vcard = entity.get("vcardArray")
        if not isinstance(vcard, list) or len(vcard) < 2:
            continue
        for item in vcard[1] or []:
            if not isinstance(item, list) or len(item) < 4:
                continue
            if item[0] in ("fn", "org") and isinstance(item[3], str):
                value = item[3].strip()
                if value:
                    return value[:200]
    return ""


def _lookup_rdap(ip: str) -> dict:
    """RDAP via registry bootstrap. Returns {} on any failure."""
    doc = _http_json(f"https://rdap.org/ip/{ip}")
    if not isinstance(doc, dict):
        return {}
    out: dict = {}
    if doc.get("name"):
        out["netname"] = str(doc["name"])[:200]
    if doc.get("handle"):
        out["rdap_handle"] = str(doc["handle"])[:100]
    if doc.get("type"):
        out["rdap_type"] = str(doc["type"])[:100]
    if doc.get("country"):
        out["country"] = str(doc["country"])[:2]
    for key in ("startAddress", "endAddress", "ipVersion"):
        if doc.get(key):
            out[key.lower()] = str(doc[key])[:45]
    org = _rdap_org(doc)
    if org:
        out["org"] = org
    return out


def _lookup_ipapi(ip: str) -> dict:
    """Geolocation + ISP + ASN + flag via ip-api.com (free, no key, HTTP only)."""
    if GEO_SOURCE != "ip-api":
        return {}
    doc = _http_json(f"http://ip-api.com/json/{ip}?fields={IPAPI_FIELDS}&lang=en")
    if not isinstance(doc, dict) or str(doc.get("status")) != "success":
        return {}
    mapping = {
        "country": "country",
        "countryCode": "countryCode",
        "region": "region",
        "regionName": "regionName",
        "city": "city",
        "zip": "postal",
        "lat": "lat",
        "lon": "lon",
        "timezone": "timezone",
        "isp": "isp",
        "org": "org",
        "as": "as",
        "asname": "asname",
        "mobile": "mobile",
        "proxy": "proxy",
        "hosting": "hosting",
    }
    out: dict = {}
    for src, dst in mapping.items():
        value = doc.get(src)
        if value not in (None, "", 0, "0", False):
            out[dst] = value
    if doc.get("reverse"):
        out["ptr"] = str(doc["reverse"])[:200]
    if out:
        out["source"] = "ip-api"
    return out


def get_cached(conn, ip: str) -> dict | None:
    """Return cached intel for ip if still fresh, else None."""
    row = conn.execute(
        "SELECT data, queried_at, ttl_seconds FROM ip_intel WHERE ip = ?", (ip,)
    ).fetchone()
    if row is None:
        return None
    if time.time() - row["queried_at"] > row["ttl_seconds"]:
        return None
    try:
        return json.loads(row["data"])
    except Exception:
        return None


def store(conn, ip: str, data: dict, ttl_seconds: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ip_intel (ip, data, queried_at, ttl_seconds) "
        "VALUES (?, ?, ?, ?)",
        (ip, json.dumps(data, separators=(",", ":")), time.time(), ttl_seconds),
    )


def enrich(conn, ip: str) -> dict:
    """Resolve + cache IP intel. Never raises; returns {} or {"private": True}.

    Only re-looks-up when the per-IP cache is empty or expired, so repeat
    telemetry heartbeats from the same public IP are free.
    """
    if not ip:
        return {}
    cached = get_cached(conn, ip)
    if cached:
        return cached

    if not _is_lookupable(ip):
        data = {"private": True, "queried_at": _now()}
        store(conn, ip, data, GEO_TTL_SECONDS)
        return data

    lookups: dict = {}
    ptr = _lookup_ptr(ip)
    if ptr:
        lookups["ptr"] = ptr
    lookups.update(_lookup_rdap(ip))
    lookups.update(_lookup_ipapi(ip))

    if not lookups:
        data = {"error": "geen IP-informatie beschikbaar", "queried_at": _now()}
        store(conn, ip, data, NEGATIVE_TTL_SECONDS)
        return data
    lookups["queried_at"] = _now()
    store(conn, ip, lookups, GEO_TTL_SECONDS)
    return lookups
