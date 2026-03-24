import os
import re
import socket
import json
import uuid
import asyncio
import time
import queue
import logging
import httpx
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, send_file
from functools import lru_cache
from urllib.parse import quote
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

# Have I Been Pwned API Key - get from https://haveibeenpwned.com/API/Key
# Set directly here or use environment variable: export HIBP_API_KEY="your-key"
HIBP_API_KEY = os.environ.get('HIBP_API_KEY', '')

CACHE_TTL_HOURS = 24
result_cache = {}

RATE_LIMIT_STATUS_CODES = {429, 403, 503}
RETRY_MAX_ATTEMPTS = 2
RETRY_BASE_DELAY = 1

platform_rate_limits = {}
search_request_counts = {}

def get_cached_result(search_type, value):
    key = f"{search_type}:{value.lower()}"
    if key in result_cache:
        cached = result_cache[key]
        if datetime.now() < cached['expires']:
            return cached['data']
        else:
            del result_cache[key]
    return None

def set_cached_result(search_type, value, data):
    key = f"{search_type}:{value.lower()}"
    result_cache[key] = {
        'data': data,
        'expires': datetime.now() + timedelta(hours=CACHE_TTL_HOURS),
        'timestamp': datetime.now()
    }

def clear_cache():
    global result_cache
    result_cache = {}
    return len(result_cache)

def get_cache_info():
    now = datetime.now()
    valid = 0
    expired = 0
    for cached in result_cache.values():
        if now < cached['expires']:
            valid += 1
        else:
            expired += 1
    return {
        'total': len(result_cache),
        'valid': valid,
        'expired': expired
    }

def is_rate_limited(site_name):
    if site_name in platform_rate_limits:
        limit_data = platform_rate_limits[site_name]
        if datetime.now() < limit_data['reset_at']:
            return True, limit_data
    return False, None

def set_rate_limited(site_name, retry_after=60):
    platform_rate_limits[site_name] = {
        'limited_at': datetime.now(),
        'reset_at': datetime.now() + timedelta(seconds=retry_after),
        'count': platform_rate_limits.get(site_name, {}).get('count', 0) + 1
    }

def get_rate_limit_status():
    now = datetime.now()
    limited = []
    for site, data in platform_rate_limits.items():
        if now < data['reset_at']:
            remaining = (data['reset_at'] - now).seconds
            limited.append({'site': site, 'remaining_seconds': remaining})
    return limited

async def check_site_with_retry(client, site_name, site_info, email, max_retries=RETRY_MAX_ATTEMPTS):
    for attempt in range(max_retries):
        try:
            result = await check_email_site(client, site_name, site_info, email)
            
            http_status = result.get('http_status') or result.get('status_code')
            if http_status in RATE_LIMIT_STATUS_CODES:
                set_rate_limited(site_name, retry_after=60 * (attempt + 1))
                if attempt < max_retries - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
            
            if result.get('rateLimit'):
                set_rate_limited(site_name, retry_after=30)
                if attempt < max_retries - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
            
            result['attempts'] = attempt + 1
            result['retried'] = attempt > 0
            return result
            
        except Exception as e:
            if attempt < max_retries - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            return {
                'site': site_name,
                'name': site_name,
                'exists': False,
                'status': 'error',
                'error_message': str(e),
                'attempts': max_retries,
                'retried': attempt > 0
            }
    
    return {
        'site': site_name,
        'name': site_name,
        'exists': False,
        'status': 'failed_after_retries',
        'attempts': max_retries
    }

def increment_request_count(search_type):
    key = f"{search_type}:{datetime.now().strftime('%Y%m%d%H')}"
    search_request_counts[key] = search_request_counts.get(key, 0) + 1
    return search_request_counts[key]

def get_request_count_info(search_type):
    now = datetime.now()
    hour_key = f"{search_type}:{now.strftime('%Y%m%d%H')}"
    count = search_request_counts.get(hour_key, 0)
    return {
        'requests_this_hour': count,
        'rate_limit': 100,
        'percent_used': min(100, int(count / 100 * 100))
    }

FALSE_POSITIVE_PATTERNS = [
    (re.compile(r'\b(not found|no results?|doesn\'t exist|not exist|user not found|profile not found|account not found|page not found|404)\b', re.I), False),
    (re.compile(r'\b(invalid user|user invalid|user does not|user hasn\'t|user has no|username not)\b', re.I), False),
    (re.compile(r'\b(removed this|content removed|deleted account|this account|has been|page removed)\b', re.I), False),
    (re.compile(r'(sign up|create account|log in|login).{0,50}(to view|to see)', re.I), False),
    (re.compile(r'(view profile|profile).{0,30}(requires|need).{0,30}(login|sign in)', re.I), False),
    (re.compile(r'error|404|403|400.{0,20}(page|content)', re.I), False),
]

CONFIRMATION_PATTERNS = [
    (re.compile(r'@' + '{username}' + r'\b', re.I), True),
    (re.compile(r'"username".{0,30}' + '{username}', re.I), True),
    (re.compile(r'"name".{0,30}' + '{username}', re.I), True),
]

PLATFORM_PRIORITY = {
    'Facebook': 1,
    'GitHub': 2,
    'Telegram': 3,
    'Steam': 4,
    'TikTok': 5,
    'Pinterest': 6,
    'Instagram': 7,
    'LinkedIn': 8,
    'Twitter/X': 9,
    'YouTube': 10,
    'Reddit': 11,
    'Snapchat': 12,
    'Discord': 13,
    'Twitch': 14,
    'Spotify': 15,
    'Tumblr': 16,
}

active_searches = {}

def deduplicate_request(search_type, query, category='all'):
    key = f"{search_type}:{query}:{category}".lower()
    if key in active_searches:
        age = time.time() - active_searches[key]
        if age > 60:
            del active_searches[key]
        else:
            return None, key
    active_searches[key] = time.time()
    return key, None

def mark_search_complete(key):
    if key in active_searches:
        del active_searches[key]

def cleanup_stale_searches(max_age_seconds=300):
    now = time.time()
    stale = [k for k, v in active_searches.items() if now - v > max_age_seconds]
    for k in stale:
        del active_searches[k]

def verify_profile(response_text, username, url=None):
    """
    Verify if a response is a real profile or false positive.
    Returns: 'verified', 'likely_false', or 'unconfirmed'
    """
    if not response_text:
        return 'unconfirmed'
    
    text_lower = response_text.lower()
    username_lower = username.lower()
    
    for pattern, _ in FALSE_POSITIVE_PATTERNS:
        if pattern.search(text_lower):
            return 'likely_false'
    
    if username_lower in text_lower:
        return 'verified'
    
    url_lower = url.lower() if url else ''
    if username_lower in url_lower:
        return 'verified'
    
    generic_patterns = [
        r'(welcome|home|landing).{0,50}(page|site)',
        r'(log in|sign in|register|sign up).{0,30}(now|today)',
        r'^<!doctype html>\s*<html>\s*<head>\s*<title>\s*</title>',
    ]
    
    for pattern in generic_patterns:
        if re.search(pattern, text_lower):
            if len(response_text) < 500:
                return 'likely_false'
    
    return 'unconfirmed'


def check_ollama_available():
    """Check if Ollama is running and available."""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        return response.status_code == 200
    except:
        return False


def ollama_generate(prompt, system_prompt=None, timeout=60):
    """Generate response from Ollama."""
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 512
            }
        }
        if system_prompt:
            payload["system"] = system_prompt
        
        response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        if response.status_code == 200:
            return response.json().get("response", "").strip()
        return None
    except Exception as e:
        logger.error(f"Ollama error: {e}", exc_info=True)
        return None


def summarize_results(query, tool, findings):
    """Generate AI summary of search results."""
    if not findings:
        return "No results found to summarize."
    
    platforms = [f.get('platform') or f.get('site', 'Unknown') for f in findings if f.get('exists')]
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


def analyze_natural_language(user_query, available_tools):
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

Available tools: {', '.join(available_tools)}

Determine what the user is searching for and extract the key information."""
    
    result = ollama_generate(prompt, system_prompt, timeout=30)
    
    if result:
        try:
            return json.loads(result)
        except:
            pass
    
    return {"type": None, "query": user_query, "confidence": 0}


def enrich_profile(platform, username, available_info):
    """Generate AI insights about a found profile."""
    system_prompt = """You are a research analyst providing context about publicly listed social media platforms. Keep responses factual and under 50 words. This is read-only analysis."""
    
    info_text = ""
    if available_info.get('url'):
        info_text += f"Profile URL: {available_info['url']}\n"
    if available_info.get('bio'):
        info_text += f"Bio: {available_info['bio']}\n"
    if available_info.get('name'):
        info_text += f"Display Name: {available_info['name']}\n"
    
    prompt = f"""Platform Analysis Request:
Platform: {platform}
Username: {username}

{info_text or 'Limited information available.'}

