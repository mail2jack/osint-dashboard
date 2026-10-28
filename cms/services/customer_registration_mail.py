"""Conservative forensic checks for raw customer-registration email."""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
import ipaddress
import re
import socket
from typing import Any

import dns.exception
import dns.resolver


DNS_BLOCKLISTS = ("zen.spamhaus.org", "bl.spamcop.net")
_IP_RE = re.compile(r"(?<![\w:])(?:\d{1,3}\.){3}\d{1,3}(?![\w:])")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _public_ips(message) -> list[str]:
    values = list(message.get_all("Received", []))
    values.extend(message.get_all("X-Originating-IP", []))
    found: list[str] = []
    for value in values:
        for candidate in _IP_RE.findall(str(value)):
            try:
                parsed = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if parsed.is_global and candidate not in found:
                found.append(candidate)
    return found


def _dnsbl_hits(ip: str) -> list[str]:
    reversed_ip = ".".join(reversed(ip.split(".")))
    hits: list[str] = []
    for blocklist in DNS_BLOCKLISTS:
        try:
            dns.resolver.resolve(f"{reversed_ip}.{blocklist}", "A", lifetime=1.5)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
            continue
        except Exception:
            continue
        hits.append(blocklist)
    return hits


def _address_header(message, name: str) -> dict[str, Any]:
    raw = str(message.get(name, "")).strip()
    addresses = getaddresses([raw]) if raw else []
    parsed = [{"name": display, "address": address.lower()} for display, address in addresses if address]
    domains = sorted({item["address"].split("@", 1)[1] for item in parsed if "@" in item["address"]})
    return {"raw": raw, "addresses": parsed, "domains": domains}


def analyze_raw_email(raw_message: bytes) -> dict[str, Any]:
    """Return a JSON-safe report; raw message storage is handled by the caller."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    sender = _address_header(message, "From")
    reply_to = _address_header(message, "Reply-To")
    return_path = _address_header(message, "Return-Path")
    ips = _public_ips(message)
    dnsbl = {ip: _dnsbl_hits(ip) for ip in ips}
    auth_header = str(message.get("Authentication-Results", ""))
    auth_lower = auth_header.lower()
    auth = {
        "raw": auth_header,
        "spf": "fail" if "spf=fail" in auth_lower else "pass" if "spf=pass" in auth_lower else "unknown",
        "dkim": "fail" if "dkim=fail" in auth_lower else "pass" if "dkim=pass" in auth_lower else "unknown",
        "dmarc": "fail" if "dmarc=fail" in auth_lower else "pass" if "dmarc=pass" in auth_lower else "unknown",
    }
    body = message.get_body(preferencelist=("plain", "html"))
    body_text = body.get_content() if body else ""
    attachments = [part.get_filename() for part in message.iter_attachments() if part.get_filename()]
    urls = _URL_RE.findall(body_text or "")
    received_headers = [str(value) for value in message.get_all("Received", [])]
    header_names = {key.lower() for key in message.keys()}
    completeness = {
        "from": bool(sender["addresses"]),
        "date": bool(message.get("Date")),
        "subject": bool(str(message.get("Subject", "")).strip()),
        "message_id": bool(str(message.get("Message-ID", "")).strip()),
        "body": bool(str(body_text).strip()),
        "received_chain": bool(received_headers),
        "authentication_results": bool(auth_header),
    }
    warnings: list[str] = []
    for label, present in completeness.items():
        if not present:
            warnings.append(f"Ontbrekend of niet controleerbaar: {label.replace('_', ' ')}.")
    if sender["domains"] and reply_to["domains"] and sender["domains"] != reply_to["domains"]:
        warnings.append("From- en Reply-To-domein verschillen.")
    if any(dnsbl.values()):
        warnings.append("Een herleidbaar publiek IP-adres staat op minimaal één DNS-blocklist.")
    if any(value == "fail" for value in auth.values() if value != auth_header):
        warnings.append("Minimaal één SPF-, DKIM- of DMARC-controle meldt een fout.")

    score = 100 - (10 * sum(1 for value in completeness.values() if not value))
    score -= 20 * sum(1 for key in ("spf", "dkim", "dmarc") if auth[key] == "fail")
    score -= 35 if any(dnsbl.values()) else 0
    score -= 10 if sender["domains"] and reply_to["domains"] and sender["domains"] != reply_to["domains"] else 0
    score = max(0, min(100, score))
    verdict = "hoog risico" if score < 50 or any(dnsbl.values()) else "controle nodig" if score < 75 else "geen directe signalen"
    date_value = None
    if message.get("Date"):
        try:
            date_value = parsedate_to_datetime(str(message["Date"])).isoformat()
        except (TypeError, ValueError, OverflowError):
            warnings.append("De Date-header heeft geen herkenbaar formaat.")
    reverse_dns = {}
    for ip in ips:
        try:
            reverse_dns[ip] = socket.gethostbyaddr(ip)[0]
        except (OSError, socket.herror):
            reverse_dns[ip] = None
    return {
        "schema_version": 1,
        "verdict": verdict,
        "score": score,
        "sender": sender,
        "reply_to": reply_to,
        "return_path": return_path,
        "date": date_value,
        "subject": str(message.get("Subject", "")),
        "message_id": str(message.get("Message-ID", "")),
        "public_ips": ips,
        "reverse_dns": reverse_dns,
        "dns_blocklists": dnsbl,
        "authentication": auth,
        "received_chain": received_headers,
        "headers": sorted(header_names),
        "attachments": attachments,
        "url_count": len(urls),
        "completeness": completeness,
        "warnings": warnings,
        "message_sha256": hashlib.sha256(raw_message).hexdigest(),
    }
