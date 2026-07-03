import logging
import socket

from curl_cffi import requests as curl_requests
from cms.services.http_utils import jitter_sleep

from cms.validators import validate_ip, validate_domain

logger = logging.getLogger(__name__)


def lookup_ip(ip_address):
    result = {
        "ip": ip_address,
        "valid": validate_ip(ip_address),
        "reverse_dns": None,
        "geolocation": None,
        "ipapi": None,
        "ports": [],
        "reputation_score": 0,
    }

    if result["valid"]:
        try:
            result["reverse_dns"] = socket.gethostbyaddr(ip_address)[0]
        except socket.herror:
            result["reverse_dns"] = "N/A"
        except Exception as e:
            logger.debug(f"Reverse DNS failed ({type(e).__name__}): {e}")
            result["reverse_dns"] = "N/A"

        try:
            jitter_sleep(domain_hint="http://ip-api.com")
            response = curl_requests.get(
                f"http://ip-api.com/json/{ip_address}", timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                result["geolocation"] = {
                    "country": data.get("country", "N/A"),
                    "region": data.get("regionName", "N/A"),
                    "city": data.get("city", "N/A"),
                    "isp": data.get("isp", "N/A"),
                    "org": data.get("org", "N/A"),
                    "as": data.get("as", "N/A"),
                    "lat": data.get("lat", 0),
                    "lon": data.get("lon", 0),
                }
        except Exception as e:
            logger.debug(f"ip-api lookup failed ({type(e).__name__}): {e}")

        try:
            import ipapi as _ipapi

            ipapi_data = _ipapi.location(ip=ip_address, output="json")
            if ipapi_data and "error" not in ipapi_data:
                result["ipapi"] = {
                    "country_name": ipapi_data.get("country_name", "N/A"),
                    "region": ipapi_data.get("region", "N/A"),
                    "city": ipapi_data.get("city", "N/A"),
                    "postal": ipapi_data.get("postal", "N/A"),
                    "latitude": ipapi_data.get("latitude"),
                    "longitude": ipapi_data.get("longitude"),
                    "timezone": ipapi_data.get("timezone", "N/A"),
                    "utc_offset": ipapi_data.get("utc_offset", "N/A"),
                    "currency": ipapi_data.get("currency", "N/A"),
                    "currency_name": ipapi_data.get("currency_name", "N/A"),
                    "asn": ipapi_data.get("asn", "N/A"),
                    "org": ipapi_data.get("org", "N/A"),
                    "languages": ipapi_data.get("languages", "N/A"),
                    "country_capital": ipapi_data.get("country_capital", "N/A"),
                    "continent_code": ipapi_data.get("continent_code", "N/A"),
                    "in_eu": ipapi_data.get("in_eu", False),
                    "country_area": ipapi_data.get("country_area"),
                    "country_population": ipapi_data.get("country_population"),
                    "calling_code": ipapi_data.get("country_calling_code", "N/A"),
                }
        except Exception as e:
            logger.debug(f"ipapi lookup failed: {e}")

        common_ports = [
            21,
            22,
            23,
            25,
            53,
            80,
            110,
            143,
            443,
            445,
            993,
            995,
            3306,
            3389,
            5432,
            8080,
        ]
        for port in common_ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            try:
                if sock.connect_ex((ip_address, port)) == 0:
                    result["ports"].append(port)
            except OSError:
                logger.debug("Port check failed for %s:%s", ip_address, port)
            finally:
                sock.close()

        blacklisted_ips = ["185.220.101", "192.42.116", "104.244.73"]
        result["reputation_score"] = 100
        for bl in blacklisted_ips:
            if ip_address.startswith(bl):
                result["reputation_score"] -= 30
        if len(result["ports"]) > 5:
            result["reputation_score"] -= 10

    return result


def lookup_domain(domain):
    result = {
        "domain": domain,
        "valid": validate_domain(domain),
        "ip_addresses": [],
        "dns_records": {},
        "whois": {},
        "subdomains": [],
        "ssl_info": None,
    }

    if result["valid"]:
        try:
            result["ip_addresses"] = list(
                set(socket.getaddrinfo(domain, 80, socket.AF_INET, socket.SOCK_STREAM))
            )
            result["ip_addresses"] = [r[4][0] for r in result["ip_addresses"]]
        except socket.gaierror:
            result["ip_addresses"] = []
        except Exception as e:
            logger.debug(f"IP resolution failed ({type(e).__name__}): {e}")
            result["ip_addresses"] = []

        try:
            dns_types = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SPF", "CAA"]
            import dns.resolver

            for dns_type in dns_types:
                try:
                    if dns_type == "A":
                        result["dns_records"]["A"] = socket.getaddrinfo(
                            domain, 80, socket.AF_INET
                        )[0][4][0]
                    elif dns_type == "AAAA":
                        try:
                            result["dns_records"]["AAAA"] = socket.getaddrinfo(
                                domain, 80, socket.AF_INET6
                            )[0][4][0]
                        except (socket.gaierror, OSError):
                            result["dns_records"]["AAAA"] = "N/A"
                        except Exception as e:
                            logger.debug(
                                f"AAAA record lookup failed ({type(e).__name__}): {e}"
                            )
                            result["dns_records"]["AAAA"] = "N/A"
                    else:
                        try:
                            answers = dns.resolver.resolve(domain, dns_type)
                            if dns_type == "MX":
                                result["dns_records"]["MX"] = [
                                    {
                                        "priority": r.preference,
                                        "host": str(r.exchange).rstrip("."),
                                    }
                                    for r in answers
                                ]
                            elif dns_type == "TXT":
                                result["dns_records"]["TXT"] = [
                                    str(r).strip('"') for r in answers
                                ]
                            elif dns_type == "SPF":
                                result["dns_records"]["SPF"] = [
                                    str(r).strip('"') for r in answers
                                ]
                            elif dns_type == "CAA":
                                result["dns_records"]["CAA"] = [str(r) for r in answers]
                            else:
                                result["dns_records"][dns_type] = [
                                    str(r) for r in answers
                                ]
                        except (
                            socket.gaierror,
                            dns.resolver.NoAnswer,
                            dns.exception.Timeout,
                        ):
                            result["dns_records"][dns_type] = "N/A"
                        except Exception as e:
                            logger.debug(
                                f"{dns_type} record lookup failed ({type(e).__name__}): {e}"
                            )
                            result["dns_records"][dns_type] = "N/A"
                except Exception as e:
                    logger.debug(
                        f"DNS type processing failed ({type(e).__name__}): {e}"
                    )
                    result["dns_records"][dns_type] = "N/A"
        except ImportError:
            for dns_type in ["A", "AAAA", "MX", "NS", "TXT", "CNAME"]:
                try:
                    if dns_type == "A":
                        result["dns_records"]["A"] = socket.getaddrinfo(
                            domain, 80, socket.AF_INET
                        )[0][4][0]
                    elif dns_type == "AAAA":
                        try:
                            result["dns_records"]["AAAA"] = socket.getaddrinfo(
                                domain, 80, socket.AF_INET6
                            )[0][4][0]
                        except Exception:
                            result["dns_records"]["AAAA"] = "N/A"
                    else:
                        result["dns_records"][dns_type] = "Not available"
                except Exception:
                    result["dns_records"][dns_type] = "N/A"
        except Exception:
            logger.exception("Domain DNS lookup error")
            result["error"] = "DNS lookup failed"

        try:
            import subprocess

            if domain.startswith("-"):
                raise ValueError("Invalid domain name")
            whois_proc = subprocess.run(
                ["whois", domain], capture_output=True, text=True, timeout=10
            )
            whois_text = whois_proc.stdout

            def extract_field(text, field_names):
                for name in field_names:
                    for line in text.split("\n"):
                        if line.lower().startswith(name.lower() + ":"):
                            return line.split(":", 1)[1].strip()
                    parts = name.split()
                    if len(parts) > 1:
                        pattern = " ".join(parts[:2]).lower()
                        for line in text.split("\n"):
                            if pattern in line.lower():
                                return line.split(":", 1)[1].strip()
                return None

            result["whois"] = {
                "registrar": extract_field(
                    whois_text, ["Registrar", "Sponsoring Registrar", "Registrar Name"]
                ),
                "registration_date": extract_field(
                    whois_text,
                    ["Creation Date", "Created", "Created On", "Created Date"],
                ),
                "expiration_date": extract_field(
                    whois_text,
                    [
                        "Expiration Date",
                        "Expires",
                        "Expires On",
                        "Expiry Date",
                        "Registry Expiry Date",
                    ],
                ),
                "updated_date": extract_field(
                    whois_text, ["Updated Date", "Modified", "Last Updated"]
                ),
                "status": extract_field(whois_text, ["Domain Status", "Status"]),
                "name_servers": [],
                "dnssec": extract_field(whois_text, ["DNSSEC"]),
                "registrant": extract_field(
                    whois_text, ["Registrant Name", "Registrant", "Owner", "Holder"]
                ),
                "registrant_org": extract_field(
                    whois_text, ["Registrant Organization", "Org", "Organization"]
                ),
                "registrant_country": extract_field(
                    whois_text, ["Registrant Country", "Country"]
                ),
                "admin_contact": extract_field(
                    whois_text, ["Admin Name", "Admin", "Administrative Contact"]
                ),
                "tech_contact": extract_field(
                    whois_text, ["Tech Name", "Tech", "Technical Contact"]
                ),
            }

            for line in whois_text.split("\n"):
                if (
                    "Name Server" in line
                    or "Nameserver" in line
                    or "nserver" in line.lower()
                ):
                    parts = line.split(":")
                    if len(parts) > 1:
                        ns = parts[1].strip().lower()
                        if ns and ns not in [
                            n.lower() for n in result["whois"]["name_servers"]
                        ]:
                            result["whois"]["name_servers"].append(ns)

        except subprocess.TimeoutExpired:
            result["whois"] = {"error": "WHOIS timeout"}
        except Exception:
            logger.exception("WHOIS lookup error")
            result["whois"] = {"error": "WHOIS lookup failed"}

        common_subdomains = [
            "www",
            "mail",
            "ftp",
            "admin",
            "blog",
            "dev",
            "api",
            "test",
            "staging",
            "smtp",
            "pop",
            "imap",
            "webmail",
        ]
        for sub in common_subdomains:
            try:
                full_domain = f"{sub}.{domain}"
                socket.getaddrinfo(full_domain, 80, socket.AF_INET)
                result["subdomains"].append(full_domain)
            except (TimeoutError, socket.gaierror):
                logger.debug("DNS resolution failed for %s", full_domain)

        try:
            import ssl

            context = ssl.create_default_context()
            with (
                socket.create_connection((domain, 443), timeout=5) as sock,
                context.wrap_socket(sock, server_hostname=domain) as ssock,
            ):
                cert = ssock.getpeercert()
                result["ssl_info"] = {
                    "issuer": dict(x[0] for x in cert["issuer"]),
                    "subject": dict(x[0] for x in cert["subject"]),
                    "version": cert["version"],
                    "not_before": cert["notBefore"],
                    "not_after": cert["notAfter"],
                }
        except Exception as e:
            logger.debug(f"SSL cert lookup failed ({type(e).__name__}): {e}")
            result["ssl_info"] = "SSL info unavailable"

    return result


__all__ = ["lookup_ip", "lookup_domain"]
