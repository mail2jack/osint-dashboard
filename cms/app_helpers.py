import os
import re
import socket
import json
import uuid
import asyncio
import threading
import time
import queue
import logging
import httpx
import requests
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from urllib.parse import quote
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from dotenv import load_dotenv

load_dotenv()

perf_logger = logging.getLogger('performance')
req_logger = logging.getLogger('requests')

logger = logging.getLogger(__name__)


def log_performance(operation, duration, details=None):
    msg = f"{operation}: {duration:.3f}s"
    if details:
        msg += f" - {details}"
    perf_logger.info(msg, extra={'extra_data': {
        'operation': operation, 'duration': duration, 'details': details
    }})


def log_request(tool, query, status, found_count=0, checked=0):
    req_logger.info(f"{tool.upper()} | {query} | {status} | found:{found_count} | checked:{checked}",
                    extra={'extra_data': {
                        'tool': tool, 'query': query, 'status': status,
                        'found_count': found_count, 'checked': checked
                    }})


CACHE_TTL_HOURS = 24
result_cache = {}

search_request_counts = {}

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
_searches_lock = threading.Lock()

_active_user_searches: dict = {}
_active_user_searches_lock = threading.Lock()
MAX_CONCURRENT_SEARCHES_PER_USER = 3

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

WHATSAPP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

SHERLOCK_DATA_URL = "https://raw.githubusercontent.com/sherlock-project/sherlock/master/sherlock_project/resources/data.json"

maigret_db = None
search_registry = {}


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


async def check_site_with_retry(client, site_name, site_info, email, max_retries=None):
    from cms.rate_limiting import RETRY_MAX_ATTEMPTS, RETRY_BASE_DELAY, RATE_LIMIT_STATUS_CODES, set_rate_limited
    if max_retries is None:
        max_retries = RETRY_MAX_ATTEMPTS
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


def deduplicate_request(search_type, query, category='all'):
    key = f"{search_type}:{query}:{category}".lower()
    with _searches_lock:
        if key in active_searches:
            age = time.time() - active_searches[key]
            if age > 60:
                del active_searches[key]
            else:
                return None, key
        active_searches[key] = time.time()
    return key, None


def mark_search_complete(key):
    with _searches_lock:
        if key in active_searches:
            del active_searches[key]


def cleanup_stale_searches(max_age_seconds=300):
    now = time.time()
    with _searches_lock:
        stale = [k for k, v in active_searches.items() if now - v > max_age_seconds]
        for k in stale:
            del active_searches[k]


def acquire_search_slot(user_id: str) -> bool:
    """Try to acquire a search slot. Returns True if allowed."""
    with _active_user_searches_lock:
        count = _active_user_searches.get(user_id, 0)
        if count >= MAX_CONCURRENT_SEARCHES_PER_USER:
            return False
        _active_user_searches[user_id] = count + 1
        return True


def release_search_slot(user_id: str):
    """Release a search slot."""
    with _active_user_searches_lock:
        count = _active_user_searches.get(user_id, 0)
        if count <= 1:
            _active_user_searches.pop(user_id, None)
        else:
            _active_user_searches[user_id] = count - 1


def cleanup_stale_search_slots(max_age_seconds: int = 3600):
    pass


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


@lru_cache(maxsize=1)
def get_sherlock_sites():
    try:
        response = requests.get(SHERLOCK_DATA_URL, timeout=30)
        if response.status_code == 200:
            data = response.json()
            data.pop('$schema', None)
            return data
    except Exception as e:
        logger.error(f"Failed to fetch Sherlock sites ({type(e).__name__}): {e}", exc_info=True)
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


