import logging
import time
import threading
from datetime import datetime, timezone
from functools import lru_cache

from curl_cffi import requests as curl_requests
from cms.services.http_utils import jittered_get

logger = logging.getLogger(__name__)

_GEO_CACHE: dict[str, dict] = {}
_GEO_CACHE_LOCK = threading.Lock()
_GEO_CACHE_TTL = 86400  # 24 hours
_LAST_API_CALL = 0.0
_API_MIN_INTERVAL = 1.5  # ip-api.com free tier: 45 req/min ≈ 1.33s min interval


def _rate_limit():
    global _LAST_API_CALL
    now = time.time()
    elapsed = now - _LAST_API_CALL
    if elapsed < _API_MIN_INTERVAL:
        time.sleep(_API_MIN_INTERVAL - elapsed)
    _LAST_API_CALL = time.time()


def _is_private_ip(ip: str) -> bool:
    if ip.startswith(
        (
            "10.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
            "192.168.",
        )
    ):
        return True
    return ip.startswith("127.") or ip == "::1" or ip == "localhost"


@lru_cache(maxsize=256)
def _lookup_ip_api(ip: str) -> dict | None:
    """Call ip-api.com JSON API. LRU-cached to avoid duplicate lookups."""
    if _is_private_ip(ip):
        return {
            "country": "Private",
            "regionName": "",
            "city": "",
            "isp": "",
            "lat": 0,
            "lon": 0,
            "status": "private",
        }
    try:
        _rate_limit()
        resp = jittered_get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,regionName,city,isp,lat,lon"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return data
            logger.debug(
                f"ip-api.com lookup failed for {ip}: {data.get('message', 'unknown')}"
            )
        else:
            logger.warning(f"ip-api.com returned {resp.status_code} for {ip}")
    except curl_requests.RequestsError as e:
        logger.debug(f"ip-api.com request error for {ip}: {e}")
    return None


def get_ip_geo(ip: str) -> dict:
    """Get geolocation data for an IP address with in-memory caching.

    Returns a dict with keys: country, region, city, isp, lat, lon.
    Returns empty values on failure or for private IPs.
    """
    if _is_private_ip(ip):
        return {
            "country": "Private",
            "region": "",
            "city": "",
            "isp": "",
            "lat": 0,
            "lon": 0,
        }

    with _GEO_CACHE_LOCK:
        cached = _GEO_CACHE.get(ip)
        if cached and time.time() - cached["_ts"] < _GEO_CACHE_TTL:
            return {k: v for k, v in cached.items() if k != "_ts"}

    data = _lookup_ip_api(ip)
    result = {
        "country": data.get("country", "") if data else "",
        "region": data.get("regionName", "") if data else "",
        "city": data.get("city", "") if data else "",
        "isp": data.get("isp", "") if data else "",
        "lat": data.get("lat", 0) if data else 0,
        "lon": data.get("lon", 0) if data else 0,
    }

    with _GEO_CACHE_LOCK:
        _GEO_CACHE[ip] = {**result, "_ts": time.time()}

    return result


def check_anomaly(user_id: str, ip_address: str, geo: dict) -> tuple[bool, str]:
    """Check if a login is anomalous based on user's login history.

    Returns (is_anomaly, reason).
    """
    from .models import LoginLog

    if not geo.get("country") or geo["country"] in ("Private", ""):
        return False, ""

    recent = (
        LoginLog.query.filter(LoginLog.user_id == user_id, LoginLog.is_success == True)
        .order_by(LoginLog.created_at.desc())
        .limit(10)
        .all()
    )

    if not recent:
        return False, ""

    known_countries = set()
    known_cities = set()
    previous_ips = set()

    for log in recent:
        if log.country:
            known_countries.add(log.country)
        if log.city and log.country:
            known_cities.add((log.city, log.country))
        if log.ip_address:
            previous_ips.add(log.ip_address)

    curr_country = geo.get("country", "")
    curr_city = geo.get("city", "")
    curr_lat = geo.get("lat", 0)
    curr_lon = geo.get("lon", 0)

    # Check 1: Country change
    if known_countries and curr_country not in known_countries:
        return True, f"Login from new country: {curr_country}"

    # Check 2: New city in same country (if we have city data)
    if known_cities and curr_city:
        match = curr_country and any(
            city == curr_city and country == curr_country
            for city, country in known_cities
        )
        if not match:
            return True, f"Login from new city: {curr_city}, {curr_country}"

    # Check 3: Impossible travel (same user in far locations within short time)
    if recent and len(recent) > 0 and recent[0].lat and recent[0].lon:
        prev = recent[0]
        if prev.created_at and prev.lat and prev.lon:
            now = datetime.now(timezone.utc)
            diff_hours = (now - prev.created_at).total_seconds() / 3600
            if diff_hours < 2:  # within 2 hours
                from math import radians, sin, cos, sqrt, asin

                lat1, lon1 = radians(prev.lat), radians(prev.lon)
                lat2, lon2 = radians(curr_lat), radians(curr_lon)
                dlat, dlon = lat2 - lat1, lon2 - lon1
                a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
                dist = 2 * 6371 * asin(sqrt(a))  # km
                if dist > 500:
                    return True, f"Impossible travel: {dist:.0f}km in {diff_hours:.1f}h"

    # Check 4: New IP address (even from same location)
    if previous_ips and ip_address not in previous_ips:
        return True, f"Login from new IP: {ip_address}"

    return False, ""


def log_login_attempt(
    user_id: str,
    ip_address: str,
    is_success: bool,
    user_agent: str = "",
    tenant_id: str | None = None,
    run_async: bool = True,
) -> None:
    """Create a LoginLog entry with geolocation and anomaly detection.

    If run_async is True, geo lookup runs in a background thread so the
    login response is not delayed.
    """
    flask_app = None
    if run_async:
        from flask import current_app

        try:
            flask_app = current_app._get_current_object()
        except RuntimeError:
            from app import app

            flask_app = app

        import threading

        t = threading.Thread(
            target=_log_login_sync,
            args=(user_id, ip_address, is_success, user_agent, tenant_id, flask_app),
            daemon=True,
        )
        t.start()
    else:
        _log_login_sync(user_id, ip_address, is_success, user_agent, tenant_id)


def _log_login_sync(
    user_id: str,
    ip_address: str,
    is_success: bool,
    user_agent: str = "",
    tenant_id: str | None = None,
    flask_app=None,
) -> None:
    from .models import db, LoginLog

    if flask_app is None:
        from app import app

        flask_app = app

    with flask_app.app_context():
        if tenant_id is not None:
            from .tenant_context import set_tenant_context

            set_tenant_context(db, tenant_id)

        geo = get_ip_geo(ip_address)

        anomaly = False
        reason = ""
        if is_success:
            try:
                anomaly, reason = check_anomaly(user_id, ip_address, geo)
            except Exception as e:
                logger.debug(f"Anomaly check error: {e}")

        try:
            log = LoginLog(
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else "",
                country=geo.get("country", ""),
                region=geo.get("region", ""),
                city=geo.get("city", ""),
                isp=geo.get("isp", ""),
                lat=geo.get("lat", 0),
                lon=geo.get("lon", 0),
                is_success=is_success,
                is_anomaly=anomaly,
                anomaly_reason=reason if anomaly else "",
            )
            db.session.add(log)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to save LoginLog: {e}")
            db.session.rollback()
