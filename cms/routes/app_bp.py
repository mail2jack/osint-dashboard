import logging
import json
import os
import asyncio
import time
import re
import requests
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Blueprint, request, jsonify, Response as FlaskResponse, send_file

from .. import csrf
from ..app_helpers import (
    acquire_search_slot,
    release_search_slot,
    MAX_CONCURRENT_SEARCHES_PER_USER,
    lookup_email,
    lookup_ip,
    lookup_domain,
    _get_overheid_key,
    _get_hibp_key,
    WEBCAM_DATA,
    search_email_async,
    get_sherlock_sites,
    search_email_holehe,
    search_email_combined,
    search_username,
    search_registry,
    generate_results_pdf,
    check_ollama_available,
)
from cms.rate_limiting import rate_limit, DEFAULT_RATE_LIMIT, STRICT_RATE_LIMIT
from cms.api_key_auth import api_key_required
from cms.feature_flags import tool_enabled
from cms.validation import validate
from cms.cache import get as cache_get, set as cache_set
from cms.validation import (
    AISummarizeSchema, AIAnalyzeQuerySchema, AIEnrichProfileSchema,
    PersonSearchSchema, EmailQuerySchema, IPQuerySchema,
    DomainQuerySchema, OpenKVKQuerySchema, WebcamQuerySchema,
    HIBPQuerySchema, UsernameQuerySchema, EmailStreamSchema,
    EmailHoleheSchema, EmailCombinedSchema, EmailCrossValidatedSchema,
    UsernameRapidAPISchema, GeneratePDFSchema,
)
from cms.services.ai_service import get_ollama_config, summarize_results, analyze_natural_language, enrich_profile
from cms.services.search_service import search_person
from search_history import search_history

logger = logging.getLogger(__name__)

app_routes_bp = Blueprint('app_routes', __name__)


# =============================================================================
# AI Routes
# =============================================================================


@app_routes_bp.route('/api/ai/status', methods=['GET'])
def ai_status() -> FlaskResponse:
    """Check if Ollama AI is available."""
    available = check_ollama_available()
    _, model = get_ollama_config()
    return jsonify({
        'available': available,
        'model': model if available else None,
        'message': 'AI features ready' if available else 'Ollama not running. Install from https://ollama.com'
    })


@app_routes_bp.route('/api/ai/summarize', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('ai')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='ai_summarize')
@validate(AISummarizeSchema)
def ai_summarize() -> FlaskResponse:
    """Generate AI summary of search results."""
    query = request.validated_data.get('query', '')
    tool = request.validated_data.get('tool', 'unknown')
    findings = request.validated_data.get('findings', [])

    if not check_ollama_available():
        return jsonify({'error': 'Ollama not available'}), 503

    summary = summarize_results(query, tool, findings)
    return jsonify({'summary': summary})


@app_routes_bp.route('/api/ai/analyze-query', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('ai')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='ai_analyze')
@validate(AIAnalyzeQuerySchema)
def ai_analyze_query() -> FlaskResponse:
    """Convert natural language to search parameters."""
    user_query = request.validated_data.get('query', '')

    if not check_ollama_available():
        return jsonify({'error': 'Ollama not available'}), 503

    available_tools = ['social', 'email', 'username', 'maigret', 'phone', 'person', 'ip', 'domain']
    result = analyze_natural_language(user_query, available_tools)
    return jsonify(result)


@app_routes_bp.route('/api/ai/enrich-profile', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('ai')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='ai_enrich')
@validate(AIEnrichProfileSchema)
def ai_enrich_profile() -> FlaskResponse:
    """Generate AI insights for a profile."""
    platform = request.validated_data.get('platform', 'Unknown')
    username = request.validated_data.get('username', '')
    info = request.validated_data.get('info', {})

    if not check_ollama_available():
        return jsonify({'error': 'Ollama not available'}), 503

    analysis = enrich_profile(platform, username, info)
    return jsonify({'analysis': analysis})


# =============================================================================
# History Routes
# =============================================================================