Provide brief context about this platform (what it is, typical use cases). Keep under 40 words. Research purposes only."""
    
    return ollama_generate(prompt, system_prompt, timeout=30) or "Analysis unavailable."


http_limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
http_client = None

def get_http_client():
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(
            limits=http_limits,
            follow_redirects=True,
            timeout=httpx.Timeout(10.0, connect=5.0)
        )
    return http_client

def close_http_client():
    global http_client
    if http_client is not None:
        asyncio.run(http_client.aclose())
        http_client = None

def get_cache_key(search_type, query, category='all'):
    return f"{search_type}:{query}:{category}".lower()

def get_cached_result(search_type, query, category='all'):
    key = get_cache_key(search_type, query, category)
    if key in result_cache:
        cached = result_cache[key]
        if datetime.now() - cached['timestamp'] < timedelta(hours=CACHE_TTL_HOURS):
            cached['from_cache'] = True
            return cached['result']
        else:
            del result_cache[key]
    return None

def set_cached_result(search_type, query, result, category='all'):
    key = get_cache_key(search_type, query, category)
    result_cache[key] = {
        'result': result,
        'timestamp': datetime.now()
    }

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0'
}

SHERLOCK_DATA_URL = "https://raw.githubusercontent.com/sherlock-project/sherlock/master/sherlock_project/resources/data.json"

from version import get_version_info

maigret_db = None
search_registry = {}

def get_maigret_database():
    global maigret_db
    if maigret_db is None:
        try:
            from maigret.sites import MaigretDatabase
            import os
            maigret_db = MaigretDatabase()
            data_path = os.path.join(os.path.dirname(__import__('maigret', fromlist=['']).__file__), 'resources', 'data.json')
            maigret_db.load_from_path(data_path)
            logger.info(f"Loaded Maigret database with {len(maigret_db.sites)} sites")
        except Exception as e:
            logger.error(f"Failed to load Maigret database: {e}", exc_info=True)
            maigret_db = None
    return maigret_db

class SearchJob:
    def __init__(self, job_id):
        self.job_id = job_id
        self.cancelled = False
        self.progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
        self.result = None
        self.completed = False
    
    def cancel(self):
        self.cancelled = True
    
    def should_stop(self):
        return self.cancelled

def get_maigret_sites_dict():
    db = get_maigret_database()
    if db:
        return db.sites_dict
    return {}

@app.route('/api/version', methods=['GET'])
def get_version():
    return jsonify(get_version_info())


@app.route('/api/ai/status', methods=['GET'])
def ai_status():
    """Check if Ollama AI is available."""
    available = check_ollama_available()
    return jsonify({
        'available': available,
        'model': OLLAMA_MODEL if available else None,
        'message': 'AI features ready' if available else 'Ollama not running. Install from https://ollama.com'
    })


@app.route('/api/ai/summarize', methods=['POST'])
def ai_summarize():
    """Generate AI summary of search results."""
    data = request.get_json()
    query = data.get('query', '')
    tool = data.get('tool', 'unknown')
    findings = data.get('findings', [])
    
    if not check_ollama_available():
        return jsonify({'error': 'Ollama not available'}), 503
    
    summary = summarize_results(query, tool, findings)
    return jsonify({'summary': summary})


@app.route('/api/ai/analyze-query', methods=['POST'])
def ai_analyze_query():
    """Convert natural language to search parameters."""
    data = request.get_json()
    user_query = data.get('query', '')
    
    if not check_ollama_available():
        return jsonify({'error': 'Ollama not available'}), 503
    
    available_tools = ['social', 'email', 'username', 'maigret', 'phone', 'person', 'ip', 'domain']
    result = analyze_natural_language(user_query, available_tools)
    return jsonify(result)


@app.route('/api/ai/enrich-profile', methods=['POST'])
def ai_enrich_profile():
    """Generate AI insights for a profile."""
    data = request.get_json()
    platform = data.get('platform', 'Unknown')
    username = data.get('username', '')
    info = data.get('info', {})
    
    if not check_ollama_available():
        return jsonify({'error': 'Ollama not available'}), 503
    
    analysis = enrich_profile(platform, username, info)
    return jsonify({'analysis': analysis})


from search_history import search_history

@app.route('/api/history', methods=['GET'])
def get_history():
    return jsonify(search_history.get_history(limit=50))

@app.route('/api/archive', methods=['GET'])
def get_archive():
    query = request.args.get('q', '')
    tool = request.args.get('tool', '')
    limit = int(request.args.get('limit', 100))
    return jsonify(search_history.get_archive(limit=limit, search_query=query, search_tool=tool if tool else None))

@app.route('/api/history/archive/<entry_id>', methods=['POST'])
def archive_entry(entry_id):
    search_history.archive_entry(entry_id)
    return jsonify({'success': True})

@app.route('/api/history/archive-all', methods=['POST'])
def archive_all():
    count = search_history.archive_all()
    return jsonify({'success': True, 'archived_count': count})

@app.route('/api/history/mark-read/<entry_id>', methods=['POST'])
def mark_read(entry_id):
    search_history.mark_read(entry_id)
    return jsonify({'success': True})

@app.route('/api/history/mark-all-read', methods=['POST'])
def mark_all_read():
    search_history.mark_all_read()
    return jsonify({'success': True})

@app.route('/api/history/stats', methods=['GET'])
def get_history_stats():
    return jsonify(search_history.get_stats())


@app.route('/api/search/stop/<job_id>', methods=['POST'])
def stop_search(job_id):
    if job_id in search_registry:
        search_registry[job_id].cancel()
        return jsonify({'success': True, 'job_id': job_id})
    return jsonify({'success': False, 'error': 'Job not found'}), 404


@app.route('/api/search/progress/<job_id>', methods=['GET'])
def get_search_progress(job_id):
    if job_id in search_registry:
        job = search_registry[job_id]
        return jsonify({
            'job_id': job_id,
            'cancelled': job.cancelled,
            'completed': job.completed,
            'progress': job.progress_state,
            'has_results': job.result is not None
        })
    return jsonify({'error': 'Job not found'}), 404


@lru_cache(maxsize=1)
def get_sherlock_sites():
    try:
        response = requests.get(SHERLOCK_DATA_URL, timeout=30)
        if response.status_code == 200:
            data = response.json()
            data.pop('$schema', None)
            return data
    except Exception as e:
        logger.error(f"Failed to fetch Sherlock sites: {e}", exc_info=True)
    return {}

def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_ip(ip):
    try:
        socket.inet_aton(ip)
        return True
    except socket.error:
        return False

def validate_domain(domain):
    pattern = r'^[a-zA-Z0-9][a-zA-Z0-9-]{0,61}[a-zA-Z0-9]?\.[a-zA-Z]{2,}$'
    return re.match(pattern, domain) is not None

def interpolate_string(input_object, username):
    if isinstance(input_object, str):
        return input_object.replace("{}", username.replace(' ', '%20'))
    elif isinstance(input_object, dict):
        return {k: interpolate_string(v, username) for k, v in input_object.items()}
    elif isinstance(input_object, list):
        return [interpolate_string(i, username) for i in input_object]
    return input_object

def check_site_email(client, email, site_info):
    name, url, check_function = site_info
    try:
        return asyncio.run(check_function(client, email, url))
    except Exception as e:
        return {
            'name': name,
            'domain': url,
            'exists': False,
            'rateLimit': False,
            'error': str(e)
        }

async def check_github(client, email, url):
    api_url = f"https://api.github.com/search/user?q={email}"
    response = await client.get(api_url, headers=HEADERS, timeout=10)
    if response.status_code == 200:
        data = response.json()
        if data.get('total_count', 0) > 0:
            user = data['items'][0]
            return {
                'name': 'GitHub',
                'domain': 'github.com',
                'exists': True,
                'rateLimit': False,
                'username': user.get('login'),
                'profile_url': user.get('html_url'),
                'avatar': user.get('avatar_url')
            }
    return {
        'name': 'GitHub',
        'domain': 'github.com',
        'exists': False,
        'rateLimit': response.status_code == 403
    }

async def check_twitter(client, email, url):
    try:
        response = await client.post(
            'https://api.twitter.com/1.1/account/settings.json',
            headers={'Authorization': 'Basic cmVhZDphcGk='},
            timeout=10
        )
    except:
        pass
    return {
        'name': 'Twitter/X',
        'domain': 'twitter.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Direct API requires authentication. Use web search.'
    }

async def check_instagram(client, email, url):
    return {
        'name': 'Instagram',
        'domain': 'instagram.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Direct API requires authentication. Use web search.'
    }

async def check_linkedin(client, email, url):
    return {
        'name': 'LinkedIn',
        'domain': 'linkedin.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Direct API requires authentication. Use web search.'
    }

async def check_discord(client, email, url):
    try:
        response = await client.post(
            'https://discord.com/api/v9/auth/login',
            json={'email': email},
            headers=HEADERS,
            timeout=10
        )
        data = response.json()
        if response.status_code == 200:
            return {'name': 'Discord', 'domain': 'discord.com', 'exists': True, 'rateLimit': False}
        elif 'captcha' in str(data).lower() or response.status_code == 400:
            return {'name': 'Discord', 'domain': 'discord.com', 'exists': False, 'rateLimit': False}
    except:
        pass
    return {'name': 'Discord', 'domain': 'discord.com', 'exists': None, 'rateLimit': True}

async def check_reddit(client, email, url):
    try:
        response = await client.get(
            f'https://www.reddit.com/.json',
            headers=HEADERS,
            timeout=10
        )
    except:
        pass
    return {
        'name': 'Reddit',
        'domain': 'reddit.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Use web search to check Reddit for this email.'
    }

async def check_netflix(client, email, url):
    try:
        response = await client.post(
            'https://api.netflix.com/api/type Pist',
            json={'email': email},
            headers=HEADERS,
            timeout=10
        )
    except:
        pass
    return {
        'name': 'Netflix',
        'domain': 'netflix.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Direct API restricted. Use web search.'
    }

async def check_spotify(client, email, url):
    try:
        response = await client.post(
            'https://spclient.wg.spotify.com/signup/public/v1/account',
            json={'email': email},
            headers=HEADERS,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 20:
                return {'name': 'Spotify', 'domain': 'spotify.com', 'exists': True, 'rateLimit': False}
            elif data.get('status') == 1:
                return {'name': 'Spotify', 'domain': 'spotify.com', 'exists': False, 'rateLimit': False}
    except:
        pass
    return {'name': 'Spotify', 'domain': 'spotify.com', 'exists': None, 'rateLimit': True}

async def check_steam(client, email, url):
    try:
        response = await client.post(
            'https://steamcommunity.com/login/getrsakey',
            data={'username': email},
            headers=HEADERS,
            timeout=10
        )
    except:
        pass
    return {
        'name': 'Steam',
        'domain': 'steamcommunity.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Direct API restricted. Use web search.'
    }

async def check_tiktok(client, email, url):
    return {
        'name': 'TikTok',
        'domain': 'tiktok.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Use web search to check TikTok for this email.'
    }

async def check_paypal(client, email, url):
    return {
        'name': 'PayPal',
        'domain': 'paypal.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Use web search to check PayPal for this email.'
    }

async def check_amazon(client, email, url):
    return {
        'name': 'Amazon',
        'domain': 'amazon.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Use web search to check Amazon for this email.'
    }

async def check_ebay(client, email, url):
    return {
        'name': 'eBay',
        'domain': 'ebay.com',
        'exists': None,
        'rateLimit': True,
        'note': 'Use web search to check eBay for this email.'
    }

async def check_email_site(client, site_name, site_info, email):
    finding = {
        'name': site_name,
        'domain': site_info.get('urlMain', site_info.get('url', '')),
        'exists': None,
        'rateLimit': False,
        'status': 'checking'
    }
    
    url = interpolate_string(site_info.get('url', ''), email)
    finding['url'] = url
    
    try:
        response = await client.head(url, headers=HEADERS, timeout=10, follow_redirects=True)
        finding['http_status'] = response.status_code
        
        if response.status_code == 200:
            finding['exists'] = True
            finding['status'] = 'found'
        else:
            finding['exists'] = False
            finding['status'] = 'not_found'
            
    except httpx.TimeoutException:
        finding['status'] = 'timeout'
        finding['rateLimit'] = True
    except httpx.ConnectError:
        finding['status'] = 'connection_error'
    except Exception:
        finding['status'] = 'error'
    
    return finding


async def search_email_async(email, progress_callback=None):
    cached = get_cached_result('email_sherlock', email)
    if cached:
        cached['from_cache'] = True
        return cached
    
    increment_request_count('email_sherlock')
    
    result = {
        'email': email,
        'valid_format': validate_email(email),
        'provider': email.split('@')[1] if '@' in email else None,
        'mx_records': None,
        'disposable': False,
        'account_checks': [],
        'search_links': [],
        'rate_limit_status': [],
        'retried_checks': 0,
        'from_cache': False
    }
    
    if not result['valid_format']:
        return result
    
    domain = result['provider']
    
    try:
        mx_records = socket.getaddrinfo(domain, 25)
        result['mx_records'] = [r[3][0] for r in mx_records[:3]]
    except:
        result['mx_records'] = []
    
    disposable_domains = ['tempmail.com', 'guerrillamail.com', 'mailinator.com', '10minutemail.com', 'throwaway.email', 'temp-mail.org', 'fakeinbox.com', 'maildrop.cc', 'yopmail.com', 'sharklasers.com']
    result['disposable'] = any(d in domain.lower() for d in disposable_domains)
    
    email_sites = get_sherlock_sites()
    
    all_checks = []
    total_sites = len(email_sites)
    checked = 0
    found_count = 0
    retried_count = 0
    
    rate_limits_hit = []
    
    batch_size = 30
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        site_items = list(email_sites.items())
        
        for i in range(0, total_sites, batch_size):
            batch = site_items[i:i + batch_size]
            tasks = []
            for site_name, site_info in batch:
                limited, limit_data = is_rate_limited(site_name)
                if limited:
                    rate_limits_hit.append({'site': site_name, 'wait': limit_data['reset_at']})
                    tasks.append(asyncio.coroutine(lambda sn=site_name, si=site_info: {
                        'site': sn, 'name': sn, 'exists': None, 'status': 'rate_limited', 'rateLimit': True
                    })())
                else:
                    tasks.append(check_site_with_retry(client, site_name, site_info, email))
            
            for site_name, site_info, task in zip([s[0] for s in batch], [s[1] for s in batch], tasks):
                try:
                    r = await asyncio.wait_for(task, timeout=15)
                    if r.get('retried'):
                        retried_count += 1
                    if r.get('rateLimit'):
                        set_rate_limited(site_name)
                    all_checks.append(r)
                    if r.get('exists') == True:
                        found_count += 1
                except (asyncio.TimeoutError, Exception):
                    all_checks.append({
                        'site': site_name,
                        'exists': False,
                        'status': 'error'
                    })
                checked += 1
                if progress_callback:
                    progress_callback({
                        'checked': checked,
                        'total': total_sites,
                        'found': found_count,
                        'percent': int((checked / total_sites) * 100),
                        'current_site': site_name
                    })
    
    result['account_checks'] = all_checks
    result['found_count'] = sum(1 for c in all_checks if c.get('exists') == True)
    result['rate_limited'] = sum(1 for c in all_checks if c.get('rateLimit') == True)
    result['retried_checks'] = retried_count
    result['rate_limit_sites'] = get_rate_limit_status()
    
    result['search_links'] = [
        {'name': 'Hunter.io', 'url': f'https://hunter.io/search/{email}'},
        {'name': 'EmailRep', 'url': f'https://emailrep.io/{email}'},
        {'name': 'Have I Been Pwned', 'url': f'https://haveibeenpwned.com/unverifiedpwned?q={email}'},
        {'name': 'Google', 'url': f'https://www.google.com/search?q="{email}"'},
        {'name': 'Dehashed', 'url': f'https://dehashed.com/search?query={email}'},
    ]
    
    set_cached_result('email_sherlock', email, result.copy())
    
    return result


def lookup_email(email):
    return asyncio.run(search_email_async(email))


async def search_email_holehe(email, progress_callback=None):
    from holehe.core import launch_module, import_submodules, get_functions
    from argparse import Namespace
    
    result = {
        'email': email,
        'valid_format': validate_email(email),
        'method': 'holehe',
        'holehe_results': [],
        'found_count': 0,
        'rate_limited_count': 0
    }
    
    if not result['valid_format']:
        return result
    
    out = []
    checked = 0
    
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    total = len(websites)
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        for website in websites:
            website_name = website.__name__
            try:
                await launch_module(website, email, client, out)
                checked += 1
                
                if progress_callback:
                    progress_callback({
                        'checked': checked,
                        'total': total,
                        'found': len([x for x in out if x.get('exists')]),
                        'percent': int((checked / total) * 100),
                        'current_site': website_name
                    })
            except Exception as e:
                checked += 1
                out.append({
                    'name': website_name,
                    'exists': False,
                    'error': True
                })
    
    found = []
    rate_limited = []
    not_found = []
    
    for item in out:
        site_data = {
            'site': item.get('name', item.get('Name', 'Unknown')),
            'domain': item.get('domain', ''),
            'exists': item.get('exists', False),
            'rateLimit': item.get('rateLimit', False),
            'error': item.get('error', False),
            'emailrecovery': item.get('emailrecovery', None),
            'phoneNumber': item.get('phoneNumber', None),
            'details': item.get('details', {})
        }
        
        if item.get('exists'):
            found.append(site_data)
        elif item.get('rateLimit'):
            rate_limited.append(site_data)
        else:
            not_found.append(site_data)
    
    result['holehe_results'] = out
    result['found'] = found
    result['rate_limited'] = rate_limited
    result['not_found'] = not_found
    result['found_count'] = len(found)
    result['rate_limited_count'] = len(rate_limited)
    result['total_checked'] = len(out)
    
    return result


def lookup_email_holehe(email):
    return asyncio.run(search_email_holehe(email))


async def search_email_combined(email, progress_callback=None):
    sherlock_result = None
    holehe_result = None
    sherlock_done = False
    holehe_done = False
    
    async def run_sherlock():
        nonlocal sherlock_result, sherlock_done
        try:
            sherlock_result = await search_email_async(email, progress_callback)
        except Exception as e:
            sherlock_result = {'error': str(e)}
        finally:
            sherlock_done = True
    
    async def run_holehe():
        nonlocal holehe_result, holehe_done
        try:
            holehe_result = await search_email_holehe(email, progress_callback)
        except Exception as e:
            holehe_result = {'error': str(e)}
        finally:
            holehe_done = True
    
    await asyncio.gather(run_sherlock(), run_holehe())
    
    combined = {
        'email': email,
        'valid_format': validate_email(email),
        'provider': email.split('@')[1] if '@' in email else None,
        'mx_records': None,
        'disposable': False,
        'search_links': []
    }
    
    if combined['valid_format']:
        domain = combined['provider']
        try:
            mx_records = socket.getaddrinfo(domain, 25)
            combined['mx_records'] = [r[3][0] for r in mx_records[:3]]
        except:
            combined['mx_records'] = []
        
        disposable_domains = ['tempmail.com', 'guerrillamail.com', 'mailinator.com', '10minutemail.com', 'throwaway.email', 'temp-mail.org', 'fakeinbox.com', 'maildrop.cc', 'yopmail.com', 'sharklasers.com']
        combined['disposable'] = any(d in domain.lower() for d in disposable_domains)
    
    combined['sherlock'] = sherlock_result or {'error': 'Sherlock search failed'}
    combined['holehe'] = holehe_result or {'error': 'Holehe search failed'}
    
    sherlock_found = combined['sherlock'].get('found_count', 0)
    holehe_found = combined['holehe'].get('found_count', 0)
    combined['found_count'] = sherlock_found + holehe_found
    
    sherlock_accounts = combined['sherlock'].get('account_checks', [])
    holehe_accounts = combined['holehe'].get('found', [])
    
    combined['cross_validated'] = cross_validate_results(sherlock_accounts, holehe_accounts)
    
    cross_validated_count = sum(1 for r in combined['cross_validated'] if r.get('cross_validated'))
    combined['cross_validated_count'] = cross_validated_count
    
    combined['search_links'] = [
        {'name': 'Hunter.io', 'url': f'https://hunter.io/search/{email}'},
        {'name': 'EmailRep', 'url': f'https://emailrep.io/{email}'},
        {'name': 'Have I Been Pwned', 'url': f'https://haveibeenpwned.com/unverifiedpwned?q={email}'},
        {'name': 'Google', 'url': f'https://www.google.com/search?q="{email}"'},
        {'name': 'Dehashed', 'url': f'https://dehashed.com/search?query={email}'},
    ]
    
    return combined


def lookup_email_combined(email):
    return asyncio.run(search_email_combined(email))


def calculate_confidence_score(result, source=None, cross_validated=False):
    """
    Calculate confidence score (0-100) for a search result.
    
    Factors:
    - Source verification (Holehe = higher, Sherlock = medium)
    - Cross-validation (both tools found = much higher)
    - HTTP status code
    - Rate limiting (lowers confidence)
    - Additional data available (recovery email, etc.)
    """
    score = 50
    
    if cross_validated:
        score += 30
    elif source == 'holehe':
        score += 15
    elif source == 'sherlock':
        score += 5
    
    if result.get('exists') == True:
        score += 15
    elif result.get('exists') == False:
        score -= 10
    
    http_status = result.get('http_status') or result.get('status_code')
    if http_status == 200:
        score += 10
    elif http_status and http_status != 200:
        score -= 5
    
    if result.get('rateLimit') or result.get('rate_limit'):
        score -= 20
    
    if result.get('emailrecovery'):
        score += 10
    
    if result.get('verification') == 'verified':
        score += 15
    elif result.get('verification') == 'likely_false':
        score -= 30
    
    return max(0, min(100, score))


def cross_validate_results(sherlock_results, holehe_results):
    """
    Cross-validate results from both tools.
    Returns combined list with cross-validation info.
    """
    if not sherlock_results and not holehe_results:
        return []
    
    sherlock_sites = {}
    holehe_sites = {}
    
    for r in (sherlock_results or []):
        site_name = (r.get('site') or r.get('name') or 'Unknown').lower()
        r['found_by'] = ['sherlock']
        r['cross_validated'] = False
        r['confidence'] = calculate_confidence_score(r, 'sherlock')
        sherlock_sites[site_name] = r
    
    for r in (holehe_results or []):
        site_name = (r.get('site') or r.get('name') or 'Unknown').lower()
        r['found_by'] = ['holehe']
        r['cross_validated'] = False
        r['confidence'] = calculate_confidence_score(r, 'holehe')
        holehe_sites[site_name] = r
    
    combined = []
    all_sites = set(sherlock_sites.keys()) | set(holehe_sites.keys())
    
    for site_name in all_sites:
        sherlock_r = sherlock_sites.get(site_name)
        holehe_r = holehe_sites.get(site_name)
        
        if sherlock_r and holehe_r:
            merged = {
                'site': site_name.title(),
                'exists': True,
                'found_by': ['sherlock', 'holehe'],
                'cross_validated': True,
                'confidence': calculate_confidence_score(sherlock_r, 'both', cross_validated=True),
                'sherlock_status': sherlock_r.get('http_status') or sherlock_r.get('status'),
                'holehe_status': 'exists' if holehe_r.get('exists') else 'not_found',
                'url': sherlock_r.get('url'),
                'emailrecovery': holehe_r.get('emailrecovery'),
                'rateLimit': sherlock_r.get('rateLimit') or holehe_r.get('rateLimit')
            }
            combined.append(merged)
        elif sherlock_r:
            combined.append({
                'site': site_name.title(),
                'exists': sherlock_r.get('exists'),
                'found_by': ['sherlock'],
                'cross_validated': False,
                'confidence': calculate_confidence_score(sherlock_r, 'sherlock'),
                'sherlock_status': sherlock_r.get('http_status') or sherlock_r.get('status'),
                'url': sherlock_r.get('url'),
                'rateLimit': sherlock_r.get('rateLimit')
            })
        elif holehe_r:
            combined.append({
                'site': site_name.title(),
                'exists': holehe_r.get('exists'),
                'found_by': ['holehe'],
                'cross_validated': False,
                'confidence': calculate_confidence_score(holehe_r, 'holehe'),
                'holehe_status': 'exists' if holehe_r.get('exists') else 'not_found',
                'emailrecovery': holehe_r.get('emailrecovery'),
                'rateLimit': holehe_r.get('rateLimit')
            })
    
    combined.sort(key=lambda x: (-x['cross_validated'], -x['confidence']))
    
    return combined


def lookup_ip(ip_address):
    result = {
        'ip': ip_address,
        'valid': validate_ip(ip_address),
        'reverse_dns': None,
        'geolocation': None,
        'whois': None,
        'ports': [],
        'reputation_score': 0
    }
    
    if result['valid']:
        try:
            result['reverse_dns'] = socket.gethostbyaddr(ip_address)[0]
        except:
            result['reverse_dns'] = 'N/A'
        
        try:
            response = requests.get(f'http://ip-api.com/json/{ip_address}', timeout=5)
            if response.status_code == 200:
                data = response.json()
                result['geolocation'] = {
                    'country': data.get('country', 'N/A'),
                    'region': data.get('regionName', 'N/A'),
                    'city': data.get('city', 'N/A'),
                    'isp': data.get('isp', 'N/A'),
                    'org': data.get('org', 'N/A'),
                    'as': data.get('as', 'N/A'),
                    'lat': data.get('lat', 0),
                    'lon': data.get('lon', 0)
                }
        except Exception as e:
            result['error'] = str(e)
        
        common_ports = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 993, 995, 3306, 3389, 5432, 8080]
        for port in common_ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            try:
                if sock.connect_ex((ip_address, port)) == 0:
                    result['ports'].append(port)
            except:
                pass
            finally:
                sock.close()
        
        blacklisted_ips = ['185.220.101', '192.42.116', '104.244.73']
        result['reputation_score'] = 100
        for bl in blacklisted_ips:
            if ip_address.startswith(bl):
                result['reputation_score'] -= 30
        
        if len(result['ports']) > 5:
            result['reputation_score'] -= 10
        
    return result

def lookup_domain(domain):
    result = {
        'domain': domain,
        'valid': validate_domain(domain),
        'ip_addresses': [],
        'dns_records': {},
        'whois': None,
        'subdomains': [],
        'ssl_info': None
    }
    
    if result['valid']:
        try:
            result['ip_addresses'] = list(set(socket.getaddrinfo(domain, 80, socket.AF_INET, socket.SOCK_STREAM)))
            result['ip_addresses'] = [r[4][0] for r in result['ip_addresses']]
        except:
            result['ip_addresses'] = []
        
        try:
            dns_types = ['A', 'AAAA', 'MX', 'NS', 'TXT', 'CNAME']
            for dns_type in dns_types:
                try:
                    if dns_type == 'A':
                        result['dns_records']['A'] = socket.getaddrinfo(domain, 80, socket.AF_INET)[0][4][0]
                    elif dns_type == 'AAAA':
                        try:
                            result['dns_records']['AAAA'] = socket.getaddrinfo(domain, 80, socket.AF_INET6)[0][4][0]
                        except:
                            result['dns_records']['AAAA'] = 'N/A'
                    else:
                        result['dns_records'][dns_type] = 'Not implemented'
                except:
                    result['dns_records'][dns_type] = 'N/A'
        except Exception as e:
            result['error'] = str(e)
        
        common_subdomains = ['www', 'mail', 'ftp', 'admin', 'blog', 'dev', 'api', 'test', 'staging']
        for sub in common_subdomains:
            try:
                full_domain = f"{sub}.{domain}"
                socket.getaddrinfo(full_domain, 80, socket.AF_INET)
                result['subdomains'].append(full_domain)
            except:
                pass
        
        try:
            import ssl
            context = ssl.create_default_context()
            with socket.create_connection((domain, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    result['ssl_info'] = {
                        'issuer': dict(x[0] for x in cert['issuer']),
                        'subject': dict(x[0] for x in cert['subject']),
                        'version': cert['version'],
                        'not_before': cert['notBefore'],
                        'not_after': cert['notAfter']
                    }
        except:
            result['ssl_info'] = 'SSL info unavailable'
    
    return result

async def check_username_async(client, platform, info, username):
    url = info['url']
    finding = {
        'platform': platform,
        'url': url,
        'exists': None,
        'checked_at': datetime.now().isoformat()
    }
    
    try:
        if info['type'] == 'api' and 'github' in url:
            response = await client.get(url, timeout=5, headers=HEADERS)
            finding['exists'] = response.status_code == 200
            if finding['exists']:
                data = response.json()
                finding['details'] = {
                    'public_repos': data.get('public_repos', 0),
                    'followers': data.get('followers', 0),
                    'following': data.get('following', 0),
                    'name': data.get('name'),
                    'bio': data.get('bio')
                }
                finding['verified'] = True
        elif info['type'] == 'api':
            response = await client.get(url, timeout=5, headers=HEADERS)
            finding['exists'] = response.status_code == 200
            finding['verified'] = True
        else:
            response = await client.head(url, timeout=3, allow_redirects=True, headers=HEADERS)
            finding['exists'] = response.status_code != 404
            if finding['exists'] and response.status_code == 200:
                verification = verify_profile('', username, url)
                finding['verification'] = verification
                if verification == 'likely_false':
                    finding['exists'] = None
                    finding['status'] = 'unverified'
    except httpx.TimeoutException:
        finding['exists'] = 'Timeout'
    except httpx.ConnectError:
        finding['exists'] = 'Connection Error'
    except Exception:
        finding['exists'] = 'Unknown'
    
    return finding

async def check_sherlock_site(client, site_name, site_info, username):
    finding = {
        'platform': site_name,
        'url': '',
        'exists': None,
        'status': 'unknown',
        'http_status': None
    }
    
    regex_check = site_info.get('regexCheck')
    if regex_check:
        try:
            if not re.search(regex_check, username):
                finding['status'] = 'invalid_username'
                finding['exists'] = False
                return finding
        except:
            pass
    
    url = interpolate_string(site_info.get('url', ''), username)
    finding['url'] = url
    
    request_method = site_info.get('request_method', 'GET').upper()
    request_payload = site_info.get('request_payload', {})
    request_payload = interpolate_string(request_payload, username)
    
    headers = dict(HEADERS)
    if 'headers' in site_info:
        headers.update(site_info['headers'])
    
    try:
        if request_method == 'GET':
            response = await client.get(url, headers=headers, timeout=5, follow_redirects=True)
        elif request_method == 'HEAD':
            response = await client.head(url, headers=headers, timeout=10, follow_redirects=True)
        elif request_method == 'POST':
            response = await client.post(url, headers=headers, json=request_payload, timeout=10, follow_redirects=True)
        else:
            response = await client.get(url, headers=headers, timeout=5, follow_redirects=True)
        
        finding['http_status'] = response.status_code
        
        response_text = response.text if hasattr(response, 'text') else ''
        
        if 'error' in site_info:
            if site_info['error'] in response_text:
                finding['status'] = 'not_found'
                finding['exists'] = False
                finding['verified'] = True
            else:
                verification = verify_profile(response_text, username, url)
                finding['verification'] = verification
                if verification == 'likely_false':
                    finding['status'] = 'unverified'
                    finding['exists'] = None
                else:
                    finding['status'] = 'found'
                    finding['exists'] = True
        elif 'success' in site_info:
            if site_info['success'] in response_text:
                verification = verify_profile(response_text, username, url)
                finding['verification'] = verification
                if verification == 'likely_false':
                    finding['status'] = 'unverified'
                    finding['exists'] = None
                else:
                    finding['status'] = 'found'
                    finding['exists'] = True
            else:
                finding['status'] = 'not_found'
                finding['exists'] = False
                finding['verified'] = True
        else:
            if response.status_code == 200:
                verification = verify_profile(response_text, username, url)
                finding['verification'] = verification
                if verification == 'likely_false':
                    finding['status'] = 'unverified'
                    finding['exists'] = None
                elif verification == 'verified':
                    finding['exists'] = True
                    finding['status'] = 'found'
                else:
                    if 'username' in site_info or site_info.get('checkType') == 'status':
                        finding['exists'] = True
                        finding['status'] = 'found'
                    else:
                        finding['exists'] = True
                        finding['status'] = 'found'
            elif response.status_code == 404:
                finding['exists'] = False
                finding['status'] = 'not_found'
                finding['verified'] = True
            else:
                finding['exists'] = response.status_code != 404
                finding['status'] = 'unknown'
                finding['status'] = 'unknown'
                
    except httpx.TimeoutException:
        finding['status'] = 'timeout'
        finding['exists'] = None
    except httpx.ConnectError:
        finding['status'] = 'connection_error'
        finding['exists'] = None
    except Exception as e:
        finding['status'] = 'error'
        finding['exists'] = None
    
    return finding

async def search_username_async(username, progress_callback=None, max_sites=150):
    sherlock_sites = get_sherlock_sites()
    
    if not sherlock_sites:
        return {
            'username': username,
            'platforms_checked': 0,
            'findings': [],
            'error': 'Could not load Sherlock site data'
        }
    
    sites_list = list(sherlock_sites.items())[:max_sites]
    all_findings = []
    total_sites = len(sites_list)
    checked = 0
    found_count = 0
    
    batch_size = 30
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        for i in range(0, total_sites, batch_size):
            batch = sites_list[i:i + batch_size]
            tasks = []
            for site_name, site_info in batch:
                tasks.append(check_sherlock_site(client, site_name, site_info, username))
            
            for site_name, site_info, task in zip([s[0] for s in batch], [s[1] for s in batch], tasks):
                try:
                    result = await asyncio.wait_for(task, timeout=10)
                    all_findings.append(result)
                    if result.get('exists') == True:
                        found_count += 1
                except (asyncio.TimeoutError, Exception):
                    all_findings.append({
                        'site': site_name,
                        'exists': False,
                        'status': 'error'
                    })
                checked += 1
                if progress_callback:
                    progress_callback({
                        'checked': checked,
                        'total': total_sites,
                        'found': found_count,
                        'percent': int((checked / total_sites) * 100),
                        'current_site': site_name
                    })
    
    result = {
        'username': username,
        'platforms_checked': total_sites,
        'findings': all_findings
    }
    
    result['found_count'] = sum(1 for f in all_findings if f.get('exists') == True)
    result['not_found_count'] = sum(1 for f in all_findings if f.get('exists') == False)
    result['invalid_count'] = sum(1 for f in all_findings if f.get('status') == 'invalid_username')
    result['error_count'] = sum(1 for f in all_findings if f.get('status') in ['timeout', 'connection_error', 'error', 'unknown'])
    
    return result


def search_username(username):
    return asyncio.run(search_username_async(username))


def search_username_maigret(username, progress_callback=None, max_sites=500):
    try:
        import maigret.maigret as maigret_module
        import logging
        
        db = get_maigret_database()
        if not db:
            return {
                'username': username,
                'platforms_checked': 0,
                'findings': [],
                'error': 'Could not load Maigret database'
            }
        
        logger = logging.getLogger('maigret')
        logger.setLevel(logging.WARNING)
        
        sites_list = sorted(db.sites, key=lambda x: getattr(x, 'rank', 9999) if hasattr(x, 'rank') else 9999)
        limited_sites = sites_list[:max_sites]
        limited_dict = {site.name: site for site in limited_sites}
        
        class ProgressNotifier:
            def __init__(self, callback, total):
                self.callback = callback
                self.checked = 0
                self.total = total
                
            def update(self, checked, total, found=None):
                self.checked = checked
                if self.callback:
                    self.callback({
                        'checked': checked,
                        'total': total,
                        'found': found if found else 0,
                        'percent': int((checked / total) * 100) if total > 0 else 0,
                        'current_site': 'maigret'
                    })
        
        notifier = ProgressNotifier(progress_callback, len(limited_dict)) if progress_callback else None
        
        results = asyncio.run(maigret_module.maigret(
            username=username,
            site_dict=limited_dict,
            logger=logger,
            query_notify=notifier,
            timeout=2,
            is_parsing_enabled=False,
            max_connections=30,
            no_progressbar=True
        ))
        
        findings = []
        found_count = 0
        
        for site_name, site_result in results.items():
            exists = site_result.get('exists', False)
            status = site_result.get('status', 'unknown')
            
            finding = {
                'site': site_name,
                'url': site_result.get('url_user') or site_result.get('url_main', ''),
                'exists': exists,
                'status': status,
                'http_status': site_result.get('http_status'),
                'rank': site_result.get('rank')
            }
            
            if exists:
                found_count += 1
                
            findings.append(finding)
        
        return {
            'username': username,
            'platforms_checked': len(findings),
            'findings': findings,
            'found_count': found_count,
            'method': 'maigret',
            'total_sites_available': len(db.sites)
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            'username': username,
            'platforms_checked': 0,
            'findings': [],
            'error': str(e)
        }


async def scrape_search_results(client, search_url, engine_name):
    finding = {
        'engine': engine_name,
        'url': search_url,
        'results': [],
        'status': 'scraping'
    }
    
    try:
        response = await client.get(search_url, headers=HEADERS, timeout=15, follow_redirects=True)
        finding['status_code'] = response.status_code
        
        if response.status_code == 200:
            html = response.text
            
            if 'google' in engine_name.lower():
                finding['results'] = extract_google_results(html)
            elif 'yandex' in engine_name.lower():
                finding['results'] = extract_yandex_results(html)
            elif 'bing' in engine_name.lower():
                finding['results'] = extract_bing_results(html)
            elif 'duckduckgo' in engine_name.lower():
                finding['results'] = extract_duckduckgo_results(html)
            else:
                finding['results'] = extract_generic_results(html)
            
            finding['status'] = 'complete'
        else:
            finding['status'] = 'error'
            
    except httpx.TimeoutException:
        finding['status'] = 'timeout'
    except httpx.ConnectError:
        finding['status'] = 'connection_error'
    except Exception as e:
        finding['status'] = 'error'
        finding['error'] = str(e)
    
    return finding


def extract_google_results(html):
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        for item in soup.select('div.g')[:10]:
            title_elem = item.select_one('h3')
            link_elem = item.select_one('a[href^="https://"]')
            snippet_elem = item.select_one('div[data-sncf]') or item.select_one('span.aCOpRe') or item.select_one('div.VwiC3b')
            
            if title_elem and link_elem:
                href = link_elem.get('href', '')
                
                if href.startswith('/url?q='):
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href.split('?')[1]).query)
                    href = parsed.get('q', [href])[0]
                elif href.startswith('/l/') or href.startswith('/aclk') or href.startswith('/search'):
                    continue
                
                if href and href.startswith('http') and 'google.com' not in href:
                    results.append({
                        'title': title_elem.get_text()[:200],
                        'url': href,
                        'snippet': snippet_elem.get_text()[:300] if snippet_elem else ''
                    })
    except:
        pass
    return results


def extract_yandex_results(html):
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        for item in soup.select('li.serp-item')[:10]:
            title_elem = item.select_one('h2 a') or item.select_one('. OrganicTitle')
            link_elem = item.select_one('h2 a') or item.select_one('a.link')
            snippet_elem = item.select_one('. OrganicTextContentSpan')
            
            if title_elem:
                results.append({
                    'title': title_elem.get_text()[:200],
                    'url': link_elem.get('href', '') if link_elem else '',
                    'snippet': snippet_elem.get_text()[:300] if snippet_elem else ''
                })
    except:
        pass
    return results


def extract_bing_results(html):
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        for item in soup.select('li.b_algo')[:10]:
            title_elem = item.select_one('h2 a')
            link_elem = item.select_one('h2 a')
            snippet_elem = item.select_one('p')
            
            if title_elem:
                results.append({
                    'title': title_elem.get_text()[:200],
                    'url': link_elem.get('href', '') if link_elem else '',
                    'snippet': snippet_elem.get_text()[:300] if snippet_elem else ''
                })
    except:
        pass
    return results


def extract_duckduckgo_results(html):
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        for item in soup.select('div.result')[:10]:
            title_elem = item.select_one('h2 a')
            link_elem = item.select_one('h2 a')
            snippet_elem = item.select_one('a.summary')
            
            if title_elem:
                results.append({
                    'title': title_elem.get_text()[:200],
                    'url': link_elem.get('href', '') if link_elem else '',
                    'snippet': snippet_elem.get_text()[:300] if snippet_elem else ''
                })
    except:
        pass
    return results


def extract_generic_results(html):
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        for item in soup.select('a')[:20]:
            href = item.get('href', '')
            if href.startswith('http') and len(href) > 20:
                text = item.get_text().strip()
                if len(text) > 10:
                    results.append({
                        'title': text[:200],
                        'url': href,
                        'snippet': ''
                    })
                    if len(results) >= 10:
                        break
    except:
        pass
    return results


async def search_person_async(full_name, progress_callback=None):
    result = {
        'name': full_name,
        'search_results': [],
        'social_results': []
    }
    
    parts = full_name.strip().split()
    if len(parts) < 2:
        return {'error': 'Please enter a full name (first and last name)', 'result': result}
    
    first_name = parts[0]
    last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''
    
    search_query = quote(f'"{first_name}" "{last_name}"')
    
    result['search_links'] = [
        {
            'engine': 'Google',
            'name': 'Search on Google',
            'url': f'https://www.google.com/search?q={search_query}',
            'query': f'"{first_name}" "{last_name}"'
        },
        {
            'engine': 'LinkedIn',
            'name': 'Search on LinkedIn',
            'url': f'https://www.linkedin.com/search/results/all/?keywords={quote(first_name + " " + last_name)}',
            'query': 'LinkedIn Profile'
        },
        {
            'engine': 'Facebook',
            'name': 'Search on Facebook',
            'url': f'https://www.facebook.com/search/top?q={quote(first_name + " " + last_name)}',
            'query': 'Facebook Profile'
        },
        {
            'engine': 'Twitter/X',
            'name': 'Search on Twitter/X',
            'url': f'https://nitter.net/search?f=users&q={quote(first_name + " " + last_name)}',
            'query': 'Twitter Profile'
        },
        {
            'engine': 'GitHub',
            'name': 'Search on GitHub',
            'url': f'https://github.com/search?q={quote(first_name + "+" + last_name)}&type=users',
            'query': 'GitHub Profile'
        },
        {
            'engine': 'Instagram',
            'name': 'Search on Instagram',
            'url': f'https://www.instagram.com/{quote(first_name + last_name)}/',
            'query': 'Instagram Profile'
        },
        {
            'engine': 'Reddit',
            'name': 'Search on Reddit',
            'url': f'https://www.reddit.com/search/?q={quote(first_name + " " + last_name)}',
            'query': 'Reddit Posts'
        },
        {
            'engine': 'YouTube',
            'name': 'Search on YouTube',
            'url': f'https://www.youtube.com/results?search_query={quote(first_name + " " + last_name)}',
            'query': 'YouTube Channel'
        },
        {
            'engine': 'TikTok',
            'name': 'Search on TikTok',
            'url': f'https://www.tiktok.com/@{quote(first_name + last_name)}',
            'query': 'TikTok Profile'
        },
        {
            'engine': 'Pipl',
            'name': 'Search on Pipl',
            'url': f'https://pipl.com/search/?q={search_query}',
            'query': 'Deep Web Search'
        },
        {
            'engine': 'Spytox',
            'name': 'Search on Spytox',
            'url': f'https://www.spytox.com/people/search?name={quote(first_name)}&location={quote(last_name)}',
            'query': 'People Directory'
        },
        {
            'engine': 'Truecaller',
            'name': 'Search on Truecaller',
            'url': f'https://www.truecaller.com/search/{quote(first_name + " " + last_name)}',
            'query': 'Phone Lookup'
        }
    ]
    
    result['total_results'] = len(result['search_links'])
    result['engines_checked'] = len(result['search_links'])
    
    return result


def search_person(full_name):
    return asyncio.run(search_person_async(full_name))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/email', methods=['POST'])
def email_lookup():
    data = request.get_json()
    email = data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    return jsonify(lookup_email(email))

@app.route('/api/ip', methods=['POST'])
def ip_lookup():
    data = request.get_json()
    ip = data.get('ip', '')
    if not ip:
        return jsonify({'error': 'IP address required'}), 400
    return jsonify(lookup_ip(ip))

@app.route('/api/domain', methods=['POST'])
def domain_lookup():
    data = request.get_json()
    domain = data.get('domain', '')
    if not domain:
        return jsonify({'error': 'Domain required'}), 400
    return jsonify(lookup_domain(domain))


@app.route('/api/hibp', methods=['POST'])
def hibp_check():
    data = request.get_json()
    email = data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    
    if not HIBP_API_KEY:
        return jsonify({'email': email, 'no_api_key': True, 'breaches': []})
    
    try:
        headers = {
            'User-Agent': 'OSINT-Dashboard',
            'hibp-api-key': HIBP_API_KEY
        }
        
        response = requests.get(
            f'https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email)}?truncateResponse=false',
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 200:
            breaches = response.json()
            return jsonify({'email': email, 'found': True, 'breaches': breaches})
        elif response.status_code == 404:
            return jsonify({'email': email, 'found': False, 'breaches': []})
        elif response.status_code == 401:
            return jsonify({'email': email, 'error': 'Invalid API key', 'no_api_key': True, 'breaches': []})
        elif response.status_code == 429:
            return jsonify({'email': email, 'error': 'Rate limited', 'breaches': []})
        else:
            return jsonify({'email': email, 'error': f'API error: {response.status_code}', 'breaches': []})
            
    except requests.Timeout:
        return jsonify({'email': email, 'error': 'Request timeout', 'breaches': []})
    except Exception as e:
        logger.error(f"HIBP check error: {e}", exc_info=True)
        return jsonify({'email': email, 'error': str(e), 'breaches': []})


@app.route('/api/username/stream', methods=['POST'])
def username_search_stream():
    from flask import Response, stream_with_context
    import threading
    import queue
    
    data = request.get_json()
    username = data.get('username', '')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        progress_state.update(progress)
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_username_async(username, progress_callback, max_sites=100))
            found_count = result.get('found_count', 0)
            search_history.add_entry('username', username, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            loop.close()
    
    sherlock_sites = get_sherlock_sites() or {}
    sherlock_max_sites = 100
    sherlock_total = min(len(sherlock_sites), sherlock_max_sites)
    
    if not sherlock_sites:
        return jsonify({'error': 'Could not load Sherlock site data'}), 400
    
    progress_state['total'] = sherlock_total
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    def generate():
        import time
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.1)
                total = progress_state['total']
                checked = progress_state['checked']
                found = progress_state['found']
                current_site = progress_state['current_site']
                
                yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/email/stream', methods=['POST'])
def email_search_stream():
    from flask import Response, stream_with_context
    import threading
    import queue
    
    data = request.get_json()
    email = data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        progress_state.update(progress)
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_email_async(email, progress_callback))
            found_count = result.get('found_count', 0)
            search_history.add_entry('email', email, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            loop.close()
    
    email_sites = get_sherlock_sites()
    
    if not email_sites:
        return jsonify({'error': 'Could not load site data'}), 400
    
    progress_state['total'] = len(email_sites)
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    def generate():
        import time
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.1)
                total = progress_state['total']
                checked = progress_state['checked']
                found = progress_state['found']
                current_site = progress_state['current_site']
                
                yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/email/holehe', methods=['POST'])
def email_holehe():
    from flask import Response, stream_with_context
    import threading
    import queue
    from argparse import Namespace
    
    data = request.get_json()
    email = data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        progress_state.update(progress)
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_email_holehe(email, progress_callback))
            search_history.add_entry('holehe', email, f'{result.get("found_count", 0)} accounts found', result.get('found_count', 0))
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            loop.close()
    
    from holehe.core import import_submodules, get_functions
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    progress_state['total'] = len(websites)
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    def generate():
        import time
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.1)
                total = progress_state['total']
                checked = progress_state['checked']
                found = progress_state['found']
                current_site = progress_state['current_site']
                
                yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/email/combined', methods=['POST'])
def email_combined():
    from flask import Response, stream_with_context
    import threading
    import queue
    
    data = request.get_json()
    email = data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        saved_total = progress_state['total']
        progress_state.update(progress)
        if progress.get('total', 0) < saved_total:
            progress_state['total'] = saved_total
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_email_combined(email, progress_callback))
            found_count = result.get('found_count', 0)
            search_history.add_entry('email', email, f'{found_count} accounts found (Sherlock + Holehe)', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            loop.close()
    
    from holehe.core import import_submodules, get_functions
    from argparse import Namespace
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    sherlock_sites = get_sherlock_sites() or {}
    holehe_total = len(websites)
    sherlock_total = len(sherlock_sites)
    progress_state['total'] = sherlock_total + holehe_total
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    def generate():
        import time
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.1)
                total = progress_state['total']
                checked = progress_state['checked']
                found = progress_state['found']
                current_site = progress_state['current_site']
                
                yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/email/crossvalidated', methods=['POST'])
def email_cross_validated():
    from flask import Response, stream_with_context
    import threading
    import queue
    
    data = request.get_json()
    email = data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        saved_total = progress_state['total']
        progress_state.update(progress)
        if progress.get('total', 0) < saved_total:
            progress_state['total'] = saved_total
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_email_combined(email, progress_callback))
            found_count = result.get('found_count', 0)
            cross_count = result.get('cross_validated_count', 0)
            search_history.add_entry('email', email, f'{found_count} found, {cross_count} cross-validated', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            loop.close()
    
    from holehe.core import import_submodules, get_functions
    from argparse import Namespace
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    sherlock_sites = get_sherlock_sites() or {}
    holehe_total = len(websites)
    sherlock_total = len(sherlock_sites)
    progress_state['total'] = sherlock_total + holehe_total
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    def generate():
        import time
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.1)
                total = progress_state['total']
                checked = progress_state['checked']
                found = progress_state['found']
                current_site = progress_state['current_site']
                
                yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/username', methods=['POST'])
def username_search():
    data = request.get_json()
    username = data.get('username', '')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    return jsonify(search_username(username))


@app.route('/api/maigret/platforms', methods=['GET'])
def get_maigret_platforms():
    from collections import Counter
    
    db = get_maigret_database()
    if not db:
        return jsonify({'error': 'Could not load Maigret database'}), 500
    
    tag = request.args.get('tag', '').lower()
    search = request.args.get('search', '').lower()
    limit = int(request.args.get('limit', 500))
    
    sites = []
    for site in db.sites:
        site_data = {
            'name': site.name,
            'pretty_name': getattr(site, 'pretty_name', site.name),
            'url_main': getattr(site, 'url_main', ''),
            'tags': list(getattr(site, 'tags', []) or []),
            'country': getattr(site, 'alexa_rank', None)
        }
        
        if tag and tag not in site_data['tags']:
            continue
        if search and search not in site.name.lower() and search not in (site_data.get('pretty_name') or '').lower():
            continue
        
        sites.append(site_data)
    
    tag_counts = Counter()
    for site in db.sites:
        tags = getattr(site, 'tags', []) or []
        for t in tags:
            tag_counts[t] += 1
    
    popular_tags = [{'tag': t, 'count': c} for t, c in tag_counts.most_common(30)]
    
    return jsonify({
        'total': len(sites),
        'tags': popular_tags,
        'sites': sites[:limit]
    })


@app.route('/api/username/maigret', methods=['POST'])
def username_search_maigret():
    from flask import Response, stream_with_context
    import threading
    import queue
    import logging
    
    data = request.get_json()
    username = data.get('username', '')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    
    selected_sites = data.get('sites', [])  # List of site names to search
    selected_tags = data.get('tags', [])     # List of tags to filter by
    max_sites = data.get('limit', 300)
    
    # Predefined site groups
    SOCIAL_MEDIA_SITES = [
        'Facebook', 'Instagram', 'Twitter', 'TikTok', 'YouTube', 'Reddit',
        'Pinterest', 'Tumblr', 'Medium', 'Twitch', 'Spotify', 'SoundCloud', 
        'GitHub', 'GitLab', 'VK', 'OK', 'Flickr', 'Vimeo', 'Dribbble', 
        'Behance', 'DeviantART', 'Etsy', 'Fiverr', 'Roblox', 'Steam', 
        'Patreon', 'Cash.app', 'Venmo', 'GoodReads', 'Myspace', 'Xing', 
        'Imgur', 'Plurk', 'AskFM', 'Badoo', 'Telegram', 'We Heart It', 
        'Pornhub', 'Strava', 'Untappd', 'Pinterest', 'Steam (Group)',
        'Steam (by id)', 'Steamid', 'Steamidfinder', 'Reddit Search (Pushshift)'
    ]
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        progress_state.update(progress)
    
    def run_search_thread():
        try:
            import maigret.maigret as maigret_module
            db = get_maigret_database()
            
            if not db:
                result_queue.put(('error', 'Could not load Maigret database'))
                return
            
            logger = logging.getLogger('maigret')
            logger.setLevel(logging.ERROR)
            
            # Build site dictionary based on selection
            if selected_sites:
                # Use only explicitly selected sites
                limited_sites = {name: db.sites_dict[name] for name in selected_sites if name in db.sites_dict}
            elif selected_tags is not None:
                if len(selected_tags) == 0:
                    # Empty tags list means ALL sites
                    limited_sites = dict(list(db.sites_dict.items())[:max_sites])
                elif 'social' in selected_tags:
                    # Special handling for social media preset
                    limited_sites = {name: db.sites_dict[name] for name in SOCIAL_MEDIA_SITES if name in db.sites_dict}
                else:
                    # Filter by tags
                    filtered = {}
                    for name, site in db.sites_dict.items():
                        site_tags = getattr(site, 'tags', []) or []
                        if any(tag in site_tags for tag in selected_tags):
                            filtered[name] = site
                    limited_sites = dict(list(filtered.items())[:max_sites])
            else:
                # Default: use top sites
                limited_sites = dict(list(db.sites_dict.items())[:max_sites])
            
            # Set progress total to the actual number of sites being searched
            progress_state['total'] = len(limited_sites)
            
            class ProgressNotifier:
                def __init__(self, cb, total):
                    self.callback = cb
                    self.checked = 0
                    self.total = total
                    self.found = 0
                    
                def start(self, message, id_type):
                    pass
                
                def update(self, result, is_similar=False):
                    self.checked += 1
                    status = getattr(result, 'status', None)
                    if status and hasattr(status, 'is_found'):
                        if status.is_found():
                            self.found += 1
                    
                    if self.callback:
                        self.callback({
                            'checked': self.checked,
                            'total': self.total,
                            'found': self.found,
                            'percent': int((self.checked / self.total) * 100) if self.total > 0 else 0,
                            'current_site': getattr(result, 'site_name', '') or ''
                        })
                
                def finish(self):
                    pass
                
                def info(self, msg):
                    pass
                
                def warning(self, msg):
                    pass
                
                def success(self, result):
                    pass
            
            notifier = ProgressNotifier(progress_callback, len(limited_sites))
            
            results = asyncio.run(maigret_module.maigret(
                username=username,
                site_dict=limited_sites,
                logger=logger,
                query_notify=notifier,
                timeout=2,
                is_parsing_enabled=False,
                max_connections=20,
                no_progressbar=True
            ))
            
            findings = []
            found_count = 0
            
            for site_name, site_result in results.items():
                exists = site_result.get('exists', False)
                if exists is None:
                    exists = False
                
                status = site_result.get('status', 'unknown')
                if hasattr(status, 'name'):
                    status = status.name
                
                url_user = site_result.get('url_user', '')
                url_main = site_result.get('url_main', '')
                
                finding = {
                    'site': site_name,
                    'url': url_user or url_main or '',
                    'exists': bool(exists),
                    'status': str(status),
                    'http_status': site_result.get('http_status')
                }
                if exists:
                    found_count += 1
                findings.append(finding)
            
            result = {
                'username': username,
                'platforms_checked': len(findings),
                'findings': findings,
                'found_count': found_count,
                'method': 'maigret',
                'total_sites_available': len(db.sites)
            }
            
            search_history.add_entry('maigret', username, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            result_queue.put(('error', str(e)))
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    def generate():
        import time
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.2)
                total = progress_state['total']
                checked = progress_state['checked']
                found = progress_state['found']
                current_site = progress_state['current_site']
                
                yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/name-search', methods=['POST'])
def name_search():
    data = request.get_json()
    full_name = data.get('name', '').strip()
    if not full_name:
        return jsonify({'error': 'Name required'}), 400
    
    parts = full_name.split()
    if len(parts) < 2:
        return jsonify({'error': 'Please enter first and last name'}), 400
    
    first_name = parts[0]
    last_name = ' '.join(parts[1:])
    
    results = {
        'name': full_name,
        'first_name': first_name,
        'last_name': last_name,
        'found_count': 0,
        'accounts': []
    }
    
    async def search_platforms():
        import httpx
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers=HEADERS
        )
        
        async def check_linkedin():
            try:
                search_url = f"https://www.linkedin.com/search/results/people/?keywords={quote(first_name)}%20{quote(last_name)}"
                response = await client.get(search_url, timeout=10, follow_redirects=True)
                if response.status_code == 200:
                    if 'login' in str(response.url).lower() or 'uas/login' in str(response.url).lower():
                        return {'site': 'LinkedIn', 'exists': None, 'url': search_url, 'status': 'login_required', 'note': 'Requires authentication'}
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'LinkedIn', 'exists': True, 'url': search_url, 'status': 'found'}
                return {'site': 'LinkedIn', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"LinkedIn search error: {e}")
                return {'site': 'LinkedIn', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_facebook():
            try:
                search_url = f"https://www.facebook.com/search/people/?q={quote(first_name)}%20{quote(last_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    profile_count = text.count('profile')
                    if profile_count > 5:
                        return {'site': 'Facebook', 'exists': True, 'url': search_url, 'status': 'found', 'note': f'{profile_count} profiles found'}
                return {'site': 'Facebook', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Facebook search error: {e}")
                return {'site': 'Facebook', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_twitter():
            try:
                search_url = f"https://twitter.com/search?q={quote(first_name)}%20{quote(last_name)}&src=sp"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'Twitter/X', 'exists': True, 'url': search_url, 'status': 'found'}
                return {'site': 'Twitter/X', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Twitter search error: {e}")
                return {'site': 'Twitter/X', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_instagram():
            try:
                search_url = f"https://www.instagram.com/web/search/topsearch/?search_term={quote(first_name)}%20{quote(last_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    if 'login' in str(response.url).lower() or 'accounts/login' in response.text.lower():
                        return {'site': 'Instagram', 'exists': None, 'url': 'https://instagram.com/', 'status': 'login_required', 'note': 'Requires authentication'}
                    data = response.json()
                    users = data.get('users', [])
                    if users:
                        found_users = []
                        for u in users[:5]:
                            user = u.get('user', {})
                            found_users.append({
                                'username': user.get('username'),
                                'full_name': user.get('full_name'),
                                'url': f"https://instagram.com/{user.get('username')}"
                            })
                        return {'site': 'Instagram', 'exists': True, 'url': 'https://instagram.com/', 'status': 'found', 'users': found_users}
                if response.status_code in [302, 307, 308]:
                    return {'site': 'Instagram', 'exists': None, 'url': 'https://instagram.com/', 'status': 'redirect', 'note': 'Login required'}
                return {'site': 'Instagram', 'exists': False, 'url': 'https://instagram.com/', 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Instagram search error: {e}")
                return {'site': 'Instagram', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_youtube():
            try:
                search_url = f"https://www.youtube.com/results?search_query={quote(first_name)}%20{quote(last_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'YouTube', 'exists': True, 'url': search_url, 'status': 'found'}
                return {'site': 'YouTube', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"YouTube search error: {e}")
                return {'site': 'YouTube', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_tiktok():
            try:
                search_url = f"https://www.tiktok.com/api/search/general/full/?keyword={quote(first_name)}%20{quote(last_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if data.get('data'):
                            return {'site': 'TikTok', 'exists': True, 'url': 'https://www.tiktok.com/', 'status': 'found', 'note': 'Found results'}
                    except:
                        pass
                return {'site': 'TikTok', 'exists': False, 'url': 'https://www.tiktok.com/', 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"TikTok search error: {e}")
                return {'site': 'TikTok', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_github():
            try:
                gh_response = await client.get(
                    f'https://api.github.com/search/users?q={quote(first_name)}+{quote(last_name)}+in:name',
                    headers={'Accept': 'application/vnd.github.v3+json'},
                    timeout=10
                )
                if gh_response.status_code == 200:
                    gh_data = gh_response.json()
                    users = gh_data.get('items', [])
                    if users:
                        found = []
                        for user in users[:5]:
                            found.append({
                                'username': user.get('login'),
                                'url': user.get('html_url'),
                                'avatar': user.get('avatar_url')
                            })
                        return {'site': 'GitHub', 'exists': True, 'url': 'https://github.com/', 'status': 'found', 'users': found}
                return {'site': 'GitHub', 'exists': False, 'url': 'https://github.com/', 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"GitHub search error: {e}")
                return {'site': 'GitHub', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_snapchat():
            try:
                search_url = f"https://map.snapchat.com/search?query={quote(full_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    return {'site': 'Snapchat', 'exists': None, 'url': search_url, 'status': 'unavailable', 'note': 'Map search only'}
                return {'site': 'Snapchat', 'exists': False, 'url': 'https://snapchat.com/', 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Snapchat search error: {e}")
                return {'site': 'Snapchat', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_reddit():
            try:
                search_url = f"https://www.reddit.com/search/?q={quote(full_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'Reddit', 'exists': True, 'url': search_url, 'status': 'found'}
                return {'site': 'Reddit', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Reddit search error: {e}")
                return {'site': 'Reddit', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_pinterest():
            try:
                search_url = f"https://www.pinterest.com/search/users/?q={quote(full_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'Pinterest', 'exists': True, 'url': search_url, 'status': 'found'}
                    if 'login' in str(response.url).lower():
                        return {'site': 'Pinterest', 'exists': None, 'url': search_url, 'status': 'login_required', 'note': 'Requires authentication'}
                return {'site': 'Pinterest', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Pinterest search error: {e}")
                return {'site': 'Pinterest', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_twitch():
            try:
                search_url = f"https://www.twitch.tv/search?term={quote(full_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'Twitch', 'exists': True, 'url': search_url, 'status': 'found'}
                    if 'login' in str(response.url).lower():
                        return {'site': 'Twitch', 'exists': None, 'url': search_url, 'status': 'login_required', 'note': 'Requires authentication'}
                return {'site': 'Twitch', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Twitch search error: {e}")
                return {'site': 'Twitch', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_medium():
            try:
                search_url = f"https://medium.com/search?q={quote(full_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'Medium', 'exists': True, 'url': search_url, 'status': 'found'}
                return {'site': 'Medium', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Medium search error: {e}")
                return {'site': 'Medium', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_discord():
            try:
                search_url = f"https://discord.com/channels/@me"
                response = await client.get(search_url, timeout=10)
                if 'login' in str(response.url).lower() or response.status_code == 401:
                    return {'site': 'Discord', 'exists': None, 'url': 'https://discord.com/', 'status': 'login_required', 'note': 'Requires authentication to search'}
                return {'site': 'Discord', 'exists': False, 'url': 'https://discord.com/', 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Discord search error: {e}")
                return {'site': 'Discord', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_lastfm():
            try:
                search_url = f"https://www.last.fm/search?q={quote(full_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'Last.fm', 'exists': True, 'url': search_url, 'status': 'found'}
                return {'site': 'Last.fm', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Last.fm search error: {e}")
                return {'site': 'Last.fm', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_threads():
            try:
                search_url = f"https://www.threads.net/search?q={quote(full_name)}"
                response = await client.get(search_url, timeout=10)
                if response.status_code == 200:
                    text = response.text.lower()
                    if first_name.lower() in text or last_name.lower() in text:
                        return {'site': 'Threads', 'exists': True, 'url': search_url, 'status': 'found'}
                    if 'login' in str(response.url).lower():
                        return {'site': 'Threads', 'exists': None, 'url': search_url, 'status': 'login_required', 'note': 'Requires authentication'}
                return {'site': 'Threads', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Threads search error: {e}")
                return {'site': 'Threads', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_hinge():
            try:
                search_url = "https://hinge.co"
                response = await client.get(search_url, timeout=10)
                if 'login' in str(response.url).lower():
                    return {'site': 'Hinge', 'exists': None, 'url': search_url, 'status': 'login_required', 'note': 'Requires authentication'}
                return {'site': 'Hinge', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Hinge search error: {e}")
                return {'site': 'Hinge', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_bumble():
            try:
                search_url = "https://bumble.com"
                response = await client.get(search_url, timeout=10)
                if 'login' in str(response.url).lower() or 'auth' in str(response.url).lower():
                    return {'site': 'Bumble', 'exists': None, 'url': search_url, 'status': 'login_required', 'note': 'Requires authentication'}
                return {'site': 'Bumble', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Bumble search error: {e}")
                return {'site': 'Bumble', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_grindr():
            try:
                search_url = "https://grindr.com"
                response = await client.get(search_url, timeout=10)
                if 'login' in str(response.url).lower():
                    return {'site': 'Grindr', 'exists': None, 'url': search_url, 'status': 'login_required', 'note': 'Requires authentication'}
                return {'site': 'Grindr', 'exists': False, 'url': search_url, 'status': 'not_found'}
            except Exception as e:
                logger.debug(f"Grindr search error: {e}")
                return {'site': 'Grindr', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        async def check_whatsapp():
            try:
                search_url = f"https://api.whatsapp.com/v1/contacts"
                response = await client.get(search_url, timeout=10)
                return {'site': 'WhatsApp', 'exists': None, 'url': 'https://whatsapp.com/', 'status': 'unavailable', 'note': 'API not available for search'}
            except Exception as e:
                logger.debug(f"WhatsApp search error: {e}")
                return {'site': 'WhatsApp', 'exists': None, 'url': '', 'status': 'error', 'error': str(e)}
        
        tasks = [
            check_linkedin(),
            check_facebook(),
            check_twitter(),
            check_instagram(),
            check_youtube(),
            check_tiktok(),
            check_github(),
            check_snapchat(),
            check_reddit(),
            check_pinterest(),
            check_twitch(),
            check_medium(),
            check_discord(),
            check_lastfm(),
            check_threads(),
            check_hinge(),
            check_bumble(),
            check_grindr(),
            check_whatsapp()
        ]
        
        import asyncio
        results_list = await asyncio.gather(*tasks)
        
        await client.aclose()
        return results_list
    
    account_results = asyncio.run(search_platforms())
    
    for result in account_results:
        if result.get('exists') == True:
            results['found_count'] += 1
            results['accounts'].append(result)
        elif result.get('exists') is None:
            results['accounts'].append(result)
    
    username_patterns = [
        f"{first_name}{last_name}".lower(),
        f"{first_name}.{last_name}".lower(),
        f"{first_name}_{last_name}".lower(),
        f"{first_name[0]}{last_name}".lower(),
        f"{first_name}{last_name[0]}".lower(),
    ]
    results['username_patterns'] = username_patterns
    
    logger.info(f"Name search for '{full_name}': {results['found_count']} platforms with results")
    
    return jsonify(results)


@app.route('/api/person/dorks', methods=['POST'])
def person_dorks_search():
    """Search using Google dorks to find person info across web"""
    data = request.get_json()
    full_name = data.get('name', '').strip()
    if not full_name:
        return jsonify({'error': 'Name required'}), 400
    
    parts = full_name.split()
    if len(parts) < 2:
        return jsonify({'error': 'Please enter first and last name'}), 400
    
    first_name = parts[0]
    last_name = ' '.join(parts[1:])
    
    dork_queries = [
        # Social media
        f'"{full_name}" site:linkedin.com',
        f'"{full_name}" site:facebook.com',
        f'"{full_name}" site:twitter.com OR site:x.com',
        f'"{full_name}" site:instagram.com',
        f'"{full_name}" site:tiktok.com',
        f'"{full_name}" site:youtube.com',
        # Files with name
        f'"{full_name}" filetype:pdf',
        f'"{full_name}" filetype:doc OR filetype:docx',
        f'"{full_name}" filetype:xls OR filetype:xlsx',
        # Public records
        f'"{full_name}" "whitepages"',
        f'"{full_name}" "public records"',
        # Location based
        f'"{full_name}" "{parts[-1]}" phone',
        f'"{full_name}" email',
        # News
        f'"{full_name}" site:news.google.com',
        f'"{full_name}" site:medium.com',
    ]
    
    results = {
        'name': full_name,
        'first_name': first_name,
        'last_name': last_name,
        'total_results': 0,
        'categories': {
            'social_media': [],
            'files': [],
            'public_records': [],
            'news': [],
            'general': []
        }
    }
    
    import httpx
    try:
        client = httpx.Client(timeout=30.0, follow_redirects=True, headers=HEADERS)
        
        google_search_url = "https://www.google.com/search"
        
        for query in dork_queries[:5]:  # Limit to first 5 to avoid rate limiting
            try:
                params = {'q': query, 'num': 10}
                response = client.get(google_search_url, params=params)
                
                if response.status_code == 200:
                    import re
                    links = re.findall(r'https?://[^\s<>"]+', response.text)
                    seen = set()
                    for link in links[:5]:
                        domain = re.sub(r'https?://(www\.)?', '', link).split('/')[0]
                        if domain and domain not in seen and 'google' not in domain:
                            seen.add(domain)
                            category = 'general'
                            if any(s in domain for s in ['linkedin', 'facebook', 'twitter', 'instagram', 'tiktok', 'youtube']):
                                category = 'social_media'
                            elif any(s in domain for s in ['pdf', 'doc', 'xls']):
                                category = 'files'
                            elif any(s in domain for s in ['news', 'medium']):
                                category = 'news'
                            
                            results['categories'][category].append({
                                'domain': domain,
                                'url': link[:200]
                            })
                            results['total_results'] += 1
                            
            except Exception as e:
                logger.debug(f"Dork query error: {e}")
                continue
        
        client.close()
        
    except Exception as e:
        logger.error(f"Dorks search error: {e}")
        return jsonify({'error': str(e)}), 500
    
    return jsonify(results)


@app.route('/api/person/stream', methods=['POST'])
def person_search_stream():
    from flask import Response, stream_with_context
    import threading
    import queue
    
    data = request.get_json()
    full_name = data.get('name', '')
    if not full_name:
        return jsonify({'error': 'Full name required'}), 400
    
    result_queue = queue.Queue()
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_person_async(full_name))
            found_count = result.get('total_results', 0)
            search_history.add_entry('person', full_name, f'{found_count} search links', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            loop.close()
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    total_tasks = 36
    
    def generate():
        import time
        completed = 0
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.3)
                
                completed = min(completed + 1, total_tasks)
                
                yield f"data: {json.dumps({'progress': {'completed': completed, 'total': total_tasks, 'percent': int((completed / total_tasks) * 100)}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/person', methods=['POST'])
def person_search():
    data = request.get_json()
    full_name = data.get('name', '')
    if not full_name:
        return jsonify({'error': 'Full name required'}), 400
    return jsonify(search_person(full_name))


SOCIAL_MEDIA_PLATFORMS = {
    'Facebook': {
        'url': 'https://www.facebook.com/{}',
        'category': 'social',
        'presence': ['fb://profile', 'entity_id', 'profile_id'],
        'url_absence': ['/login/']
    },
    'Instagram': {
        'url': 'https://www.instagram.com/{}',
        'category': 'social',
        'presence': ['<div id="splash-screen">', '"profile_id"'],
        'url_absence': ['/accounts/login/', '/login/']
    },
    'Twitter/X': {
        'url': 'https://twitter.com/{}',
        'category': 'social',
        'presence': [],
        'absence': []
    },
    'TikTok': {
        'url': 'https://www.tiktok.com/@{}',
        'category': 'social',
        'presence': ['followerCount', 'followingCount'],
        'absence': ['Could not find this account', 'account does not exist']
    },
    'YouTube': {
        'url': 'https://www.youtube.com/@{}',
        'category': 'social',
        'presence': [],
        'absence': []
    },
    'LinkedIn': {
        'url': 'https://www.linkedin.com/in/{}',
        'category': 'social',
        'presence': [],
        'absence': []
    },
    'Snapchat': {
        'url': 'https://www.snapchat.com/add/{}',
        'category': 'social',
        'presence': [],
        'absence': []
    },
    'Reddit': {
        'url': 'https://www.reddit.com/user/{}',
        'category': 'social',
        'presence': [],
        'absence': []
    },
    'Pinterest': {
        'url': 'https://www.pinterest.com/{}',
        'category': 'social',
        'presence': ['"username"', 'data-grid-item'],
        'url_absence': ['/login/', '?loginError=']
    },
    'Tumblr': {
        'url': 'https://{}.tumblr.com',
        'category': 'social',
        'presence': [],
        'absence': []
    },
    'Twitch': {
        'url': 'https://www.twitch.tv/{}',
        'category': 'gaming',
        'presence': [],
        'absence': []
    },
    'Discord': {
        'url': 'https://discord.com/users/{}',
        'category': 'messaging',
        'presence': [],
        'absence': []
    },
    'Steam': {
        'url': 'https://steamcommunity.com/id/{}',
        'category': 'gaming',
        'presence': ['profile_page', 'actual_persona_name'],
        'absence': ['The specified profile could not be found']
    },
    'Spotify': {
        'url': 'https://open.spotify.com/user/{}',
        'category': 'creative',
        'presence': [],
        'absence': []
    },
    'SoundCloud': {
        'url': 'https://soundcloud.com/{}',
        'category': 'creative',
        'presence': ['profile', 'soundcloud'],
        'absence': ['Couldn\'t find that page']
    },
    'GitHub': {
        'url': 'https://github.com/{}',
        'category': 'developer',
        'presence': ['p-nickname', 'calendar-graph'],
        'absence': ['This is not the page you\'re looking for']
    },
    'GitLab': {
        'url': 'https://gitlab.com/{}',
        'category': 'developer',
        'presence': ['profile-header', 'user-info'],
        'absence': ['This user could not be found']
    },
    'Bitbucket': {
        'url': 'https://bitbucket.org/{}',
        'category': 'developer',
        'presence': ['profile', 'avatar'],
        'absence': ['This page doesn\'t exist']
    },
    'Medium': {
        'url': 'https://medium.com/@{}',
        'category': 'social',
        'presence': ['profile', 'author'],
        'absence': ['404 — Page not found']
    },
    'Quora': {
        'url': 'https://www.quora.com/profile/{}',
        'category': 'social',
        'presence': ['user', 'profile'],
        'absence': ['There is no profile']
    },
    'Vimeo': {
        'url': 'https://vimeo.com/{}',
        'category': 'creative',
        'presence': ['creator', 'profile'],
        'absence': ['could not be found']
    },
    'Flickr': {
        'url': 'https://www.flickr.com/people/{}',
        'category': 'creative',
        'presence': ['photostream', 'profile'],
        'absence': ['not found on Flickr']
    },
    'Behance': {
        'url': 'https://www.behance.net/{}',
        'category': 'creative',
        'presence': ['profile-info', 'owner'],
        'absence': ['couldn\'t be found']
    },
    'Dribbble': {
        'url': 'https://dribbble.com/{}',
        'category': 'creative',
        'presence': ['profile', 'shot'],
        'absence': ['Page not found']
    },
    'Keybase': {
        'url': 'https://keybase.io/{}',
        'category': 'developer',
        'presence': ['keybase', 'proofs'],
        'absence': ['No one by that name']
    },
    'Mastodon': {
        'url': 'https://mastodon.social/@{}',
        'category': 'messaging',
        'presence': ['mstdn', 'toot'],
        'absence': ['The requested account could not be found']
    },
    'Threads': {
        'url': 'https://www.threads.net/@{}',
        'category': 'social',
        'presence': ['threads', 'profile'],
        'absence': ['couldn\'t find']
    },
    'VK': {
        'url': 'https://vk.com/{}',
        'category': 'social',
        'presence': ['profile', 'op_header'],
        'absence': ['is not found']
    },
    'Telegram': {
        'url': 'https://t.me/{}',
        'category': 'messaging',
        'presence': ['og:title'],
        'absence': ['Contact @']
    },
    'CashApp': {
        'url': 'https://cash.app/${}',
        'category': 'other',
        'presence': ['cashtag', 'profile'],
        'absence': ['doesn\'t exist']
    },
    'Venmo': {
        'url': 'https://venmo.com/{}',
        'category': 'other',
        'presence': ['profile', 'user'],
        'absence': ['We couldn\'t find']
    },
    'DeviantArt': {
        'url': 'https://www.deviantart.com/{}',
        'category': 'creative',
        'presence': ['deviation', 'user-profile'],
        'absence': ['couldn\'t find this user']
    },
    'Imgur': {
        'url': 'https://imgur.com/user/{}',
        'category': 'creative',
        'presence': ['avatar', 'user-info'],
        'absence': ['404 - Not Found']
    },
    'LeetCode': {
        'url': 'https://leetcode.com/{}',
        'category': 'developer',
        'presence': ['profile', 'user-profile'],
        'absence': ['User does not exist']
    },
    'Replit': {
        'url': 'https://replit.com/@{}',
        'category': 'developer',
        'presence': ['profile', 'replit'],
        'absence': ['couldn\'t find']
    },
    'CodePen': {
        'url': 'https://codepen.io/{}',
        'category': 'developer',
        'presence': ['profile', 'codepen'],
        'absence': ['404 - Page Not Found']
    },
    'StackOverflow': {
        'url': 'https://stackoverflow.com/users/-1/{}',
        'category': 'developer',
        'presence': ['profile', 'user-card'],
        'absence': ['Page not found']
    },
    'Goodreads': {
        'url': 'https://www.goodreads.com/{}',
        'category': 'social',
        'presence': ['user', 'profile'],
        'absence': ['Not Found']
    },
    'MyAnimeList': {
        'url': 'https://myanimelist.net/profile/{}',
        'category': 'social',
        'presence': ['profile', 'user-info'],
        'absence': ['does not have a profile']
    },
    'Last.fm': {
        'url': 'https://www.last.fm/user/{}',
        'category': 'creative',
        'presence': ['library', 'recent-tracks'],
        'absence': ['We couldn\'t find']
    },
    'Letterboxd': {
        'url': 'https://letterboxd.com/{}',
        'category': 'social',
        'presence': ['film-grid', 'member'],
        'absence': ['Page not found']
    },
    'Patreon': {
        'url': 'https://www.patreon.com/{}',
        'category': 'other',
        'presence': ['patreon', 'campaign'],
        'absence': ['This page doesn\'t exist']
    },
    'Kaggle': {
        'url': 'https://www.kaggle.com/{}',
        'category': 'developer',
        'presence': ['profile', 'user-info'],
        'absence': ['Could not find user']
    },
    'ArtStation': {
        'url': 'https://www.artstation.com/{}',
        'category': 'creative',
        'presence': ['portfolio', 'artist'],
        'absence': ['Page not found']
    },
    'Strava': {
        'url': 'https://www.strava.com/athletes/{}',
        'category': 'other',
        'presence': ['athlete', 'activity'],
        'absence': ['The athlete you were looking for does not exist']
    },
    'VSCO': {
        'url': 'https://vsco.co/{}',
        'category': 'creative',
        'presence': ['journal', 'images'],
        'absence': ['couldn\'t find']
    },
    'PSN': {
        'url': 'https://psnprofiles.com/{}',
        'category': 'gaming',
        'presence': ['profile', 'trophies'],
        'absence': ['doesn\'t exist']
    },
    'Roblox': {
        'url': 'https://www.roblox.com/users/{}',
        'category': 'gaming',
        'presence': ['profile', 'avatar'],
        'absence': ['Page not found']
    },
    'Fiverr': {
        'url': 'https://www.fiverr.com/{}',
        'category': 'other',
        'presence': ['seller', 'profile'],
        'absence': ['Page not found']
    },
    '500px': {
        'url': 'https://500px.com/{}',
        'category': 'creative',
        'presence': ['user', 'photo'],
        'absence': ['doesn\'t exist']
    },
    'Linktree': {
        'url': 'https://linktr.ee/{}',
        'category': 'linkinbio',
        'presence': ['linktree', 'profile'],
        'absence': ['couldn\'t find']
    },
    'Carrd': {
        'url': 'https://{}.carrd.co',
        'category': 'linkinbio',
        'presence': ['carrd', 'profile'],
        'absence': []
    },
    'Wix': {
        'url': 'https://{}.wixsite.com',
        'category': 'other',
        'presence': ['wix', 'site'],
        'absence': []
    },
    'WordPress': {
        'url': 'https://{}.wordpress.com',
        'category': 'other',
        'presence': ['wordpress', 'post'],
        'absence': ['doesn\'t exist']
    },
    'Blogger': {
        'url': 'https://{}.blogspot.com',
        'category': 'other',
        'presence': ['blog', 'post'],
        'absence': ['Blog not found']
    },
    'WhatsApp': {
        'url': 'https://wa.me/{}',
        'category': 'messaging',
        'presence': ['start chatting', 'chat with'],
        'absence': ['phone number is not on whatsapp', 'is unavailable', 'cannot send']
    },
}

SITE_CATEGORIES = {
    'all': {'name': 'All Sites', 'filter': None},
    'social': {'name': 'Social Media', 'filter': lambda p: p.get('category') == 'social'},
    'developer': {'name': 'Developer', 'filter': lambda p: p.get('category') == 'developer'},
    'gaming': {'name': 'Gaming', 'filter': lambda p: p.get('category') == 'gaming'},
    'creative': {'name': 'Creative', 'filter': lambda p: p.get('category') == 'creative'},
    'messaging': {'name': 'Messaging', 'filter': lambda p: p.get('category') == 'messaging'},
    'linkinbio': {'name': 'Link-in-Bio', 'filter': lambda p: p.get('category') == 'linkinbio'},
    'other': {'name': 'Other', 'filter': lambda p: p.get('category') == 'other'},
}

def get_platforms_by_category(category='all'):
    if category == 'all':
        return SOCIAL_MEDIA_PLATFORMS
    filter_func = SITE_CATEGORIES.get(category, SITE_CATEGORIES['all'])['filter']
    if filter_func is None:
        return SOCIAL_MEDIA_PLATFORMS
    return {k: v for k, v in SOCIAL_MEDIA_PLATFORMS.items() if filter_func(v)}


async def check_social_platform(client, platform_name, platform_info, query, timeout=5.0):
    finding = {
        'platform': platform_name,
        'url': '',
        'exists': None,
        'status': 'checking'
    }
    
    clean_query = query.replace('+', '').replace(' ', '').lower()
    url = platform_info['url'].format(clean_query)
    finding['url'] = url
    
    presence_strs = platform_info.get('presence', [])
    absence_strs = platform_info.get('absence', [])
    url_absence_strs = platform_info.get('url_absence', [])
    
    try:
        response = await client.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
        finding['http_status'] = response.status_code
        
        response_text = response.text.lower()
        final_url = str(response.url).lower()
        
        has_presence = any(ps.lower() in response_text for ps in presence_strs) if presence_strs else False
        has_absence = any(as_.lower() in response_text for as_ in absence_strs) if absence_strs else False
        has_url_absence = any(ua.lower() in final_url for ua in url_absence_strs) if url_absence_strs else False
        
        if has_absence or has_url_absence:
            finding['exists'] = False
            finding['status'] = 'not_found'
        elif has_presence:
            finding['exists'] = True
            finding['status'] = 'found'
        elif response.status_code == 404:
            finding['exists'] = False
            finding['status'] = 'not_found'
        else:
            finding['exists'] = None
            finding['status'] = 'unknown'
            
    except httpx.TimeoutException:
        finding['status'] = 'timeout'
    except httpx.ConnectError:
        finding['status'] = 'connection_error'
    except Exception:
        finding['status'] = 'error'
    
    return finding


def sort_platforms_by_priority(platforms):
    platform_items = list(platforms.items())
    platform_items.sort(key=lambda x: PLATFORM_PRIORITY.get(x[0], 999))
    return platform_items


async def search_social_async(query, search_type='username', progress_callback=None, platforms=None, use_cache=True):
    if platforms is None:
        platforms = SOCIAL_MEDIA_PLATFORMS
    
    cached = get_cached_result('social', query) if use_cache else None
    if cached:
        return cached
    
    result = {
        'query': query,
        'search_type': search_type,
        'platforms': [],
        'found': [],
        'not_found': [],
        'from_cache': False
    }
    
    all_results = []
    total_platforms = len(platforms)
    checked = 0
    
    batch_size = 25
    
    client = get_http_client()
    cookies = {
        'wd': '1920x1080',
        'CONSENT': 'YES+cb.20210328-17-p0.en+FX+921'
    }
    
    platform_items = sort_platforms_by_priority(platforms)
    
    for i in range(0, total_platforms, batch_size):
        batch = platform_items[i:i + batch_size]
        tasks = []
        for platform_name, platform_info in batch:
            timeout = 3.0 if PLATFORM_PRIORITY.get(platform_name, 999) <= 5 else 5.0
            tasks.append(check_social_platform(client, platform_name, platform_info, query, timeout))
        
        for platform_name, platform_info, task in zip([p[0] for p in batch], [p[1] for p in batch], tasks):
            try:
                r = await asyncio.wait_for(task, timeout=10)
                all_results.append(r)
                if r.get('exists') == True:
                    result['found'].append(r)
            except (asyncio.TimeoutError, Exception):
                all_results.append({
                    'platform': platform_name,
                    'exists': False,
                    'status': 'error'
                })
            checked += 1
            if progress_callback:
                progress_callback({
                    'checked': checked,
                    'total': total_platforms,
                    'found': len(result['found']),
                    'percent': int((checked / total_platforms) * 100),
                    'current_site': platform_name
                })
    
    result['platforms'] = all_results
    result['total_checked'] = total_platforms
    result['found_count'] = len(result['found'])
    result['not_found_count'] = sum(1 for p in all_results if p.get('exists') == False)
    
    if use_cache:
        set_cached_result('social', query, result)
    
    return result


def search_social(query, search_type='username'):
    return asyncio.run(search_social_async(query, search_type))


@app.route('/api/social/stream', methods=['POST'])
def social_search_stream():
    from flask import Response, stream_with_context
    import threading
    import queue
    
    data = request.get_json()
    query = data.get('query', '')
    search_type = data.get('type', 'username')
    category = data.get('category', 'all')
    use_cache = data.get('use_cache', True)
    
    if not query:
        return jsonify({'error': 'Query required'}), 400
    
    cleanup_stale_searches(max_age_seconds=60)
    
    search_key, existing_key = deduplicate_request(search_type, query, category)
    if existing_key:
        return jsonify({'error': 'Search already in progress', 'query': query}), 409
    
    cached = get_cached_result('social', query, category) if use_cache else None
    if cached:
        cached['from_cache'] = True
        search_history.add_entry('social', query, f'{cached.get("found_count", 0)} accounts found (cached)', cached.get('found_count', 0))
        mark_search_complete(search_key)
        return jsonify(cached)
    
    platforms = get_platforms_by_category(category)
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        progress_state.update(progress)
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_social_async(query, search_type, progress_callback, platforms, use_cache))
            found_count = result.get('found_count', 0)
            search_history.add_entry('social', query, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            mark_search_complete(search_key)
            loop.close()
    
    total_platforms = len(platforms)
    progress_state['total'] = total_platforms
    
    thread = threading.Thread(target=run_search_thread)
    thread.start()
    
    def generate():
        import time
        
        while True:
            try:
                status, data = result_queue.get_nowait()
                if status == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': data})}\n\n"
                break
            except queue.Empty:
                time.sleep(0.1)
                total = progress_state['total']
                checked = progress_state['checked']
                found = progress_state['found']
                current_site = progress_state['current_site']
                
                yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/social', methods=['POST'])
def social_search():
    data = request.get_json()
    query = data.get('query', '')
    search_type = data.get('type', 'username')
    if not query:
        return jsonify({'error': 'Query required'}), 400
    return jsonify(search_social(query, search_type))


@app.route('/api/system/restart', methods=['POST'])
def system_restart():
    import subprocess
    import threading
    
    def restart_in_background():
        time.sleep(1)
        subprocess.Popen(['bash', '-c', 'cd "$(dirname "$0")" && ./start.sh &'])
    
    threading.Thread(target=restart_in_background, daemon=True).start()
    return jsonify({'status': 'restarting', 'message': 'Application is restarting...'})

@app.route('/api/system/exit', methods=['POST'])
def system_exit():
    import threading
    
    def exit_app():
        time.sleep(0.5)
        import os
        os._exit(0)
    
    threading.Thread(target=exit_app, daemon=True).start()
    return jsonify({'status': 'exiting', 'message': 'Application is shutting down...'})


platform_health_cache = {'data': None, 'timestamp': None}
HEALTH_CHECK_INTERVAL = 300

@app.route('/api/platform-health', methods=['GET'])
def get_platform_health():
    now = datetime.now()
    
    if platform_health_cache['data'] and platform_health_cache['timestamp']:
        age = (now - platform_health_cache['timestamp']).total_seconds()
        if age < HEALTH_CHECK_INTERVAL:
            return jsonify(platform_health_cache['data'])
    
    def check_health():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        client = get_http_client()
        
        health_results = []
        
        test_usernames = {
            'Facebook': 'zuck',
            'GitHub': 'torvalds',
            'Telegram': 'durov',
            'TikTok': 'tiktok',
            'Pinterest': 'youtube',
            'Steam': 'glowe',
            'Instagram': 'instagram',
            'Twitter/X': 'elonmusk',
            'YouTube': 'youtube',
            'LinkedIn': 'satyanadella',
            'Reddit': 'spez',
            'Snapchat': 'snapchat',
            'Discord': 'discord',
            'Twitch': 'twitch',
            'Spotify': 'spotify',
            'Tumblr': 'nytimes',
            'Medium': 'medium',
            'Quora': 'quora',
            'Threads': 'threads',
            'VK': 'vk',
            'GitLab': 'gitlab',
            'Bitbucket': 'bitbucket',
            'Keybase': 'keybase',
            'LeetCode': 'leetcode',
            'Replit': 'replit',
            'CodePen': 'codepen',
            'StackOverflow': 'stackoverflow',
            'SoundCloud': 'soundcloud',
            'Vimeo': 'vimeo',
            'Flickr': 'flickr',
            'Behance': 'behance',
            'Dribbble': 'dribbble',
            'DeviantArt': 'deviantart',
            'Imgur': 'imgur',
            'Last.fm': 'last.fm',
            'Goodreads': 'goodreads',
            'MyAnimeList': 'myanimelist',
            'Letterboxd': 'letterboxd',
            'CashApp': 'cashapp',
            'Venmo': 'venmo',
            'Patreon': 'patreon',
            'Strava': 'strava',
            'Fiverr': 'fiverr',
            'ArtStation': 'artstation',
            'VSCO': 'vsco',
            'PSN': 'psn',
            'Roblox': 'roblox',
            'Mastodon': 'mastodon',
            '500px': '500px',
            'Linktree': 'linktree',
            'Carrd': 'carrd',
            'Wix': 'wix',
            'WordPress': 'wordpress',
            'Blogger': 'blogger',
            'WhatsApp': 'wa.me',
        }
        
        for platform_name, platform_info in SOCIAL_MEDIA_PLATFORMS.items():
            test_user = test_usernames.get(platform_name, 'test')
            clean_test = test_user.replace('+', '').replace(' ', '').lower()
            url = platform_info['url'].format(clean_test)
            
            try:
                response = loop.run_until_complete(client.get(url, headers=HEADERS, timeout=5, follow_redirects=True))
                status = response.status_code
                
                if status == 200:
                    presence_strs = platform_info.get('presence', [])
                    absence_strs = platform_info.get('absence', [])
                    url_absence_strs = platform_info.get('url_absence', [])
                    
                    response_text = response.text.lower()
                    final_url = str(response.url).lower()
                    
                    has_presence = any(ps.lower() in response_text for ps in presence_strs) if presence_strs else None
                    has_absence = any(as_.lower() in response_text for as_ in absence_strs) if absence_strs else False
                    has_url_absence = any(ua.lower() in final_url for ua in url_absence_strs) if url_absence_strs else False
                    
                    if presence_strs:
                        if has_presence:
                            health = 'working'
                        elif has_absence or has_url_absence:
                            health = 'working'
                        else:
                            health = 'unknown'
                    else:
                        health = 'working'
                elif status == 404:
                    health = 'working'
                elif status == 403 or status == 429:
                    health = 'blocked'
                else:
                    health = 'degraded'
                    
            except Exception as e:
                health = 'error'
                status = 0
            
            health_results.append({
                'platform': platform_name,
                'health': health,
                'status': status,
                'category': platform_info.get('category', 'other')
            })
        
        loop.close()
        return health_results
    
    import threading
    result_queue = queue.Queue()
    
    def run_check():
        try:
            result = check_health()
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
    
    thread = threading.Thread(target=run_check)
    thread.start()
    thread.join(timeout=60)
    
    if not result_queue.empty():
        status, data = result_queue.get_nowait()
        if status == 'complete':
            working = [p for p in data if p['health'] == 'working']
            degraded = [p for p in data if p['health'] in ['degraded', 'unknown']]
            blocked = [p for p in data if p['health'] in ['blocked', 'error']]
            
            result = {
                'platforms': data,
                'summary': {
                    'working': len(working),
                    'degraded': len(degraded),
                    'blocked': len(blocked),
                    'total': len(data)
                },
                'timestamp': now.isoformat()
            }
            
            platform_health_cache['data'] = result
            platform_health_cache['timestamp'] = now
            
            return jsonify(result)
    
    return jsonify({'error': 'Health check timeout', 'platforms': [], 'summary': {'working': 0, 'degraded': 0, 'blocked': 0, 'total': 0}})


@app.route('/api/cache/status', methods=['GET'])
def cache_status():
    return jsonify({
        'cache_info': get_cache_info(),
        'rate_limits': get_rate_limit_status(),
        'request_counts': {
            'email_sherlock': get_request_count_info('email_sherlock'),
            'email_holehe': get_request_count_info('email_holehe'),
            'username': get_request_count_info('username'),
            'social': get_request_count_info('social')
        }
    })


@app.route('/api/cache/clear', methods=['POST'])
def cache_clear():
    count = clear_cache()
    return jsonify({'success': True, 'cleared_entries': count})


@app.route('/api/rate-limits', methods=['GET'])
def rate_limits_status():
    limited_sites = get_rate_limit_status()
    return jsonify({
        'limited_sites': limited_sites,
        'total_limited': len(limited_sites)
    })


WHATSAPP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def normalize_phone_number(phone):
    """Normalize phone number to WhatsApp format (digits only, no + sign)"""
    import re
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('0') and len(digits) > 9:
        digits = digits[1:]
    return digits


@app.route('/api/phone', methods=['POST'])
def phone_osint():
    """Comprehensive phone number OSINT lookup"""
    import phonenumbers
    from phonenumbers import geocoder, carrier, timezone
    
    data = request.get_json()
    phone = data.get('phone', '')
    
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400
    
    result = {
        'phone': phone,
        'valid': False,
        'formatted': None,
        'country': None,
        'country_code': None,
        'region': None,
        'carrier': None,
        'line_type': None,
        'timezone': None,
        'is_valid': False,
        'services': {}
    }
    
    try:
        parsed = phonenumbers.parse(phone, None)
        result['valid'] = phonenumbers.is_valid_number(parsed)
        result['formatted'] = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        
        try:
            country = geocoder.description_for_number(parsed, 'en')
            result['country'] = country
        except:
            pass
        
        try:
            result['country_code'] = f"+{parsed.country_code}"
        except:
            pass
        
        try:
            region = geocoder.description_for_number(parsed, None)
            result['region'] = region
        except:
            pass
        
        try:
            carrier_name = carrier.name_for_number(parsed, 'en')
            result['carrier'] = carrier_name
        except:
            pass
        
        try:
            line_type = carrier._api_for_number(parsed).get('type', 'unknown')
            if callable(line_type):
                line_type = line_type(parsed)
            result['line_type'] = str(line_type)
        except:
            pass
        
        try:
            tz = timezone.time_zones_for_number(parsed)
            result['timezone'] = tz[0] if tz else None
        except:
            pass
        
        normalized = normalize_phone_number(phone)
        result['normalized'] = normalized
        
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            try:
                wa_url = f'https://api.whatsapp.com/send?phone={normalized}'
                wa_response = client.get(wa_url, headers=WHATSAPP_HEADERS)
                wa_text = wa_response.text.lower()
                wa_exists = not any(p in wa_text for p in [
                    'phone number is not on whatsapp',
                    'is unavailable',
                    'cannot send messages'
                ])
                result['services']['whatsapp'] = {
                    'exists': wa_exists,
                    'url': f'https://wa.me/{normalized}'
                }
            except Exception as e:
                result['services']['whatsapp'] = {'error': str(e)}
            
            try:
                tg_url = f'https://t.me/+{normalized}'
                tg_response = client.get(tg_url, headers=HEADERS)
                if tg_response.status_code == 400:
                    result['services']['telegram'] = {'exists': True, 'url': tg_url}
                else:
                    result['services']['telegram'] = {'exists': False}
            except Exception as e:
                result['services']['telegram'] = {'error': str(e)}
        
        search_history.add_entry('phone', phone, f"Valid: {result['valid']}, Country: {result['country']}, Carrier: {result['carrier']}", 1 if result['valid'] else 0)
        
        return jsonify(result)
    
    except phonenumbers.NumberParseException as e:
        return jsonify({'error': f'Invalid phone number format: {str(e)}'}), 400
    except Exception as e:
        logger.error(f"Phone OSINT error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/whatsapp', methods=['POST'])
def whatsapp_lookup():
    """Check if a phone number exists on WhatsApp"""
    data = request.get_json()
    phone = data.get('phone', '')
    
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400
    
    normalized = normalize_phone_number(phone)
    
    if len(normalized) < 10:
        return jsonify({'error': 'Invalid phone number format'}), 400
    
    result = {
        'phone': normalized,
        'query': phone,
        'exists': None,
        'status': 'checking',
        'url': f'https://wa.me/{normalized}'
    }
    
    try:
        url = f'https://api.whatsapp.com/send?phone={normalized}'
        
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            response = client.get(url, headers=WHATSAPP_HEADERS)
            text = response.text.lower()
            
            result['http_status'] = response.status_code
            
            absence_patterns = [
                'phone number is not on whatsapp',
                'is unavailable',
                'cannot send messages to this number',
                'invalid phone number',
                'check the number',
            ]
            
            has_absence = any(pattern in text for pattern in absence_patterns)
            
            if has_absence:
                result['exists'] = False
                result['status'] = 'not_found'
                result['message'] = 'Phone number not found on WhatsApp'
            else:
                result['exists'] = True
                result['status'] = 'found'
                result['message'] = 'Phone number found on WhatsApp'
                
    except httpx.TimeoutException:
        result['status'] = 'timeout'
        result['message'] = 'Request timed out'
    except httpx.ConnectError:
        result['status'] = 'connection_error'
        result['message'] = 'Connection error'
    except Exception as e:
        result['status'] = 'error'
        result['message'] = str(e)
    
    search_history.add_entry('whatsapp', phone, result['message'], 1 if result['exists'] else 0)
    
    return jsonify(result)


@app.route('/api/telegram', methods=['POST'])
def telegram_lookup():
    """Check if a phone number exists on Telegram"""
    data = request.get_json()
    phone = data.get('phone', '')
    
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400
    
    normalized = normalize_phone_number(phone)
    
    if len(normalized) < 10:
        return jsonify({'error': 'Invalid phone number format'}), 400
    
    result = {
        'phone': normalized,
        'query': phone,
        'exists': None,
        'status': 'checking',
        'url': f'https://t.me/+{normalized}'
    }
    
    try:
        url = f'https://t.me/+{normalized}'
        
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            response = client.get(url, headers=HEADERS)
            result['http_status'] = response.status_code
            text = response.text.lower()
            
            if response.status_code == 400:
                result['exists'] = False
                result['status'] = 'not_found'
                result['message'] = 'Invalid Telegram link or number not found'
            elif response.status_code == 200:
                if 'telegram' in text and ('join' in text or 'subscribe' in text or 'confirm' in text):
                    result['exists'] = True
                    result['status'] = 'found'
                    result['message'] = 'Phone number linked to Telegram'
                else:
                    result['exists'] = None
                    result['status'] = 'unknown'
                    result['message'] = 'Unable to determine Telegram status'
            else:
                result['exists'] = None
                result['status'] = 'unknown'
                result['message'] = f'Status code: {response.status_code}'
                
    except httpx.TimeoutException:
        result['status'] = 'timeout'
        result['message'] = 'Request timed out'
    except httpx.ConnectError:
        result['status'] = 'connection_error'
        result['message'] = 'Connection error'
    except Exception as e:
        result['status'] = 'error'
        result['message'] = str(e)
    
    search_history.add_entry('telegram', phone, result['message'], 1 if result['exists'] else 0)
    
    return jsonify(result)


@app.route('/api/carrier', methods=['POST'])
def carrier_lookup():
    """Get carrier and validation information for a phone number"""
    import phonenumbers
    from phonenumbers import carrier, geocoder, NumberParseException
    
    data = request.get_json()
    phone = data.get('phone', '')
    
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400
    
    normalized = normalize_phone_number(phone)
    
    if len(normalized) < 10:
        return jsonify({'error': 'Invalid phone number format'}), 400
    
    result = {
        'phone': normalized,
        'query': phone,
        'carrier': None,
        'line_type': None,
        'country': None,
        'country_code': None,
        'valid': None,
        'status': 'checking',
        'message': None
    }
    
    try:
        phone_to_parse = phone
        if not phone_to_parse.startswith('+'):
            phone_to_parse = '+' + phone_to_parse
        
        parsed = phonenumbers.parse(phone_to_parse, None)
        result['valid'] = phonenumbers.is_valid_number(parsed)
        result['possible'] = phonenumbers.is_possible_number(parsed)
        
        if result['possible']:
            result['country_code'] = f"+{parsed.country_code}"
            
            try:
                location = geocoder.description_for_number(parsed, 'en')
                if location:
                    result['country'] = location
            except:
                pass
            
            try:
                carrier_name = carrier.name_for_number(parsed, 'en')
                if carrier_name:
                    result['carrier'] = carrier_name
            except:
                pass
            
            number_type = phonenumbers.number_type(parsed)
            type_map = {
                phonenumbers.PhoneNumberType.MOBILE: 'Mobile',
                phonenumbers.PhoneNumberType.FIXED_LINE: 'Fixed Line',
                phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: 'Fixed Line or Mobile',
                phonenumbers.PhoneNumberType.PAGER: 'Pager',
                phonenumbers.PhoneNumberType.PERSONAL_NUMBER: 'Personal Number',
                phonenumbers.PhoneNumberType.PREMIUM_RATE: 'Premium Rate',
                phonenumbers.PhoneNumberType.SHARED_COST: 'Shared Cost',
                phonenumbers.PhoneNumberType.TOLL_FREE: 'Toll Free',
                phonenumbers.PhoneNumberType.UAN: 'UAN',
                phonenumbers.PhoneNumberType.UNKNOWN: 'Unknown',
                phonenumbers.PhoneNumberType.VOICEMAIL: 'Voicemail',
                phonenumbers.PhoneNumberType.VOIP: 'VoIP',
            }
            result['line_type'] = type_map.get(number_type, 'Unknown')
            
            if result['valid']:
                result['status'] = 'found'
                result['message'] = f"Valid {result['line_type']} number from {result['country'] or 'Unknown'}"
            else:
                result['status'] = 'not_found'
                result['message'] = 'Number is not valid for any region'
        else:
            result['status'] = 'not_found'
            result['message'] = 'Number format not possible'
            
    except NumberParseException as e:
        result['status'] = 'error'
        result['message'] = f'Failed to parse number: {str(e)}'
    except Exception as e:
        result['status'] = 'error'
        result['message'] = str(e)
    
    search_history.add_entry('carrier', phone, f"{result.get('carrier', 'Unknown')} - {result.get('line_type', 'Unknown')}", 1 if result.get('valid') else 0)
    
    return jsonify(result)


@app.route('/api/phone-lookup', methods=['POST'])
def phone_lookup_all():
    """Check phone number on multiple services"""
    data = request.get_json()
    phone = data.get('phone', '')
    services = data.get('services', ['whatsapp', 'telegram', 'carrier'])
    
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400
    
    results = {}
    
    if 'whatsapp' in services:
        from flask import make_response
        whatsapp_result = whatsapp_lookup()
        results['whatsapp'] = whatsapp_result.get_json()
    
    if 'telegram' in services:
        telegram_result = telegram_lookup()
        results['telegram'] = telegram_result.get_json()
    
    if 'carrier' in services:
        carrier_result = carrier_lookup()
        results['carrier'] = carrier_result.get_json()
    
    return jsonify({
        'phone': normalize_phone_number(phone),
        'query': phone,
        'results': results
    })


def generate_results_pdf(data, search_type, query):
    os.makedirs('reports', exist_ok=True)
    filename = f"reports/{search_type}_{query}_{uuid.uuid4().hex[:8]}.pdf"
    
    doc = SimpleDocTemplate(filename, pagesize=letter)
    story = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, spaceAfter=20)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=14, spaceAfter=10)
    normal_style = styles['Normal']
    
    search_title = {
        'email': f'Email OSINT Report: {query}',
        'username': f'Username OSINT Report: {query}',
        'social': f'Social Media Report: {query}',
        'ip': f'IP Lookup Report: {query}',
        'domain': f'Domain Lookup Report: {query}',
        'person': f'People Search Report: {query}'
    }.get(search_type, f'OSINT Report: {query}')
    
    story.append(Paragraph(search_title, title_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_style))
    story.append(Spacer(1, 20))
    
    if search_type == 'email' or search_type == 'username':
        found = data.get('findings', data.get('account_checks', []))
        found_accounts = [f for f in found if f.get('exists') == True]
        
        story.append(Paragraph(f"Found {len(found_accounts)} accounts", heading_style))
        story.append(Spacer(1, 10))
        
        if found_accounts:
            table_data = [['Platform', 'URL']]
            for f in found_accounts[:200]:
                url = f.get('url', f.get('profile_url', 'N/A'))
                platform = f.get('site', f.get('platform', 'Unknown'))
                table_data.append([platform, url])
            
            table = Table(table_data, colWidths=[2*inch, 4*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkcyan),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            story.append(table)
    
    elif search_type == 'social':
        found = data.get('found', [])
        story.append(Paragraph(f"Found {len(found)} social media accounts", heading_style))
        story.append(Spacer(1, 10))
        
        if found:
            table_data = [['Platform', 'URL', 'Status']]
            for f in found[:200]:
                url = f.get('url', 'N/A')
                platform = f.get('platform', 'Unknown')
                status = f.get('status', 'found')
                table_data.append([platform, url, status])
            
            table = Table(table_data, colWidths=[1.5*inch, 3.5*inch, 1*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkcyan),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            story.append(table)
    
    elif search_type == 'ip':
        story.append(Paragraph('IP Information', heading_style))
        story.append(Spacer(1, 10))
        info_items = [
            ('IP Address', data.get('ip', 'N/A')),
            ('Country', data.get('country', 'N/A')),
            ('City', data.get('city', 'N/A')),
            ('ISP', data.get('isp', 'N/A')),
            ('ASN', data.get('asn', 'N/A')),
            ('Hostname', data.get('hostname', 'N/A')),
        ]
        for label, value in info_items:
            if value:
                story.append(Paragraph(f"<b>{label}:</b> {value}", normal_style))
    
    elif search_type == 'domain':
        story.append(Paragraph('Domain Information', heading_style))
        story.append(Spacer(1, 10))
        if data.get('registrar'):
            story.append(Paragraph(f"<b>Registrar:</b> {data.get('registrar')}", normal_style))
        if data.get('creation_date'):
            story.append(Paragraph(f"<b>Created:</b> {data.get('creation_date')}", normal_style))
        if data.get('expiration_date'):
            story.append(Paragraph(f"<b>Expires:</b> {data.get('expiration_date')}", normal_style))
        if data.get('nameservers'):
            story.append(Paragraph(f"<b>Name Servers:</b> {', '.join(data.get('nameservers', []))}", normal_style))
    
    elif search_type == 'person':
        if data.get('results'):
            story.append(Paragraph('Search Results', heading_style))
            for engine, results in data.get('results', {}).items():
                if results and results.get('results'):
                    story.append(Spacer(1, 10))
                    story.append(Paragraph(f"<b>{engine}:</b>", normal_style))
                    for r in results.get('results', [])[:10]:
                        story.append(Paragraph(f"- {r.get('title', 'N/A')}: {r.get('url', 'N/A')}", normal_style))
    
    story.append(Spacer(1, 30))
    story.append(Paragraph("<i>Generated by OSINT Dashboard</i>", normal_style))
    
    doc.build(story)
    return filename


@app.route('/api/generate-pdf', methods=['POST'])
def generate_pdf():
    data = request.get_json()
    results = data.get('results', {})
    search_type = data.get('type', 'unknown')
    query = data.get('query', 'unknown')
    
    try:
        filename = generate_results_pdf(results, search_type, query)
        return jsonify({'success': True, 'filename': filename, 'download_url': f'/download/{os.path.basename(filename)}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/download/<filename>')
def download_pdf(filename):
    safe_filename = os.path.basename(filename)
    path = os.path.join('reports', safe_filename)
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name=safe_filename)
    return jsonify({'error': 'File not found'}), 404


@app.errorhandler(404)
def not_found_error(e):
    logger.warning(f"404 Not Found: {request.path}")
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"500 Internal Server Error: {str(e)}", exc_info=True)
    return jsonify({'error': 'Internal server error'}), 500


@app.before_request
def log_request_info():
    logger.debug(f"Request: {request.method} {request.path}")


@app.after_request
def log_response_info(response):
    logger.debug(f"Response: {response.status_code}")
    return response


if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
