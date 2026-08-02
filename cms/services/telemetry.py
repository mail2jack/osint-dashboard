"""Telemetry client — registers the install and reports a daily heartbeat.

Sends system info (hostname, IPs, OS/kernel, CPU/RAM/disk, app version) to the
central Iveras license server (https://license.iveras.com). Failures are silent
so a down license server never disrupts the dashboard.
"""

import logging
import os
import secrets
import socket
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

TELEMETRY_SERVER_DEFAULT = "https://license.iveras.com"
CHECK_IN_INTERVAL = 86400
MIN_INTERVAL = 6 * 3600


def _get_setting(key, default):
    try:
        from cms.models import Setting

        value = Setting.get(key)
        return value if value not in (None, "") else default
    except Exception:
        return os.environ.get(key.upper(), default)


def is_telemetry_enabled() -> bool:
    value = _get_setting("telemetry_enabled", "true")
    return str(value).lower() in ("true", "1", "yes")


def telemetry_server_url() -> str:
    url = _get_setting("telemetry_server_url", TELEMETRY_SERVER_DEFAULT)
    if not url:
        url = TELEMETRY_SERVER_DEFAULT
    return str(url).rstrip("/")


def get_install_id() -> str | None:
    value = os.environ.get("INSTALL_ID", "").strip()
    if value:
        return value
    return _get_setting("install_id", None)


def get_install_token() -> str | None:
    value = os.environ.get("INSTALL_TOKEN", "").strip()
    if value:
        return value
    return _get_setting("install_token", None)


def ensure_install_identity() -> str | None:
    from cms.models import Setting

    install_id = get_install_id() or Setting.get("install_id")
    token = get_install_token() or Setting.get("install_token")
    changed = False
    if not install_id:
        install_id = str(uuid.uuid4())
        Setting.set(
            "install_id",
            install_id,
            category="system",
            description="Unique install identifier (telemetry)",
        )
        changed = True
    if not token:
        token = secrets.token_hex(32)
        Setting.set(
            "install_token",
            token,
            category="system",
            description="Telemetry authentication token",
            encrypt=True,
        )
        changed = True
    if changed:
        _append_to_env(install_id, token)
        logger.info("Telemetry identity ready: %s", install_id)
    return install_id


def _append_to_env(install_id: str, token: str) -> None:
    try:
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        env_path = os.path.join(root, ".env")
        if not os.path.isfile(env_path):
            return
        with open(env_path) as f:
            content = f.read()
        additions = []
        if "INSTALL_ID=" not in content:
            additions.append(f"INSTALL_ID={install_id}")
        if "INSTALL_TOKEN=" not in content:
            additions.append(f"INSTALL_TOKEN={token}")
        if additions:
            with open(env_path, "a") as f:
                f.write("\n" + "\n".join(additions) + "\n")
            os.chmod(env_path, 0o600)
    except Exception:
        logger.debug("Could not append telemetry identity to .env", exc_info=True)


def _local_ips() -> list:
    ips = []
    try:
        import psutil

        for _name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if ip and not ip.startswith("127.") and ip not in ips:
                        ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and ip not in ips:
                ips.append(ip)
        except Exception:
            pass
    return ips[:8]


def _cpu_model() -> str:
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    try:
        import platform as platform_mod

        return platform_mod.processor() or "unknown"
    except Exception:
        return "unknown"


def _is_docker() -> bool:
    if os.environ.get("DOCKER") or os.path.exists("/.dockerenv"):
        return True
    if os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as f:
            return "docker" in f.read()
    except Exception:
        return False


def collect_system_info() -> dict:
    import platform as platform_mod

    info = {
        "hostname": socket.gethostname() or "unknown",
        "os_name": platform_mod.system(),
        "os_version": platform_mod.release(),
        "kernel": platform_mod.version(),
        "platform": "docker" if _is_docker() else "bare-metal",
        "app_version": "",
        "cpu_model": "",
        "cpu_count": 0,
        "ram_gb": 0.0,
        "disk_gb": 0.0,
        "local_ips": _local_ips(),
    }
    try:
        from version import get_version

        info["app_version"] = get_version()
    except Exception:
        pass
    try:
        import psutil

        info["cpu_count"] = psutil.cpu_count(logical=True) or 0
        info["cpu_model"] = _cpu_model()
        info["ram_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
        try:
            info["disk_gb"] = round(psutil.disk_usage("/").total / (1024**3), 1)
        except Exception:
            pass
    except Exception:
        pass
    return info


def _post(url: str, payload: dict) -> requests.Response | None:
    install_id = get_install_id()
    token = get_install_token()
    if not install_id or not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "X-Install-ID": install_id}
    return requests.post(url, json=payload, headers=headers, timeout=8)