@app_routes_bp.route('/api/history', methods=['GET'])
def get_history() -> FlaskResponse:
    return jsonify(search_history.get_history(limit=50))


@app_routes_bp.route('/api/archive', methods=['GET'])
def get_archive() -> FlaskResponse:
    query = request.args.get('q', '')
    tool = request.args.get('tool', '')
    limit = int(request.args.get('limit', 100))
    return jsonify(search_history.get_archive(limit=limit, search_query=query, search_tool=tool if tool else None))


@app_routes_bp.route('/api/history/archive/<entry_id>', methods=['POST'])
@csrf.exempt
def archive_entry(entry_id) -> FlaskResponse:
    search_history.archive_entry(entry_id)
    return jsonify({'success': True})


@app_routes_bp.route('/api/history/archive-all', methods=['POST'])
@csrf.exempt
def archive_all() -> FlaskResponse:
    count = search_history.archive_all()
    return jsonify({'success': True, 'archived_count': count})


@app_routes_bp.route('/api/history/mark-read/<entry_id>', methods=['POST'])
@csrf.exempt
def mark_read(entry_id) -> FlaskResponse:
    search_history.mark_read(entry_id)
    return jsonify({'success': True})


@app_routes_bp.route('/api/history/mark-all-read', methods=['POST'])
@csrf.exempt
def mark_all_read() -> FlaskResponse:
    search_history.mark_all_read()
    return jsonify({'success': True})


@app_routes_bp.route('/api/history/stats', methods=['GET'])
def get_history_stats() -> FlaskResponse:
    return jsonify(search_history.get_stats())


@app_routes_bp.route('/api/search/stop/<job_id>', methods=['POST'])
@csrf.exempt
def stop_search(job_id) -> FlaskResponse:
    if job_id in search_registry:
        search_registry[job_id].cancel()
        return jsonify({'success': True, 'job_id': job_id})
    return jsonify({'success': False, 'error': 'Job not found'}), 404


@app_routes_bp.route('/api/search/progress/<job_id>', methods=['GET'])
def get_search_progress(job_id) -> FlaskResponse:
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


# =============================================================================
# OSINT Person Search Stream
# =============================================================================


@app_routes_bp.route('/api/person/stream', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('username')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='person')
@validate(PersonSearchSchema)
def person_search_stream() -> FlaskResponse:
    from flask import Response, stream_with_context
    import threading
    import queue

    name = request.validated_data.get('name', '')
    if not name:
        return jsonify({'error': 'Name required'}), 400

    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        if not acquire_search_slot(current_user.id):
            return jsonify({'error': f'Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete.'}), 429

    result_queue = queue.Queue()

    def run_search():
        try:
            result = search_person(name)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', f"{type(e).__name__}: {str(e)}"))
        finally:
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    thread = threading.Thread(target=run_search)
    thread.start()

    def generate():
        while True:
            try:
                msg_type, msg_data = result_queue.get(timeout=30)
                if msg_type == 'complete':
                    yield f"data: {json.dumps({'complete': True, 'result': msg_data})}\n\n"
                    break
                elif msg_type == 'error':
                    yield f"data: {json.dumps({'complete': True, 'error': msg_data})}\n\n"
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'complete': True, 'result': {'name': name, 'search_links': [], 'error': 'Timeout'}})}\n\n"
                break

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


# =============================================================================
# IP / Email / Domain Lookup
# =============================================================================


@app_routes_bp.route('/api/email', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('email')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='email')
@validate(EmailQuerySchema)
def email_lookup() -> FlaskResponse:
    email = request.validated_data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    cached = cache_get('email', email)
    if cached:
        return jsonify(cached)
    result = lookup_email(email)
    cache_set('email', email, result, timeout=300)
    return jsonify(result)


@app_routes_bp.route('/api/ip', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('ip')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='ip')
@validate(IPQuerySchema)
def ip_lookup() -> FlaskResponse:
    ip = request.validated_data.get('ip', '')
    if not ip:
        return jsonify({'error': 'IP address required'}), 400
    cached = cache_get('ip', ip)
    if cached:
        return jsonify(cached)
    result = lookup_ip(ip)
    cache_set('ip', ip, result, timeout=3600)
    return jsonify(result)


