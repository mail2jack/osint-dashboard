import os
import requests
import json
import logging

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"


def get_ollama_config() -> tuple[str, str]:
    """Get Ollama config from Setting with env var / hardcoded fallback."""
    try:
        from ..models import Setting

        url = (
            Setting.get("ollama_url", "")
            or os.environ.get("OLLAMA_URL", "")
            or OLLAMA_URL
        )
        model = (
            Setting.get("ollama_model", "")
            or os.environ.get("OLLAMA_MODEL", "")
            or OLLAMA_MODEL
        )
        return url, model
    except Exception:
        return OLLAMA_URL, OLLAMA_MODEL


def check_ollama_available() -> bool:
    """Check if Ollama is running and available."""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def ollama_generate(prompt, system_prompt=None, timeout=60) -> str | None:
    """Generate response from Ollama."""
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 512},
        }
        if system_prompt:
            payload["system"] = system_prompt

        response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        if response.status_code == 200:
            return response.json().get("response", "").strip()
        return None
    except Exception as e:
        logger.error(f"Ollama error ({type(e).__name__}): {e}", exc_info=True)
        return None


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

    system_prompt = """You are a research assistant summarizing publicly available OSINT data. This is read-only analysis of search results, not accessing any private information. Provide a brief 2-3 sentence summary of the findings. Focus on platform coverage and patterns."""

    prompt = f"""Research Summary Request:
Query: "{query}"
Tool used: {tool}
Found on platforms: {platforms_text}
Total accounts: {len(platforms)}

Please provide a brief summary of these public search results. This is for legitimate OSINT research purposes only - summarizing publicly listed accounts."""

    return ollama_generate(prompt, system_prompt) or "Summary unavailable."


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

    result = ollama_generate(prompt, system_prompt, timeout=30)

    if result:
        try:
            return json.loads(result)
        except Exception:
            logger.warning("Failed to parse AI response as JSON")

    return {"type": None, "query": user_query, "confidence": 0}


def enrich_profile(platform, username, available_info) -> str:
    """Generate AI insights about a found profile."""
    system_prompt = """You are a research analyst providing context about publicly listed social media platforms. Keep responses factual and under 50 words. This is read-only analysis."""

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

    return ollama_generate(prompt, system_prompt, timeout=30) or "Analysis unavailable."