async def search_email_async(email, progress_callback=None, limit=30):
    from cms.rate_limiting import is_rate_limited, set_rate_limited, get_rate_limit_status
    cache_key = f'email_sherlock_{limit}'
    cached = get_cached_result(cache_key, email)
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
    except socket.gaierror:
        result['mx_records'] = []
    except Exception as e:
        logger.debug(f"MX lookup failed ({type(e).__name__}): {e}")
        result['mx_records'] = []

    disposable_domains = ['tempmail.com', 'guerrillamail.com', 'mailinator.com', '10minutemail.com', 'throwaway.email', 'temp-mail.org', 'fakeinbox.com', 'maildrop.cc', 'yopmail.com', 'sharklasers.com']
    result['disposable'] = any(d in domain.lower() for d in disposable_domains)

    email_sites = get_sherlock_sites()

    # Priority sites for Quick search (popular platforms with email)
    priority_sites = [
        'Facebook', 'Instagram', 'Twitter', 'TikTok', 'LinkedIn', 'YouTube',
        'WhatsApp', 'Telegram', 'Snapchat', 'Pinterest', 'Reddit', 'GitHub',
        'Dropbox', 'Google', 'Microsoft', 'Apple', 'Amazon', 'Netflix',
        'Spotify', 'Adobe', 'Discord', 'Slack', 'Zoom', 'PayPal', 'Steam',
        'Ebay', 'Airbnb', 'Uber', 'Tinder', 'Bumble'
    ]

    # Apply limit - prioritize important sites
    if limit <= 50:
        priority = {k: v for k, v in email_sites.items() if k in priority_sites}
        remaining = {k: v for k, v in email_sites.items() if k not in priority_sites}
        combined = {**priority, **remaining}
        email_sites = dict(list(combined.items())[:limit])
    else:
        email_sites = dict(list(email_sites.items())[:limit])

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

    set_cached_result(cache_key, email, result.copy())

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
        except Exception:
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
        'ipapi': None,
        'ports': [],
        'reputation_score': 0
    }

    if result['valid']:
        try:
            result['reverse_dns'] = socket.gethostbyaddr(ip_address)[0]
        except socket.herror:
            result['reverse_dns'] = 'N/A'
        except Exception as e:
            logger.debug(f"Reverse DNS failed ({type(e).__name__}): {e}")
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
            logger.debug(f"ip-api lookup failed ({type(e).__name__}): {e}")

        try:
            import ipapi
            ipapi_data = ipapi.location(ip=ip_address, output='json')
            if ipapi_data and 'error' not in ipapi_data:
                result['ipapi'] = {
                    'country_name': ipapi_data.get('country_name', 'N/A'),
                    'region': ipapi_data.get('region', 'N/A'),
                    'city': ipapi_data.get('city', 'N/A'),
                    'postal': ipapi_data.get('postal', 'N/A'),
                    'latitude': ipapi_data.get('latitude'),
                    'longitude': ipapi_data.get('longitude'),
                    'timezone': ipapi_data.get('timezone', 'N/A'),
                    'utc_offset': ipapi_data.get('utc_offset', 'N/A'),
                    'currency': ipapi_data.get('currency', 'N/A'),
                    'currency_name': ipapi_data.get('currency_name', 'N/A'),
                    'asn': ipapi_data.get('asn', 'N/A'),
                    'org': ipapi_data.get('org', 'N/A'),
                    'languages': ipapi_data.get('languages', 'N/A'),
                    'country_capital': ipapi_data.get('country_capital', 'N/A'),
                    'continent_code': ipapi_data.get('continent_code', 'N/A'),
                    'in_eu': ipapi_data.get('in_eu', False),
                    'country_area': ipapi_data.get('country_area'),
                    'country_population': ipapi_data.get('country_population'),
                    'calling_code': ipapi_data.get('country_calling_code', 'N/A')
                }
        except Exception as e:
            logger.debug(f"ipapi lookup failed: {e}")

        common_ports = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 993, 995, 3306, 3389, 5432, 8080]
        for port in common_ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            try:
                if sock.connect_ex((ip_address, port)) == 0:
                    result['ports'].append(port)
            except (socket.error, OSError):
                logger.debug("Port check failed for %s:%s", ip_address, port)
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
        'whois': {},
        'subdomains': [],
        'ssl_info': None
    }

    if result['valid']:
        try:
            result['ip_addresses'] = list(set(socket.getaddrinfo(domain, 80, socket.AF_INET, socket.SOCK_STREAM)))
            result['ip_addresses'] = [r[4][0] for r in result['ip_addresses']]
        except socket.gaierror:
            result['ip_addresses'] = []
        except Exception as e:
            logger.debug(f"IP resolution failed ({type(e).__name__}): {e}")
            result['ip_addresses'] = []

        try:
            dns_types = ['A', 'AAAA', 'MX', 'NS', 'TXT', 'CNAME', 'SPF', 'CAA']
            import dns.resolver
            for dns_type in dns_types:
                try:
                    if dns_type == 'A':
                        result['dns_records']['A'] = socket.getaddrinfo(domain, 80, socket.AF_INET)[0][4][0]
                    elif dns_type == 'AAAA':
                        try:
                            result['dns_records']['AAAA'] = socket.getaddrinfo(domain, 80, socket.AF_INET6)[0][4][0]
                        except (socket.gaierror, OSError):
                            result['dns_records']['AAAA'] = 'N/A'
                        except Exception as e:
                            logger.debug(f"AAAA record lookup failed ({type(e).__name__}): {e}")
                            result['dns_records']['AAAA'] = 'N/A'
                    else:
                        try:
                            answers = dns.resolver.resolve(domain, dns_type)
                            if dns_type == 'MX':
                                result['dns_records']['MX'] = [{'priority': r.preference, 'host': str(r.exchange).rstrip('.')} for r in answers]
                            elif dns_type == 'TXT':
                                result['dns_records']['TXT'] = [str(r).strip('"') for r in answers]
                            elif dns_type == 'SPF':
                                result['dns_records']['SPF'] = [str(r).strip('"') for r in answers]
                            elif dns_type == 'CAA':
                                result['dns_records']['CAA'] = [str(r) for r in answers]
                            else:
                                result['dns_records'][dns_type] = [str(r) for r in answers]
                        except (socket.gaierror, dns.resolver.NoAnswer, dns.exception.Timeout):
                            result['dns_records'][dns_type] = 'N/A'
                        except Exception as e:
                            logger.debug(f"{dns_type} record lookup failed ({type(e).__name__}): {e}")
                            result['dns_records'][dns_type] = 'N/A'
                except Exception as e:
                    logger.debug(f"DNS type processing failed ({type(e).__name__}): {e}")
                    result['dns_records'][dns_type] = 'N/A'
        except ImportError:
            for dns_type in ['A', 'AAAA', 'MX', 'NS', 'TXT', 'CNAME']:
                try:
                    if dns_type == 'A':
                        result['dns_records']['A'] = socket.getaddrinfo(domain, 80, socket.AF_INET)[0][4][0]
                    elif dns_type == 'AAAA':
                        try:
                            result['dns_records']['AAAA'] = socket.getaddrinfo(domain, 80, socket.AF_INET6)[0][4][0]
                        except Exception:
                            result['dns_records']['AAAA'] = 'N/A'
                    else:
                        result['dns_records'][dns_type] = 'Not available'
                except Exception:
                    result['dns_records'][dns_type] = 'N/A'
        except Exception as e:
            result['error'] = str(e)

        try:
            import subprocess
            whois_proc = subprocess.run(['whois', domain], capture_output=True, text=True, timeout=10)
            whois_text = whois_proc.stdout

            def extract_field(text, field_names):
                for name in field_names:
                    for line in text.split('\n'):
                        if line.lower().startswith(name.lower() + ':'):
                            return line.split(':', 1)[1].strip()
                    parts = name.split()
                    if len(parts) > 1:
                        pattern = ' '.join(parts[:2]).lower()
                        for line in text.split('\n'):
                            if pattern in line.lower():
                                return line.split(':', 1)[1].strip()
                return None

            result['whois'] = {
                'registrar': extract_field(whois_text, ['Registrar', 'Sponsoring Registrar', 'Registrar Name']),
                'registration_date': extract_field(whois_text, ['Creation Date', 'Created', 'Created On', 'Created Date']),
                'expiration_date': extract_field(whois_text, ['Expiration Date', 'Expires', 'Expires On', 'Expiry Date', 'Registry Expiry Date']),
                'updated_date': extract_field(whois_text, ['Updated Date', 'Modified', 'Last Updated']),
                'status': extract_field(whois_text, ['Domain Status', 'Status']),
                'name_servers': [],
                'dnssec': extract_field(whois_text, ['DNSSEC']),
                ' registrant': extract_field(whois_text, ['Registrant Name', 'Registrant', 'Owner', 'Holder']),
                'registrant_org': extract_field(whois_text, ['Registrant Organization', 'Org', 'Organization']),
                'registrant_country': extract_field(whois_text, ['Registrant Country', 'Country']),
                'admin_contact': extract_field(whois_text, ['Admin Name', 'Admin', 'Administrative Contact']),
                'tech_contact': extract_field(whois_text, ['Tech Name', 'Tech', 'Technical Contact']),
            }

            for line in whois_text.split('\n'):
                if 'Name Server' in line or 'Nameserver' in line or 'nserver' in line.lower():
                    parts = line.split(':')
                    if len(parts) > 1:
                        ns = parts[1].strip().lower()
                        if ns and ns not in [n.lower() for n in result['whois']['name_servers']]:
                            result['whois']['name_servers'].append(ns)

        except subprocess.TimeoutExpired:
            result['whois'] = {'error': 'WHOIS timeout'}
        except Exception as e:
            result['whois'] = {'error': f"{type(e).__name__}: {e}"}

        common_subdomains = ['www', 'mail', 'ftp', 'admin', 'blog', 'dev', 'api', 'test', 'staging', 'smtp', 'pop', 'imap', 'webmail']
        for sub in common_subdomains:
            try:
                full_domain = f"{sub}.{domain}"
                socket.getaddrinfo(full_domain, 80, socket.AF_INET)
                result['subdomains'].append(full_domain)
            except (socket.gaierror, socket.timeout):
                logger.debug("DNS resolution failed for %s", full_domain)

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
        except Exception as e:
            logger.debug(f"SSL cert lookup failed ({type(e).__name__}): {e}")
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
        except Exception:
            logger.debug("Finding extraction failed for %s", site_info.get('url', '?'))

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
            from cms.services.search_service import (
                extract_google_results, extract_yandex_results,
                extract_bing_results, extract_duckduckgo_results,
                extract_generic_results
            )
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