@app_routes_bp.route('/api/domain', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('domain')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='domain')
@validate(DomainQuerySchema)
def domain_lookup() -> FlaskResponse:
    domain = request.validated_data.get('domain', '')
    if not domain:
        return jsonify({'error': 'Domain required'}), 400
    cached = cache_get('domain', domain)
    if cached:
        return jsonify(cached)
    result = lookup_domain(domain)
    cache_set('domain', domain, result, timeout=3600)
    return jsonify(result)


# =============================================================================
# OpenKVK (Dutch Business Registry)
# =============================================================================


@app_routes_bp.route('/api/openkvk', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('openkvk')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='openkvk')
@validate(OpenKVKQuerySchema)
def openkvk_lookup() -> FlaskResponse:
    query = request.validated_data.get('query', '')
    if not query:
        return jsonify({'error': 'Company name, KVK number, or postcode required'}), 400

    cached = cache_get('openkvk', query)
    if cached:
        return jsonify(cached)

    api_key = _get_overheid_key()
    result = {
        'query': query,
        'results': [],
        'error': None,
        'configured': bool(api_key)
    }

    if not api_key:
        result['error'] = 'Overheid.io API key not configured'
        result['setup_hint'] = 'Set via Settings > API Keys (overheid_api_key) or OVERHEID_API_KEY env var. Get free key at https://overheid.io'
        return jsonify(result)

    try:
        clean_query = quote(query)
        search_url = f'https://api.overheid.io/v3/openkvk?query={clean_query}&size=20'

        headers = {
            'Accept': 'application/json',
            'ovio-api-key': api_key
        }

        response = requests.get(search_url, headers=headers, timeout=15)

        if response.status_code == 200:
            data = response.json()
            bedrijven = data.get('_embedded', {}).get('bedrijf', [])

            for company in bedrijven:
                slug = company.get('_links', {}).get('self', {}).get('href', '')
                if slug:
                    slug = slug.lstrip('/')
                    detail_url = f'https://api.overheid.io/v3/openkvk/{slug}'
                    try:
                        detail_resp = requests.get(detail_url, headers=headers, timeout=10)
                        if detail_resp.status_code == 200:
                            detail = detail_resp.json()
                            company.update(detail)
                    except requests.RequestException as e:
                        logger.debug(f"OpenKVK detail fetch failed ({type(e).__name__}): {e}")

                result['results'].append({
                    'kvknummer': company.get('kvkNummer') or company.get('kvknummer'),
                    'naam': company.get('naam') or (company.get('huidigeHandelsNamen', [''])[0] if company.get('huidigeHandelsNamen') else ''),
                    'handelsnamen': company.get('huidigeHandelsNamen', []),
                    'rechtsvorm': company.get('rechtsvormOmschrijving'),
                    'activiteit': company.get('activiteitomschrijving'),
                    'sbi_codes': company.get('sbi', []),
                    'website': company.get('website'),
                    'bezoekadres': None,
                    'postcode': None,
                    'plaats': None,
                    'land': None,
                    'coords': None,
                    'inschrijvingstype': company.get('inschrijvingstype'),
                    'actief': company.get('actief', True),
                    'vestigingsnummer': company.get('vestigingsnummer'),
                    'updated_at': company.get('updated_at'),
                    'details_url': slug
                })

                bezoek = company.get('bezoeklocatie', {})
                if bezoek:
                    addr = bezoek.get('straat', '')
                    huisnr = bezoek.get('huisnummer', '')
                    result['results'][-1]['bezoekadres'] = f"{addr} {huisnr}".strip()
                    result['results'][-1]['postcode'] = bezoek.get('postcode')
                    result['results'][-1]['plaats'] = bezoek.get('plaats')
                    result['results'][-1]['land'] = bezoek.get('land')

                loc = company.get('locatie', {})
                if loc:
                    result['results'][-1]['coords'] = {
                        'lat': loc.get('lat'),
                        'lon': loc.get('lon')
                    }

            result['total'] = data.get('totalItemCount', len(result['results']))

        elif response.status_code == 404:
            result['error'] = 'No results found'
        else:
            result['error'] = f'API error: {response.status_code}'

    except requests.Timeout:
        result['error'] = 'Request timed out'
    except requests.RequestException as e:
        result['error'] = f"Request failed: {str(e)}"
    except Exception as e:
        result['error'] = f"Unexpected error ({type(e).__name__}): {str(e)}"

    search_history.add_entry('openkvk', query, f"{len(result['results'])} results found", len(result['results']))

    return jsonify(result)