def _send(kind: str, install_id: str) -> requests.Response | None:
    payload = {"install_id": install_id, "info": collect_system_info()}
    url = f"{telemetry_server_url()}/api/{kind}"
    try:
        resp = _post(url, payload)
    except Exception as e:
        logger.debug("Telemetry %s request failed: %s", kind, e)
        return None
    if resp is None:
        return None
    if resp.status_code in (401, 403, 404):
        _clear_registered(install_id)
    return resp


def _consume_license(resp: requests.Response) -> None:
    """Cache the signed license returned with the check-in response."""
    try:
        from cms.services import license as license_service

        license_service.cache_license(resp.json().get("license"))
    except Exception:
        logger.debug("Could not refresh license from check-in", exc_info=True)


def _registered_id() -> str | None:
    return _get_setting("telemetry_registered_id", None)


def _mark_registered(install_id: str) -> None:
    try:
        from cms.models import Setting

        Setting.set(
            "telemetry_registered_id",
            install_id,
            category="system",
            description="Install identity acknowledged by the license server",
        )
    except Exception:
        logger.debug("Could not mark telemetry registered", exc_info=True)


def _clear_registered(install_id: str) -> None:
    registered = _registered_id()
    if registered != install_id:
        return
    try:
        from cms.models import Setting

        Setting.set(
            "telemetry_registered_id",
            "",
            category="system",
            description="Install identity acknowledged by the license server",
        )
    except Exception:
        logger.debug("Could not clear telemetry registration", exc_info=True)


def _last_check() -> float | None:
    value = _get_setting("telemetry_last_check", None)
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _set_last_check(now: float) -> None:
    try:
        from cms.models import Setting

        Setting.set(
            "telemetry_last_check",
            str(now),
            category="system",
            description="Last successful telemetry check-in (unix timestamp)",
        )
    except Exception:
        logger.debug("Could not store telemetry last check", exc_info=True)


def maybe_check_in(force: bool = False) -> bool:
    install_id = get_install_id()
    if not install_id or not is_telemetry_enabled():
        return False

    registered = _registered_id() == install_id
    now = time.time()
    last = _last_check()

    if not registered:
        resp = _send("register", install_id)
        if resp is not None and resp.status_code < 400:
            _mark_registered(install_id)
            _set_last_check(now)
            _consume_license(resp)
            logger.info("Telemetry: install %s registered", install_id)
            return True
        return False

    if force or last is None or (now - last) >= CHECK_IN_INTERVAL:
        resp = _send("telemetry", install_id)
        if resp is not None and resp.status_code < 400:
            _set_last_check(now)
            _consume_license(resp)
            return True
        if resp is not None and resp.status_code in (401, 403, 404):
            _clear_registered(install_id)
        return False
    return False


def _telemetry_loop(app) -> None:
    time.sleep(5)
    while True:
        try:
            ctx = app.app_context()
            ctx.push()
            try:
                maybe_check_in()
            finally:
                ctx.pop()
        except Exception:
            logger.debug("Telemetry check-in failed", exc_info=True)
        time.sleep(3600)


def init_telemetry(app) -> None:
    if app.testing or os.environ.get("FLASK_ENV", "development") != "production":
        return
    if os.environ.get("TELEMETRY_DISABLED", "").lower() in ("1", "true", "yes"):
        return
    try:
        ctx = app.app_context()
        ctx.push()
        try:
            ensure_install_identity()
        finally:
            ctx.pop()
    except Exception:
        logger.debug("Telemetry identity init skipped", exc_info=True)
    threading.Thread(
        target=_telemetry_loop,
        args=(app,),
        daemon=True,
        name="telemetry-checkin",
    ).start()


def report_now() -> bool:
    return maybe_check_in(force=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
