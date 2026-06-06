import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "openrouter/auto"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"


# =============================================================================
# OpenRouter provider
# =============================================================================


def get_openrouter_config() -> dict:
    """Get OpenRouter config from Settings with env var / hardcoded fallback."""
    try:
        from ..setting_cache import cached_setting_get

        api_key = cached_setting_get("openrouter_api_key", "") or os.environ.get(
            "OPENROUTER_API_KEY", ""
        )
        model = (
            cached_setting_get("openrouter_model", "")
            or os.environ.get("OPENROUTER_MODEL", "")
            or OPENROUTER_MODEL
        )
        base_url = (
            cached_setting_get("openrouter_base_url", "")
            or os.environ.get("OPENROUTER_BASE_URL", "")
            or OPENROUTER_BASE_URL
        )
        return {"api_key": api_key, "model": model, "base_url": base_url}
    except Exception:
        return {
            "api_key": "",
            "model": OPENROUTER_MODEL,
            "base_url": OPENROUTER_BASE_URL,
        }


def check_openrouter_available() -> bool:
    """Check if OpenRouter is reachable with the configured API key."""
    config = get_openrouter_config()
    if not config["api_key"]:
        return False
    try:
        r = httpx.get(
            f"{config['base_url']}/models",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def openrouter_generate(prompt, system_prompt=None, timeout=60) -> str | None:
    """Generate response from OpenRouter chat completions API."""
    config = get_openrouter_config()
    if not config["api_key"]:
        return None

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        r = httpx.post(
            f"{config['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://iveras-dashboard.local",
                "X-Title": "Iveras OSINT Dashboard",
            },
            json={
                "model": config["model"],
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 512,
                "stream": False,
            },
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
        logger.warning("OpenRouter returned %s: %s", r.status_code, r.text[:200])
        return None
    except Exception as e:
        logger.error("OpenRouter error (%s): %s", type(e).__name__, e, exc_info=True)
        return None


# =============================================================================
# Ollama provider (fallback)
# =============================================================================


def get_ollama_config() -> tuple[str, str]:
    """Get Ollama config from Setting with env var / hardcoded fallback."""
    try:
        from ..setting_cache import cached_setting_get

        url = (
            cached_setting_get("ollama_url", "")
            or os.environ.get("OLLAMA_URL", "")
            or OLLAMA_URL
        )
        model = (
            cached_setting_get("ollama_model", "")
            or os.environ.get("OLLAMA_MODEL", "")
            or OLLAMA_MODEL
        )
        return url, model
    except Exception:
        return OLLAMA_URL, OLLAMA_MODEL


def check_ollama_available() -> bool:
    """Check if Ollama is running and available."""
    try:
        response = httpx.get("http://localhost:11434/api/tags", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def ollama_generate(prompt, system_prompt=None, timeout=60) -> str | None:
    """Generate response from Ollama (fallback provider)."""
    url, model = get_ollama_config()
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 512},
        }
        if system_prompt:
            payload["system"] = system_prompt

        r = httpx.post(url, json=payload, timeout=timeout)
        if r.status_code == 200:
            return r.json().get("response", "").strip()
        return None
    except Exception as e:
        logger.error("Ollama error (%s): %s", type(e).__name__, e, exc_info=True)
        return None


# =============================================================================
# Unified provider routing
# =============================================================================


def get_ai_config() -> dict:
    """Get current AI provider configuration with availability."""
    or_config = get_openrouter_config()
    if or_config["api_key"]:
        return {
            "provider": "openrouter",
            "model": or_config["model"],
            "available": check_openrouter_available(),
        }
    url, model = get_ollama_config()
    return {
        "provider": "ollama",
        "model": model,
        "available": check_ollama_available(),
    }


def check_ai_available() -> bool:
    """Check if any AI provider (OpenRouter or Ollama) is available."""
    or_config = get_openrouter_config()
    if or_config["api_key"]:
        if check_openrouter_available():
            return True
    return check_ollama_available()


def _generate(prompt, system_prompt=None, timeout=60) -> str | None:
    """Try OpenRouter first, fall back to Ollama."""
    if get_openrouter_config()["api_key"]:
        result = openrouter_generate(prompt, system_prompt, timeout)
        if result is not None:
            return result
        logger.info("OpenRouter failed, falling back to Ollama")
    return ollama_generate(prompt, system_prompt, timeout)


# =============================================================================
# Consumer functions (provider-agnostic)
# =============================================================================


def summarize_results(query, tool, findings) -> str:
    """Generate AI summary of search results."""
    if not findings:
        return "No results found to summarize."

    platforms = [
        f.get("platform") or f.get("site", "Unknown")
        for f in findings
        if f.get("exists")
    ]
    if not platforms:
        return "No confirmed accounts found."

    platforms_text = ", ".join(platforms[:15])
    if len(platforms) > 15:
        platforms_text += f" and {len(platforms) - 15} more"

    system_prompt = "You are a research assistant summarizing publicly available OSINT data. Provide a brief 2-3 sentence summary of the findings. Focus on platform coverage and patterns."

    prompt = f"""Research Summary Request:
Query: "{query}"
Tool used: {tool}
Found on platforms: {platforms_text}
Total accounts: {len(platforms)}

Provide a brief summary of these public search results for legitimate OSINT research purposes."""

    return _generate(prompt, system_prompt) or "Summary unavailable."


def analyze_natural_language(user_query, available_tools) -> dict:
    """Convert natural language query to structured search parameters."""
    system_prompt = """You are an OSINT query analyzer. Parse natural language queries and determine:
1. The search type (username, email, phone, name, ip, domain)
2. The actual search value
3. Any additional context

Respond ONLY with valid JSON in this format:
{"type": "username|email|phone|name|ip|domain", "query": "the actual search value", "confidence": 0.0-1.0}

If the query is unclear, set confidence below 0.5."""

    prompt = f"""Analyze this natural language OSINT query and extract the search parameters:

Query: "{user_query}"

Available tools: {", ".join(available_tools)}

Determine what the user is searching for and extract the key information."""

    result = _generate(prompt, system_prompt, timeout=30)

    if result:
        try:
            return json.loads(result)
        except Exception:
            logger.warning("Failed to parse AI response as JSON")

    return {"type": None, "query": user_query, "confidence": 0}


def enrich_profile(platform, username, available_info) -> str:
    """Generate AI insights about a found profile."""
    system_prompt = "You are a research analyst providing context about publicly listed social media platforms. Keep responses factual and under 50 words."

    info_text = ""
    if available_info.get("url"):
        info_text += f"Profile URL: {available_info['url']}\n"
    if available_info.get("bio"):
        info_text += f"Bio: {available_info['bio']}\n"
    if available_info.get("name"):
        info_text += f"Display Name: {available_info['name']}\n"

    prompt = f"""Platform Analysis Request:
Platform: {platform}
Username: {username}

{info_text or "Limited information available."}

Provide brief context about this platform (what it is, typical use cases). Keep under 40 words. Research purposes only."""

    return _generate(prompt, system_prompt, timeout=30) or "Analysis unavailable."