# =============================================================================
# Webcam Lookup
# =============================================================================


@app_routes_bp.route('/api/webcam', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('webcam')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='webcam')
@validate(WebcamQuerySchema)
def webcam_lookup() -> FlaskResponse:
    query = request.validated_data.get('query', '').lower().strip()
    country_code = request.validated_data.get('country', '').lower().strip()

    webcams = WEBCAM_DATA['webcams'].copy()
    results = []
    selected_country = None

    # Country name to code mapping
    country_map = {
        'united states': 'us', 'usa': 'us', 'us': 'us', 'america': 'us',
        'united kingdom': 'uk', 'uk': 'uk', 'britain': 'uk', 'england': 'uk',
        'netherlands': 'nl', 'holland': 'nl', 'nl': 'nl',
        'germany': 'de', 'deutschland': 'de', 'de': 'de',
        'france': 'fr', 'fr': 'fr',
        'japan': 'jp', 'jp': 'jp',
        'australia': 'au', 'au': 'au',
        'canada': 'ca', 'ca': 'ca',
        'italy': 'it', 'it': 'it',
        'spain': 'es', 'es': 'es',
    }

    # Convert country name to code if needed
    if query in country_map:
        country_code = country_map[query]
        query = ''

    # If country code specified
    if country_code:
        results = [w for w in webcams if w['country'] == country_code]
        selected_country = next((c for c in WEBCAM_DATA['countries'] if c['code'] == country_code), None)
    # If search query
    elif query:
        # Search by city, country, or title
        results = [w for w in webcams if
                   query in w['city'].lower() or
                   query in w['country'].lower() or
                   query in w['title'].lower() or
                   query in w['location'].lower()]
    else:
        # Return all webcams
        results = webcams

    return jsonify({
        'webcams': results[:24],  # Limit to 24
        'countries': WEBCAM_DATA['countries'],
        'selected_country': selected_country
    })


# =============================================================================
# HIBP (Have I Been Pwned)
# =============================================================================


@app_routes_bp.route('/api/hibp', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('hibp')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='hibp')
@validate(HIBPQuerySchema)
def hibp_check() -> FlaskResponse:
    email = request.validated_data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400

    cached = cache_get('hibp', email)
    if cached:
        return jsonify(cached)

    hibp_key = _get_hibp_key()
    if not hibp_key:
        return jsonify({'email': email, 'no_api_key': True, 'breaches': []})

    try:
        headers = {
            'User-Agent': 'OSINT-Dashboard',
            'hibp-api-key': hibp_key
        }

        response = requests.get(
            f'https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email)}?truncateResponse=false',
            headers=headers,
            timeout=15
        )

        if response.status_code == 200:
            breaches = response.json()
            result = {'email': email, 'found': True, 'breaches': breaches}
            cache_set('hibp', email, result, timeout=3600)
            return jsonify(result)
        elif response.status_code == 404:
            result = {'email': email, 'found': False, 'breaches': []}
            cache_set('hibp', email, result, timeout=3600)
            return jsonify(result)
        elif response.status_code == 401:
            return jsonify({'email': email, 'error': 'Invalid API key', 'no_api_key': True, 'breaches': []})
        elif response.status_code == 429:
            return jsonify({'email': email, 'error': 'Rate limited', 'breaches': []})
        else:
            return jsonify({'email': email, 'error': f'API error: {response.status_code}', 'breaches': []})

    except requests.Timeout:
        return jsonify({'email': email, 'error': 'Request timeout', 'breaches': []})
    except Exception as e:
        logger.error(f"HIBP check error ({type(e).__name__}): {e}", exc_info=True)
        return jsonify({'email': email, 'error': str(e), 'breaches': []})