def _get_overheid_key():
    """Get Overheid.io API key: env var first, then DB Setting."""
    key = os.environ.get('OVERHEID_API_KEY', '')
    if not key:
        try:
            from flask import current_app as app
            with app.app_context():
                from cms.models import Setting
                key = Setting.get('overheid_api_key', '')
        except Exception as e:
            logger.debug(f"_get_overheid_key failed ({type(e).__name__}): {e}")
    return key


def _get_twochat_credentials():
    """Get 2Chat credentials: env var first, then DB Setting."""
    api_key = os.environ.get('TWOCHAT_API_KEY', '')
    number = os.environ.get('TWOCHAT_WHATSAPP_NUMBER', '')
    if not api_key or not number:
        try:
            from flask import current_app as app
            with app.app_context():
                from cms.models import Setting
                if not api_key:
                    api_key = Setting.get('twochat_api_key', '')
                if not number:
                    number = Setting.get('twochat_whatsapp_number', '')
        except Exception as e:
            logger.debug(f"_get_twochat_credentials failed ({type(e).__name__}): {e}")
    return api_key, number


def _get_brave_key():
    """Get Brave Search API key: env var first, then DB Setting."""
    key = os.environ.get('BRAVE_API_KEY', '')
    if not key:
        try:
            from flask import current_app as app
            with app.app_context():
                from cms.models import Setting
                key = Setting.get('brave_api_key', '')
        except Exception as e:
            logger.debug(f"_get_brave_key failed ({type(e).__name__}): {e}")
    return key


