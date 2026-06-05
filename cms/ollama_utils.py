import logging

logger = logging.getLogger(__name__)


def check_ollama_available() -> bool:
    """Check if any AI provider (OpenRouter or Ollama) is available."""
    from cms.services.ai_service import check_ai_available

    return check_ai_available()


__all__ = ["check_ollama_available"]