# =============================================================================
# Username Stream
# =============================================================================


@app_routes_bp.route('/api/username/stream', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('username')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='username_stream')
@validate(UsernameQuerySchema)
def username_search_stream() -> FlaskResponse:
    from flask import Response, stream_with_context
    import threading
    import queue

    username = request.validated_data.get('username', '')
    if not username:
        return jsonify({'error': 'Username required'}), 400

    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        if not acquire_search_slot(current_user.id):
            return jsonify({'error': f'Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete.'}), 429

    result_queue = queue.Queue()
    stop_event = threading.Event()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0, '_stop': stop_event}

    def progress_callback(progress):
        if stop_event.is_set():
            return
        progress_state.update(progress)

    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_email_async(email, progress_callback, limit))
            found_count = result.get('found_count', 0)
            search_history.add_entry('email', email, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', f"{type(e).__name__}: {str(e)}"))
        finally:
            loop.close()
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    email_sites = get_sherlock_sites()

    if not email_sites:
        return jsonify({'error': 'Could not load site data'}), 400

    progress_state['total'] = limit

    thread = threading.Thread(target=run_search_thread, daemon=True)
    thread.start()

    def generate():
        try:
            while not stop_event.is_set():
                try:
                    status, data = result_queue.get_nowait()
                    if status == 'complete':
                        yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                    else:
                        yield f"data: {json.dumps({'error': data})}\n\n"
                    break
                except queue.Empty:
                    if request.is_disconnected:
                        stop_event.set()
                        return
                    time.sleep(0.1)
                    total = progress_state['total']
                    checked = progress_state['checked']
                    found = progress_state['found']
                    current_site = progress_state['current_site']

                    yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
        finally:
            stop_event.set()

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


# =============================================================================
# Email Stream
# =============================================================================


@app_routes_bp.route('/api/email/stream', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('email')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='email_stream')
@validate(EmailStreamSchema)
def email_search_stream() -> FlaskResponse:
    from flask import Response, stream_with_context
    import threading
    import queue

    email = request.validated_data.get('email', '')
    tags = request.validated_data.get('tags', ['all'])
    if not email:
        return jsonify({'error': 'Email required'}), 400

    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        if not acquire_search_slot(current_user.id):
            return jsonify({'error': f'Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete.'}), 429

    # Map tags to site limits (handle both string and numeric)
    limit = 30  # Default to Quick
    for tag in tags:
        if isinstance(tag, str) and tag.isdigit():
            limit = max(limit, int(tag))
        elif isinstance(tag, int):
            limit = max(limit, tag)
        elif tag in ['social', '30']:
            limit = max(limit, 30)
        elif tag in ['developer', '50']:
            limit = max(limit, 50)
        elif tag in ['gaming', '100']:
            limit = max(limit, 100)
        elif tag in ['all', '200']:
            limit = max(limit, 200)

    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}

    def progress_callback(progress):
        progress_state.update(progress)

    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_email_async(email, progress_callback, limit))
            found_count = result.get('found_count', 0)
            search_history.add_entry('email', email, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', f"{type(e).__name__}: {str(e)}"))
        finally:
            loop.close()
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    email_sites = get_sherlock_sites()

    if not email_sites:
        return jsonify({'error': 'Could not load site data'}), 400

    progress_state['total'] = limit

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


# =============================================================================
# Email Holehe
# =============================================================================


@app_routes_bp.route('/api/email/holehe', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('email_holehe')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='email_holehe')
@validate(EmailHoleheSchema)
def email_holehe() -> FlaskResponse:
    from flask import Response, stream_with_context
    import threading
    import queue
    from argparse import Namespace

    email = request.validated_data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400

    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        if not acquire_search_slot(current_user.id):
            return jsonify({'error': f'Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete.'}), 429

    stop_event = threading.Event()
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0, '_stop': stop_event}

    def progress_callback(progress):
        if stop_event.is_set():
            return
        progress_state.update(progress)

    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_email_holehe(email, progress_callback))
            search_history.add_entry('holehe', email, f'{result.get("found_count", 0)} accounts found', result.get('found_count', 0))
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', f"{type(e).__name__}: {str(e)}"))
        finally:
            loop.close()
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    from holehe.core import import_submodules, get_functions
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    progress_state['total'] = len(websites)

    thread = threading.Thread(target=run_search_thread, daemon=True)
    thread.start()

    def generate():
        try:
            while not stop_event.is_set():
                try:
                    status, data = result_queue.get_nowait()
                    if status == 'complete':
                        yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                    else:
                        yield f"data: {json.dumps({'error': data})}\n\n"
                    break
                except queue.Empty:
                    if request.is_disconnected:
                        stop_event.set()
                        return
                    time.sleep(0.1)
                    total = progress_state['total']
                    checked = progress_state['checked']
                    found = progress_state['found']
                    current_site = progress_state['current_site']

                    yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
        finally:
            stop_event.set()

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