def _get_hibp_key():
    """Get Have I Been Pwned API key: env var first, then DB Setting."""
    key = os.environ.get('HIBP_API_KEY', '')
    if not key:
        try:
            from flask import current_app as app
            with app.app_context():
                from cms.models import Setting
                key = Setting.get('hibp_api_key', '')
        except Exception as e:
            logger.debug(f"_get_hibp_key failed ({type(e).__name__}): {e}")
    return key


def normalize_phone_number(phone: str) -> str:
    if not phone:
        return phone
    cleaned = re.sub(r'[^\d+]', '', phone)
    if cleaned.startswith('+'):
        cleaned = cleaned[1:]
    if cleaned.startswith('00'):
        cleaned = cleaned[2:]
    elif cleaned.startswith('0'):
        cleaned = cleaned[1:]
    return cleaned


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


# Public webcam data - organized by country and city
WEBCAM_DATA = {
    'countries': [
        {'code': 'us', 'name': 'United States'},
        {'code': 'uk', 'name': 'United Kingdom'},
        {'code': 'nl', 'name': 'Netherlands'},
        {'code': 'de', 'name': 'Germany'},
        {'code': 'fr', 'name': 'France'},
        {'code': 'jp', 'name': 'Japan'},
        {'code': 'au', 'name': 'Australia'},
        {'code': 'ca', 'name': 'Canada'},
        {'code': 'it', 'name': 'Italy'},
        {'code': 'es', 'name': 'Spain'},
        {'code': 'ch', 'name': 'Switzerland'},
        {'code': 'at', 'name': 'Austria'},
        {'code': 'be', 'name': 'Belgium'},
        {'code': 'no', 'name': 'Norway'},
        {'code': 'se', 'name': 'Sweden'},
    ],
    'webcams': [
        # United States
        {'title': 'Times Square', 'location': 'New York, US', 'url': 'https://www.earthcam.com/fanschoose/nyctimessquare/', 'thumbnail': 'https://videos-3.earthcam.com/fecnetwork/9974.flv/playlist.m3u8', 'country': 'us', 'city': 'new york', 'type': 'stream'},
        {'title': 'Las Vegas Strip', 'location': 'Las Vegas, US', 'url': 'https://www.earthcam.com/fanschoose/vegasstrip/', 'thumbnail': 'https://videos-3.earthcam.com/fecnetwork/4018.flv/playlist.m3u8', 'country': 'us', 'city': 'las vegas', 'type': 'stream'},
        {'title': 'South Beach', 'location': 'Miami Beach, US', 'url': 'https://www.earthcam.com/floridakeys/', 'thumbnail': 'https://videos-3.earthcam.com/fecnetwork/9974.flv/playlist.m3u8', 'country': 'us', 'city': 'miami', 'type': 'stream'},
        {'title': 'French Quarter', 'location': 'New Orleans, US', 'url': 'https://www.earthcam.com/louisiana/bourbonstreet/', 'thumbnail': '', 'country': 'us', 'city': 'new orleans', 'type': 'image'},
        {'title': 'Santa Monica Pier', 'location': 'Santa Monica, US', 'url': 'https://www.santamonica.gov/places/santa-monica-pier-live-camera', 'thumbnail': '', 'country': 'us', 'city': 'santa monica', 'type': 'image'},
        {'title': 'Hawaii Beach', 'location': 'Honolulu, Hawaii, US', 'url': 'https://www.earthcam.com/hawaii/waikiki/', 'thumbnail': '', 'country': 'us', 'city': 'honolulu', 'type': 'image'},
        {'title': 'Denver Downtown', 'location': 'Denver, US', 'url': 'https://www.earthcam.com/colorado/denver/', 'thumbnail': '', 'country': 'us', 'city': 'denver', 'type': 'image'},
        {'title': 'Chicago Skyline', 'location': 'Chicago, US', 'url': 'https://www.earthcam.com/illinois/chicagobridgetower/', 'thumbnail': '', 'country': 'us', 'city': 'chicago', 'type': 'image'},
        # Netherlands
        {'title': 'Dam Square', 'location': 'Amsterdam, NL', 'url': 'https://www.amsterdam.nl/publish/pages/846657/live_camera.html', 'thumbnail': '', 'country': 'nl', 'city': 'amsterdam', 'type': 'image'},
        {'title': 'Leidseplein', 'location': 'Amsterdam, NL', 'url': 'https://www.amsterdam.nl/publish/pages/846657/live_camera.html', 'thumbnail': '', 'country': 'nl', 'city': 'amsterdam', 'type': 'image'},
        {'title': 'Keukenhof Tulips', 'location': 'Lisse, NL', 'url': 'https://www.keukenhof.nl/', 'thumbnail': '', 'country': 'nl', 'city': 'lisse', 'type': 'image'},
        {'title': 'Rotterdam Harbor', 'location': 'Rotterdam, NL', 'url': 'https://www.portofrotterdam.com/en/cameras', 'thumbnail': '', 'country': 'nl', 'city': 'rotterdam', 'type': 'image'},
        # United Kingdom
        {'title': 'Tower Bridge', 'location': 'London, UK', 'url': 'https://www.earthcam.com/uk/london/', 'thumbnail': '', 'country': 'uk', 'city': 'london', 'type': 'image'},
        {'title': 'Abbey Road', 'location': 'London, UK', 'url': 'https://www.abbeyroad.com/crossing', 'thumbnail': '', 'country': 'uk', 'city': 'london', 'type': 'image'},
        {'title': 'Edinburgh Castle', 'location': 'Edinburgh, UK', 'url': 'https://www.edinburgh.gov.uk/cam1', 'thumbnail': '', 'country': 'uk', 'city': 'edinburgh', 'type': 'image'},
        {'title': 'Brighton Pier', 'location': 'Brighton, UK', 'url': 'https://www.brighton.gov.uk/', 'thumbnail': '', 'country': 'uk', 'city': 'brighton', 'type': 'image'},
        # France
        {'title': 'Eiffel Tower', 'location': 'Paris, FR', 'url': 'https://www.earthcam.com/fr/pariseiffeltower/', 'thumbnail': '', 'country': 'fr', 'city': 'paris', 'type': 'image'},
        {'title': 'Louvre', 'location': 'Paris, FR', 'url': 'https://www.earthcam.com/fr/parislouvre/', 'thumbnail': '', 'country': 'fr', 'city': 'paris', 'type': 'image'},
        {'title': 'Nice Beach', 'location': 'Nice, FR', 'url': 'https://www.nicetourisme.com/en/webcams', 'thumbnail': '', 'country': 'fr', 'city': 'nice', 'type': 'image'},
        # Germany
        {'title': 'Brandenburg Gate', 'location': 'Berlin, DE', 'url': 'https://www.earthcam.com/germany/berlin/', 'thumbnail': '', 'country': 'de', 'city': 'berlin', 'type': 'image'},
        {'title': 'Munich Marienplatz', 'location': 'Munich, DE', 'url': 'www.muenchen.de', 'thumbnail': '', 'country': 'de', 'city': 'munich', 'type': 'image'},
        {'title': 'Cologne Cathedral', 'location': 'Cologne, DE', 'url': 'https://www.koelntourguide.de/', 'thumbnail': '', 'country': 'de', 'city': 'cologne', 'type': 'image'},
        # Japan
        {'title': 'Shibuya Crossing', 'location': 'Tokyo, JP', 'url': 'https://www.shinjuku.life/', 'thumbnail': '', 'country': 'jp', 'city': 'tokyo', 'type': 'image'},
        {'title': 'Mount Fuji', 'location': 'Fujinomiya, JP', 'url': 'https://www.shizuoka-guide.com/', 'thumbnail': '', 'country': 'jp', 'city': 'fuji', 'type': 'image'},
        {'title': 'Nara Deer Park', 'location': 'Nara, JP', 'url': 'https://www.narakoubou.com/', 'thumbnail': '', 'country': 'jp', 'city': 'nara', 'type': 'image'},
        # Australia
        {'title': 'Sydney Harbour', 'location': 'Sydney, AU', 'url': 'https://www.sydney.com/', 'thumbnail': '', 'country': 'au', 'city': 'sydney', 'type': 'image'},
        {'title': 'Bondi Beach', 'location': 'Sydney, AU', 'url': 'https://www.bondi.nsw.gov.au/', 'thumbnail': '', 'country': 'au', 'city': 'bondi', 'type': 'image'},
        {'title': 'Melbourne CBD', 'location': 'Melbourne, AU', 'url': 'https://www.melbourne.vic.gov.au/', 'thumbnail': '', 'country': 'au', 'city': 'melbourne', 'type': 'image'},
        # Italy
        {'title': 'Colosseum', 'location': 'Rome, IT', 'url': 'https://www.earthcam.com/italy/rome/', 'thumbnail': '', 'country': 'it', 'city': 'rome', 'type': 'image'},
        {'title': 'Venice Canal', 'location': 'Venice, IT', 'url': 'https://www.italia.it/', 'thumbnail': '', 'country': 'it', 'city': 'venice', 'type': 'image'},
        {'title': 'Florence Duomo', 'location': 'Florence, IT', 'url': 'https://www.museodelduomo.org/', 'thumbnail': '', 'country': 'it', 'city': 'florence', 'type': 'image'},
        # Spain
        {'title': 'La Rambla', 'location': 'Barcelona, ES', 'url': 'https://www.barcelona-tourisme.com/', 'thumbnail': '', 'country': 'es', 'city': 'barcelona', 'type': 'image'},
        {'title': 'Plaza Mayor', 'location': 'Madrid, ES', 'url': 'https://www.esmadrid.com/', 'thumbnail': '', 'country': 'es', 'city': 'madrid', 'type': 'image'},
        {'title': 'Ibiza Beach', 'location': 'Ibiza, ES', 'url': 'https://www.ibiza.travel/', 'thumbnail': '', 'country': 'es', 'city': 'ibiza', 'type': 'image'},
        # Switzerland
        {'title': 'Zermatt Matterhorn', 'location': 'Zermatt, CH', 'url': 'www.zermatt.ch', 'thumbnail': '', 'country': 'ch', 'city': 'zermatt', 'type': 'image'},
        {'title': 'Jungfrau', 'location': 'Jungfraujoch, CH', 'url': 'www.jungfrau.ch', 'thumbnail': '', 'country': 'ch', 'city': 'jungfrau', 'type': 'image'},
        # Austria
        {'title': 'Vienna Square', 'location': 'Vienna, AT', 'url': 'https://www.wien.info/', 'thumbnail': '', 'country': 'at', 'city': 'vienna', 'type': 'image'},
        {'title': 'Salzburg Old Town', 'location': 'Salzburg, AT', 'url': 'https://www.salzburg.info/', 'thumbnail': '', 'country': 'at', 'city': 'salzburg', 'type': 'image'},
        # Belgium
        {'title': 'Grand Place', 'location': 'Brussels, BE', 'url': 'https://www.brussels.be/', 'thumbnail': '', 'country': 'be', 'city': 'brussels', 'type': 'image'},
        {'title': 'Antwerp Harbor', 'location': 'Antwerp, BE', 'url': 'https://www.portofantwerp.com/', 'thumbnail': '', 'country': 'be', 'city': 'antwerp', 'type': 'image'},
        # Norway
        {'title': 'Bergen Harbor', 'location': 'Bergen, NO', 'url': 'https://www.visitbergen.com/', 'thumbnail': '', 'country': 'no', 'city': 'bergen', 'type': 'image'},
        {'title': 'Oslo Opera', 'location': 'Oslo, NO', 'url': 'https://www.oslo.com/', 'thumbnail': '', 'country': 'no', 'city': 'oslo', 'type': 'image'},
        # Sweden
        {'title': 'Stockholm Old Town', 'location': 'Stockholm, SE', 'url': 'https://www.stockholm.se/', 'thumbnail': '', 'country': 'se', 'city': 'stockholm', 'type': 'image'},
        {'title': 'Gothenburg Harbor', 'location': 'Gothenburg, SE', 'url': 'https://www.goteborg.com/', 'thumbnail': '', 'country': 'se', 'city': 'gothenburg', 'type': 'image'},
        # Canada
        {'title': 'Niagara Falls', 'location': 'Ontario, CA', 'url': 'https://www.earthcam.com/canada/niagarafalls/', 'thumbnail': '', 'country': 'ca', 'city': 'niagara', 'type': 'image'},
        {'title': 'Vancouver Harbor', 'location': 'Vancouver, CA', 'url': 'https://www.portmetrovancouver.com/', 'thumbnail': '', 'country': 'ca', 'city': 'vancouver', 'type': 'image'},
        {'title': 'Toronto Skyline', 'location': 'Toronto, CA', 'url': 'https://www.toronto.ca/', 'thumbnail': '', 'country': 'ca', 'city': 'toronto', 'type': 'image'},
    ]
}


