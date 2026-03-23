import os
import re
import socket
import json
import uuid
import asyncio
import httpx
import requests
from datetime import datetime
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

app = Flask(__name__)

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
            print(f"Loaded Maigret database with {len(maigret_db.sites)} sites")
        except Exception as e:
            print(f"Failed to load Maigret database: {e}")
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
        print(f"Failed to fetch Sherlock sites: {e}")
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
    result = {
        'email': email,
        'valid_format': validate_email(email),
        'provider': email.split('@')[1] if '@' in email else None,
        'mx_records': None,
        'disposable': False,
        'account_checks': [],
        'search_links': []
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
    
    batch_size = 50
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        site_items = list(email_sites.items())
        
        for i in range(0, total_sites, batch_size):
            batch = site_items[i:i + batch_size]
            tasks = []
            for site_name, site_info in batch:
                tasks.append(check_email_site(client, site_name, site_info, email))
            
            for site_name, site_info, task in zip([s[0] for s in batch], [s[1] for s in batch], tasks):
                try:
                    r = await asyncio.wait_for(task, timeout=10)
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
    
    result['search_links'] = [
        {'name': 'Hunter.io', 'url': f'https://hunter.io/search/{email}'},
        {'name': 'EmailRep', 'url': f'https://emailrep.io/{email}'},
        {'name': 'Have I Been Pwned', 'url': f'https://haveibeenpwned.com/unverifiedpwned?q={email}'},
        {'name': 'Google', 'url': f'https://www.google.com/search?q="{email}"'},
        {'name': 'Dehashed', 'url': f'https://dehashed.com/search?query={email}'},
    ]
    
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
        elif info['type'] == 'api':
            response = await client.get(url, timeout=5, headers=HEADERS)
            finding['exists'] = response.status_code == 200
        else:
            response = await client.head(url, timeout=3, allow_redirects=True, headers=HEADERS)
            finding['exists'] = response.status_code != 404
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
            else:
                finding['status'] = 'found'
                finding['exists'] = True
        elif 'success' in site_info:
            if site_info['success'] in response_text:
                finding['status'] = 'found'
                finding['exists'] = True
            else:
                finding['status'] = 'not_found'
                finding['exists'] = False
        else:
            if response.status_code == 200:
                if 'username' in site_info or site_info.get('checkType') == 'status':
                    finding['exists'] = True
                    finding['status'] = 'found'
                else:
                    finding['exists'] = True
                    finding['status'] = 'found'
            elif response.status_code == 404:
                finding['exists'] = False
                finding['status'] = 'not_found'
            else:
                finding['exists'] = response.status_code != 404
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

async def search_username_async(username, progress_callback=None):
    sherlock_sites = get_sherlock_sites()
    
    if not sherlock_sites:
        return {
            'username': username,
            'platforms_checked': 0,
            'findings': [],
            'error': 'Could not load Sherlock site data'
        }
    
    all_findings = []
    total_sites = len(sherlock_sites)
    checked = 0
    found_count = 0
    
    batch_size = 50
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        site_items = list(sherlock_sites.items())
        
        for i in range(0, total_sites, batch_size):
            batch = site_items[i:i + batch_size]
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


def search_username_maigret(username, progress_callback=None):
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
        
        class ProgressNotifier:
            def __init__(self, callback):
                self.callback = callback
                self.checked = 0
                self.total = len(db.sites)
                
            def update(self, checked, total, found=None):
                self.checked = checked
                if self.callback:
                    self.callback({
                        'checked': checked,
                        'total': total,
                        'found': found if found else 0,
                        'percent': int((checked / total) * 100) if total > 0 else 0,
                        'current_site': ''
                    })
        
        notifier = ProgressNotifier(progress_callback) if progress_callback else None
        
        results = asyncio.run(maigret_module.maigret(
            username=username,
            site_dict=db.sites_dict,
            logger=logger,
            query_notify=notifier,
            timeout=5,
            is_parsing_enabled=False,
            max_connections=50,
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
            result = loop.run_until_complete(search_username_async(username, progress_callback))
            found_count = result.get('found_count', 0)
            search_history.add_entry('username', username, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
            loop.close()
    
    sherlock_sites = get_sherlock_sites()
    
    if not sherlock_sites:
        return jsonify({'error': 'Could not load Sherlock site data'}), 400
    
    progress_state['total'] = len(sherlock_sites)
    
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


@app.route('/api/username', methods=['POST'])
def username_search():
    data = request.get_json()
    username = data.get('username', '')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    return jsonify(search_username(username))


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
            
            progress_state['total'] = len(db.sites)
            
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
            
            notifier = ProgressNotifier(progress_callback, len(db.sites))
            
            results = asyncio.run(maigret_module.maigret(
                username=username,
                site_dict=db.sites_dict,
                logger=logger,
                query_notify=notifier,
                timeout=3,
                is_parsing_enabled=False,
                max_connections=30,
                no_progressbar=True
            ))
            
            findings = []
            found_count = 0
            
            for site_name, site_result in results.items():
                exists = site_result.get('exists', False)
                finding = {
                    'site': site_name,
                    'url': site_result.get('url_user') or site_result.get('url_main', ''),
                    'exists': exists,
                    'status': site_result.get('status', 'unknown'),
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
        'presence': ['"name"', '"screen_name"', 'followers_count'],
        'url_absence': ['/suspended', '/nonexistent']
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
        'presence': ['channelId', '"@type":"Person"'],
        'absence': ['This channel does not exist']
    },
    'LinkedIn': {
        'url': 'https://www.linkedin.com/in/{}',
        'category': 'social',
        'presence': ['Public profile', 'LinkedIn Member'],
        'absence': ['The LinkedIn member you are viewing does not exist']
    },
    'Snapchat': {
        'url': 'https://www.snapchat.com/add/{}',
        'category': 'social',
        'presence': ['Snapchat', 'add'],
        'absence': ['couldn\'t find that page']
    },
    'Reddit': {
        'url': 'https://www.reddit.com/user/{}',
        'category': 'social',
        'presence': ['"name"', '"created_utc"'],
        'url_absence': ['/login/']
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
        'presence': ['posts', 'tumblr'],
        'absence': ['there\'s nothing here']
    },
    'Twitch': {
        'url': 'https://www.twitch.tv/{}',
        'category': 'gaming',
        'presence': ['data-name', 'channel-header__user-name'],
        'absence': ['was not found', 'channel doesn\'t exist']
    },
    'Discord': {
        'url': 'https://discord.com/users/{}',
        'category': 'messaging',
        'presence': ['profileCard', 'user-tag'],
        'absence': ['unknown member']
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
        'presence': ['data-testid', 'entity-info'],
        'absence': ['couldn\'t find that page']
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
        'presence': ['tgme_page', 'message'],
        'absence': ['Please check the username']
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


async def check_social_platform(client, platform_name, platform_info, query):
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
        response = await client.get(url, headers=HEADERS, timeout=5, follow_redirects=True)
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


async def search_social_async(query, search_type='username', progress_callback=None, platforms=None):
    if platforms is None:
        platforms = SOCIAL_MEDIA_PLATFORMS
    
    result = {
        'query': query,
        'search_type': search_type,
        'platforms': [],
        'found': [],
        'not_found': []
    }
    
    all_results = []
    total_platforms = len(platforms)
    checked = 0
    
    batch_size = 20
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=20, cookies={'wd': '1920x1080'}) as client:
        platform_items = list(platforms.items())
        
        for i in range(0, total_platforms, batch_size):
            batch = platform_items[i:i + batch_size]
            tasks = []
            for platform_name, platform_info in batch:
                tasks.append(check_social_platform(client, platform_name, platform_info, query))
            
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
    
    if not query:
        return jsonify({'error': 'Query required'}), 400
    
    platforms = get_platforms_by_category(category)
    
    result_queue = queue.Queue()
    progress_state = {'checked': 0, 'found': 0, 'current_site': '', 'total': 0}
    
    def progress_callback(progress):
        progress_state.update(progress)
    
    def run_search_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(search_social_async(query, search_type, progress_callback, platforms))
            found_count = result.get('found_count', 0)
            search_history.add_entry('social', query, f'{found_count} accounts found', found_count)
            result_queue.put(('complete', result))
        except Exception as e:
            result_queue.put(('error', str(e)))
        finally:
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


if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