# =============================================================================
# Email Combined
# =============================================================================


@app_routes_bp.route('/api/email/combined', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('email_holehe')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='email_combined')
@validate(EmailCombinedSchema)
def email_combined() -> FlaskResponse:
    from flask import Response, stream_with_context
    import threading
    import queue

    email = request.validated_data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400

    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        if not acquire_search_slot(current_user.id):
            return jsonify({'error': f'Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete.'}), 429

    stop_event = threading.Event()
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0, '_stop': stop_event}

    def progress_callback(progress):
        if stop_event.is_set():
            return
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
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    from holehe.core import import_submodules, get_functions
    from argparse import Namespace
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    sherlock_sites = get_sherlock_sites() or {}
    holehe_total = len(websites)
    sherlock_total = len(sherlock_sites)
    progress_state['total'] = sherlock_total + holehe_total

    thread = threading.Thread(target=run_search_thread, daemon=True)
    thread.start()

    def generate():
        try:
            while not stop_event.is_set():
                try:
                    status, data = result_queue.get_nowait()
                    if status == 'complete':
                        yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                    else:
                        yield f"data: {json.dumps({'error': data})}\n\n"
                    break
                except queue.Empty:
                    if request.is_disconnected:
                        stop_event.set()
                        return
                    time.sleep(0.1)
                    total = progress_state['total']
                    checked = progress_state['checked']
                    found = progress_state['found']
                    current_site = progress_state['current_site']

                    yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
        finally:
            stop_event.set()

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


# =============================================================================
# Email Cross-validated
# =============================================================================


@app_routes_bp.route('/api/email/crossvalidated', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('email_holehe')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='email_crossvalidated')
@validate(EmailCrossValidatedSchema)
def email_cross_validated() -> FlaskResponse:
    from flask import Response, stream_with_context
    import threading
    import queue

    email = request.validated_data.get('email', '')
    if not email:
        return jsonify({'error': 'Email required'}), 400

    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        if not acquire_search_slot(current_user.id):
            return jsonify({'error': f'Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete.'}), 429

    stop_event = threading.Event()
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0, '_stop': stop_event}

    def progress_callback(progress):
        if stop_event.is_set():
            return
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
            result_queue.put(('error', f"{type(e).__name__}: {str(e)}"))
        finally:
            loop.close()
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    from holehe.core import import_submodules, get_functions
    from argparse import Namespace
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    sherlock_sites = get_sherlock_sites() or {}
    holehe_total = len(websites)
    sherlock_total = len(sherlock_sites)
    progress_state['total'] = sherlock_total + holehe_total

    thread = threading.Thread(target=run_search_thread, daemon=True)
    thread.start()

    def generate():
        try:
            while not stop_event.is_set():
                try:
                    status, data = result_queue.get_nowait()
                    if status == 'complete':
                        yield f"data: {json.dumps({'complete': True, 'result': data})}\n\n"
                    else:
                        yield f"data: {json.dumps({'error': data})}\n\n"
                    break
                except queue.Empty:
                    if request.is_disconnected:
                        stop_event.set()
                        return
                    time.sleep(0.1)
                    total = progress_state['total']
                    checked = progress_state['checked']
                    found = progress_state['found']
                    current_site = progress_state['current_site']

                    yield f"data: {json.dumps({'progress': {'checked': checked, 'total': total, 'found': found, 'percent': int((checked / total) * 100) if total > 0 else 0, 'current_site': current_site}})}\n\n"
        finally:
            stop_event.set()

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