def check_ollama_available() -> bool:
    from cms.services.ai_service import get_ollama_config
    config = get_ollama_config()
    if not config.get('url'):
        return False
    try:
        import httpx
        r = httpx.get(f"{config['url']}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


__all__ = [
    'perf_logger', 'req_logger', 'log_performance', 'log_request',
    'result_cache', 'search_request_counts', 'active_searches', '_searches_lock',
    '_active_user_searches', '_active_user_searches_lock', 'MAX_CONCURRENT_SEARCHES_PER_USER',
    'acquire_search_slot', 'release_search_slot', 'cleanup_stale_search_slots',
    'get_sherlock_sites', 'search_email_async', 'search_email_holehe', 'search_email_combined',
    'lookup_email', 'lookup_email_holehe', 'lookup_email_combined',
    'search_username', 'search_username_maigret', 'search_username_async',
    'lookup_ip', 'lookup_domain',
    '_get_overheid_key', '_get_twochat_credentials', '_get_brave_key', '_get_hibp_key',
    'check_ollama_available', 'WEBCAM_DATA',
    'SearchJob', 'search_registry', 'get_maigret_sites_dict',
    'normalize_phone_number', 'generate_results_pdf',
    'CACHE_TTL_HOURS', 'get_cached_result', 'set_cached_result', 'clear_cache', 'get_cache_info',
    'deduplicate_request', 'mark_search_complete', 'cleanup_stale_searches',
]
