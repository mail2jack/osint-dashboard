import logging

logger = logging.getLogger(__name__)


def check_ollama_available() -> bool:
    from cms.services.ai_service import get_ollama_config

    url, _model = get_ollama_config()
    if not url:
        return False
    try:
        import httpx

        r = httpx.get(f"{url}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


__all__ = ["check_ollama_available"]