# =============================================================================
# Username Search
# =============================================================================


@app_routes_bp.route('/api/username', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('username')
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='username')
@validate(UsernameQuerySchema)
def username_search() -> FlaskResponse:
    username = request.validated_data.get('username', '')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    return jsonify(search_username(username))


# =============================================================================
# Username RapidAPI
# =============================================================================


@app_routes_bp.route('/api/username/rapidapi', methods=['POST'])
@csrf.exempt
@api_key_required
@tool_enabled('username')
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='username_rapidapi')
@validate(UsernameRapidAPISchema)
def username_rapidapi() -> FlaskResponse:
    """Check username availability via RapidAPI, fallback to Sherlock."""
    from cms.models import Setting
    username = request.validated_data.get('username', '')
    if not username:
        return jsonify({'error': 'Username required'}), 400

    USAGE_LIMIT = 100
    now_month = datetime.now(timezone.utc).strftime('%Y-%m')
    stored_month = Setting.get('rapidapi_username_month')
    used_count = int(Setting.get('rapidapi_username_used') or '0') if stored_month == now_month else 0
    api_key = Setting.get('rapidapi_username_key')

    usage_info = {
        'used': used_count,
        'limit': USAGE_LIMIT,
        'remaining': max(0, USAGE_LIMIT - used_count),
    }

    # Try RapidAPI
    if api_key and used_count < USAGE_LIMIT:
        try:
            headers = {
                'x-rapidapi-key': api_key,
                'x-rapidapi-host': 'osint-username-availability-brand-checker-api.p.rapidapi.com',
            }
            resp = requests.get(
                f'https://osint-username-availability-brand-checker-api.p.rapidapi.com/check?username={username}',
                headers=headers,
                timeout=15
            )
            data = resp.json()

            # Increment counter
            Setting.set('rapidapi_username_month', now_month)
            Setting.set('rapidapi_username_used', str(used_count + 1))
            usage_info['used'] = used_count + 1
            usage_info['remaining'] = max(0, USAGE_LIMIT - used_count - 1)

            # Verify "taken" results to reduce false positives
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                results_list = data.get('results', [])
                taken = [r for r in results_list if r.get('available') is True and r.get('url')]
                if taken:
                    fp_patterns = [
                        rb'\b(not found|no results?|doesn\'t exist|profile not found)\b',
                        rb'\b(404|page not found|this page doesn\'t exist)\b',
                        rb'\b(invalid user|user invalid|username not|user not found)\b',
                        rb'\b(removed this|content removed|deleted account|this account)\b',
                        rb'(sign up|create account|log in|login).{0,50}(to view|to see)',
                        rb'(view profile|profile).{0,30}(requires|need).{0,30}(login|sign in)',
                        rb'\b(error|404|403|400)\b.{0,20}\b(page|content)',
                    ]
                    fp_compiled = [re.compile(p, re.I) for p in fp_patterns]

                    def _verify(url, timeout=5):
                        try:
                            r = requests.get(url, timeout=timeout, allow_redirects=True, headers={'User-Agent': 'Mozilla/5.0'})
                            body = r.content[:2048].lower()
                            # Check false-positive patterns
                            for pat in fp_compiled:
                                if pat.search(body):
                                    return False, 'likely_false_positive'
                            # Check if username appears in response
                            username_bytes = username.lower().encode()
                            if username_bytes in body:
                                return True, 'verified'
                            # Tiny response (<500 bytes) likely a generic page
                            if len(body) < 500:
                                return False, 'too_small'
                            return True, 'unconfirmed'
                        except Exception as e:
                            logger.debug(f"RapidAPI verify failed ({type(e).__name__}): {e}")
                            return None, 'unreachable'

                    with ThreadPoolExecutor(max_workers=10) as pool:
                        fut_map = {pool.submit(_verify, r['url']): r for r in taken}
                        for fut in as_completed(fut_map):
                            r = fut_map[fut]
                            verified, note = fut.result()
                            if verified is False:
                                r['available'] = False
                            r['verified'] = verified
                            r['verification_note'] = note
            except Exception as ve:
                logger.warning(f"RapidAPI verification failed: {ve}")

            return jsonify({
                'source': 'rapidapi',
                'username': username,
                'results': data,
                'api_usage': usage_info,
            })
        except Exception as e:
            logger.error(f"RapidAPI username check failed for '{username}' ({type(e).__name__}): {e}")

    # Fallback to Sherlock
    usage_info['note'] = 'Maandlimiet bereikt of API niet geconfigureerd - gebruik Sherlock'
    return jsonify({
        'source': 'sherlock',
        'username': username,
        'fallback_to_sherlock': True,
        'api_usage': usage_info,
    })


@app_routes_bp.route('/api/username/rapidapi-status', methods=['GET'])
def username_rapidapi_status() -> FlaskResponse:
    """Return current RapidAPI usage status."""
    from cms.models import Setting
    now_month = datetime.now(timezone.utc).strftime('%Y-%m')
    stored_month = Setting.get('rapidapi_username_month')
    used_count = int(Setting.get('rapidapi_username_used') or '0') if stored_month == now_month else 0
    api_key = Setting.get('rapidapi_username_key')
    return jsonify({
        'configured': bool(api_key),
        'used': used_count,
        'limit': 100,
        'remaining': max(0, 100 - used_count),
    })


# =============================================================================
# PDF Routes
# =============================================================================


@app_routes_bp.route('/api/generate-pdf', methods=['POST'])
@csrf.exempt
@validate(GeneratePDFSchema)
def generate_pdf() -> FlaskResponse:
    results = request.validated_data.get('results', {})
    search_type = request.validated_data.get('type', 'unknown')
    query = request.validated_data.get('query', 'unknown')

    try:
        filename = generate_results_pdf(results, search_type, query)
        return jsonify({'success': True, 'filename': filename, 'download_url': f'/download/{os.path.basename(filename)}'})
    except Exception as e:
        logger.error(f"PDF generation error ({type(e).__name__}): {e}")
        return jsonify({'error': str(e)}), 500


@app_routes_bp.route('/download/<filename>')
def download_pdf(filename) -> FlaskResponse:
    safe_filename = os.path.basename(filename)
    path = os.path.join('reports', safe_filename)
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name=safe_filename)
    return jsonify({'error': 'File not found'}), 404


# =============================================================================
# Phone Routes (added via add_url_rule)
# =============================================================================

from cms.services.phone_service import phone_osint, whatsapp_lookup, check_whatsapp_2chat, telegram_lookup, carrier_lookup, phone_lookup_all

# Apply rate limiting to phone_osint
phone_osint = rate_limit(limit=STRICT_RATE_LIMIT, key_prefix='phone')(phone_osint)

# Register routes
app_routes_bp.add_url_rule('/api/phone', 'phone_osint', csrf.exempt(phone_osint), methods=['POST'])
app_routes_bp.add_url_rule('/api/whatsapp', 'whatsapp_lookup', csrf.exempt(whatsapp_lookup), methods=['POST'])
app_routes_bp.add_url_rule('/api/phone/2chat', 'check_whatsapp_2chat', csrf.exempt(check_whatsapp_2chat), methods=['POST'])
app_routes_bp.add_url_rule('/api/telegram', 'telegram_lookup', csrf.exempt(telegram_lookup), methods=['POST'])
app_routes_bp.add_url_rule('/api/carrier', 'carrier_lookup', csrf.exempt(carrier_lookup), methods=['POST'])
app_routes_bp.add_url_rule('/api/phone-lookup', 'phone_lookup_all', csrf.exempt(phone_lookup_all), methods=['POST'])
