import logging
import re
import copy
import base64
import socket
import os
import json
import time
import requests as http_requests
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import request, jsonify, current_app, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Subject, Finding, Setting, AuditLog, Address, Case, Comment
from ..auth import roles_required
from ..encryption_utils import encryptor
from ..validation import validate, EmailCheckSchema, KadasterLookupSchema, RDWCheckSchema, PhoneLookupSchema, PolitiebureauLookupSchema, RDWUpdateSchema, VesselLookupSchema, VesselUpdateSubjectSchema, VesselFindingSchema, CheckPolicieDataSchema, InterpolFindingSchema

logger = logging.getLogger(__name__)


@cms_bp.route('/api/phone-lookup-stored', methods=['GET'])
@login_required
def phone_lookup_stored():
    """Return the most recent stored lookup for a phone number."""
    phone = (request.args.get('phone') or '').strip()
    if not phone:
        return jsonify({'found': False})
    try:
        import phonenumbers
        parsed = phonenumbers.parse(phone, 'NL')
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        normalized = re.sub(r'[^0-9]', '', e164)
    except Exception:
        normalized = re.sub(r'[^0-9]', '', phone)
    from ..models import PhoneLookup
    lookup = PhoneLookup.query.filter(
        PhoneLookup.phone == normalized,
        PhoneLookup.raw_response.isnot(None)
    ).order_by(PhoneLookup.created_at.desc()).first()
    if not lookup:
        return jsonify({'found': False})
    return jsonify({
        'found': True,
        'lookup_id': lookup.id,
        'created_at': lookup.created_at.isoformat() if lookup.created_at else None,
        'raw_response': lookup.raw_response,
        'profile_picture': lookup.profile_picture,
        'created_by_name': lookup.creator.full_name if lookup.creator else None,
    })


@cms_bp.route('/api/phone-lookup', methods=['POST'])
@login_required
@validate(PhoneLookupSchema)
def phone_lookup():
    phone = request.validated_data['phone'].strip()

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
        'normalized': None,
        'services': {},
        'nl_info': None
    }

    try:
        import httpx
        import phonenumbers
        from phonenumbers import geocoder, carrier, timezone as pn_tz

        parsed = phonenumbers.parse(phone, 'NL')
        result['valid'] = phonenumbers.is_valid_number(parsed)
        result['formatted'] = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164)
        result['country_code'] = f"+{parsed.country_code}"

        try:
            result['country'] = geocoder.description_for_number(parsed, 'en')
        except Exception:
            pass

        try:
            result['region'] = geocoder.description_for_number(parsed, 'nl')
        except Exception:
            pass

        try:
            result['carrier'] = carrier.name_for_number(parsed, 'nl')
        except Exception:
            pass

        try:
            ntype = phonenumbers.number_type(parsed)
            line_map = {
                phonenumbers.PhoneNumberType.MOBILE: 'Mobile',
                phonenumbers.PhoneNumberType.FIXED_LINE: 'Fixed Line',
                phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: 'Fixed Line or Mobile',
                phonenumbers.PhoneNumberType.PAGER: 'Pager',
                phonenumbers.PhoneNumberType.PERSONAL_NUMBER: 'Personal Number',
                phonenumbers.PhoneNumberType.PREMIUM_RATE: 'Premium Rate',
                phonenumbers.PhoneNumberType.SHARED_COST: 'Shared Cost',
                phonenumbers.PhoneNumberType.TOLL_FREE: 'Toll Free',
                phonenumbers.PhoneNumberType.UAN: 'UAN',
                phonenumbers.PhoneNumberType.VOIP: 'VoIP',
            }
            result['line_type'] = line_map.get(ntype, str(ntype))
        except Exception:
            pass

        try:
            tz = pn_tz.time_zones_for_number(parsed)
            result['timezone'] = tz[0] if tz else None
        except Exception:
            pass

        normalized = re.sub(r'[^0-9]', '', result['formatted'])
        result['normalized'] = normalized

        api_key = Setting.get('whatsapp_checkleaked_key')
        if api_key:
            now_month = datetime.now(timezone.utc).strftime('%Y-%m')
            stored_month = Setting.get('whatsapp_checkleaked_month')
            used_count = int(Setting.get('whatsapp_checkleaked_used') or '0') if stored_month == now_month else 0
            limit = 50
            result['api_usage'] = {
                'used': used_count,
                'limit': limit,
                'remaining': max(0, limit - used_count),
            }
            if used_count >= limit:
                result['api_usage']['note'] = 'Maandlimiet bereikt, gebruik fallback'
            else:
                try:
                    cl_url = f'https://whatsapp-data1.p.rapidapi.com/number/{normalized}?telegram=1'
                    cl_headers = {
                        'x-rapidapi-key': api_key,
                        'x-rapidapi-host': 'whatsapp-data1.p.rapidapi.com',
                    }
                    cl_resp = httpx.get(cl_url, headers=cl_headers, timeout=15)
                    cl_data = cl_resp.json()
                    if 'isWAContact' in cl_data or 'isUser' in cl_data:
                        wa_exists = cl_data.get('isWAContact') or cl_data.get('isUser')
                        tg = cl_data.get('telegram')
                        tg_exists = not ('not on Telegram' in ((tg or {}).get('error') or '')) if tg else None

                        profile_pic_b64 = None
                        pic_status = cl_data.get('image_status')
                        if pic_status and pic_status != 'item-not-found' and pic_status != 'not-authorized':
                            try:
                                pic_url = f'https://whatsapp-data1.p.rapidapi.com/picture/{normalized}'
                                pic_resp = httpx.get(pic_url, headers=cl_headers, timeout=10)
                                if pic_resp.status_code == 200 and pic_resp.headers.get('content-type', '').startswith('image/'):
                                    profile_pic_b64 = 'data:' + pic_resp.headers['content-type'] + ';base64,' + base64.b64encode(pic_resp.content).decode()
                            except Exception as e:
                                logger.debug(f"Phone lookup profile picture fetch failed ({type(e).__name__}): {e}")

                        result['services']['whatsapp'] = {
                            'exists': bool(wa_exists),
                            'url': f'https://wa.me/{normalized}' if wa_exists else None,
                            'business': cl_data.get('isBusiness'),
                            'enterprise': cl_data.get('isEnterprise'),
                            'verified': cl_data.get('isVerified'),
                            'about': cl_data.get('about'),
                            'about_set_at': cl_data.get('aboutSetAt'),
                            'line_type': cl_data.get('type'),
                            'cached': cl_data.get('cached'),
                            'check_date': cl_data.get('date'),
                            'banned': cl_data.get('checkMetadata', {}).get('isBanned'),
                            'image_status': pic_status,
                            'profile_picture': profile_pic_b64,
                        }
                        result['services']['telegram'] = {
                            'exists': tg_exists if tg_exists is not None else None,
                            'url': f'https://t.me/+{normalized}' if tg_exists else None,
                            'error': (tg or {}).get('error') if tg else None,
                        }
                        result['raw_api_data'] = cl_data

                        from ..models import PhoneLookup
                        stored_result = copy.deepcopy(result)
                        lookup = PhoneLookup(
                            phone=normalized,
                            raw_response=stored_result,
                            profile_picture=profile_pic_b64,
                            created_by=current_user.id if current_user.is_authenticated else None,
                        )
                        db.session.add(lookup)
                        db.session.commit()

                        Setting.set('whatsapp_checkleaked_month', now_month)
                        Setting.set('whatsapp_checkleaked_used', str(used_count + 1))
                        result['api_usage']['used'] = used_count + 1
                        result['api_usage']['remaining'] = max(0, limit - used_count - 1)
                        result['lookup_id'] = lookup.id
                    else:
                        raise Exception(f'API returned {cl_resp.status_code}: {cl_data.get("error","no data")}')
                except Exception as e:
                    logger.debug(f"Phone lookup RapidAPI (whatsapp.checkleaked.cc) failed ({type(e).__name__}): {e}")

        if 'whatsapp' not in result['services'] or result['services']['whatsapp'].get('exists') is None:
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                try:
                    wa_url = f'https://api.whatsapp.com/send?phone={normalized}'
                    wa_resp = client.get(
                        wa_url, headers={'User-Agent': 'Mozilla/5.0'})
                    wa_text = wa_resp.text.lower()
                    if 'phone number is not on whatsapp' in wa_text:
                        result['services']['whatsapp'] = {'exists': False}
                    else:
                        result['services']['whatsapp'] = {
                            'exists': True, 'url': f'https://wa.me/{normalized}'}
                except Exception as e:
                    logger.debug(f"Phone lookup WhatsApp fallback scrape failed ({type(e).__name__}): {e}")
                    result['services']['whatsapp'] = {
                        'exists': None, 'note': 'Check failed'}

        if 'telegram' not in result['services'] or result['services']['telegram'].get('exists') is None:
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                try:
                    tg_url = f'https://t.me/+{normalized}'
                    tg_resp = client.get(
                        tg_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
                    tg_text = tg_resp.text.lower()
                    if tg_resp.status_code == 400 or 'join' in tg_text or 'subscribe' in tg_text:
                        result['services']['telegram'] = {
                            'exists': True, 'url': tg_url}
                    elif tg_resp.status_code == 200:
                        result['services']['telegram'] = {'exists': False}
                    else:
                        result['services']['telegram'] = {
                            'exists': None, 'note': 'Unable to verify'}
                except Exception as e:
                    logger.debug(f"Phone lookup Telegram fallback scrape failed ({type(e).__name__}): {e}")
                    result['services']['telegram'] = {
                        'exists': None, 'note': 'Check failed'}

        if result['country_code'] == '+31':
            try:
                bd_url = 'https://free.bedrijfsdata.nl/v1.1/phone'
                bd_params = {'country_code': 'nl',
                             'phone': phone.lstrip('+').lstrip('00')}
                bd_resp = httpx.get(bd_url, params=bd_params, timeout=10)
                if bd_resp.status_code == 200:
                    bd_data = bd_resp.json().get('phone', {})
                    result['nl_info'] = {
                        'valid': bd_data.get('valid') == 1,
                        'region': bd_data.get('region'),
                        'carrier': bd_data.get('carrier'),
                        'is_mobile': bd_data.get('ismobile') == 1
                    }
                    if bd_data.get('region') and not result.get('region'):
                        result['region'] = bd_data['region']
                    if bd_data.get('carrier') and not result.get('carrier'):
                        result['carrier'] = bd_data['carrier']
            except Exception as e:
                logger.debug(f"Phone lookup bedrijfsdata.nl failed ({type(e).__name__}): {e}")

        logger.info(
            f"Phone lookup: {phone} → valid={result['valid']}, carrier={result['carrier']}, region={result['region']}, wa={result['services'].get('whatsapp', {}).get('exists')}")
        return jsonify(result), 200

    except ImportError:
        return jsonify({'error': 'phonenumbers library not installed'}), 500
    except Exception as e:
        logger.error(f"Phone lookup error ({type(e).__name__}): {e}")
        return jsonify({'error': f'Phone lookup failed: {str(e)}'}), 500


@cms_bp.route('/api/email-check', methods=['POST'])
@login_required
@validate(EmailCheckSchema)
def email_check():
    """Validate an email address and check for known breaches."""
    email = request.validated_data['email'].strip().lower()

    import httpx

    result = {
        'email': email,
        'valid_format': False,
        'domain': None,
        'has_mx': False,
        'disposable': False,
        'hibp_found': False,
        'hibp_breaches': [],
        'emailrep': None,
        'search_links': [],
        'error': None
    }

    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        result['error'] = 'Invalid email format'
        return jsonify(result), 200

    result['valid_format'] = True
    domain = email.split('@')[1]
    result['domain'] = domain

    disposable_domains = {
        'mailinator.com', 'guerrillamail.com', 'tempmail.com', 'throwaway.email',
        'yopmail.com', 'sharklasers.com', 'trashmail.com', '10minutemail.com',
        'mailnator.com', 'temp-mail.org', 'getairmail.com', 'tempinbox.com',
        'spamgourmet.com', 'mailexpire.com', 'maildrop.cc', 'burnermail.io',
        'inboxbear.com', 'discard.email', 'mintemail.com', 'mailforspam.com',
    }
    if domain.lower() in disposable_domains:
        result['disposable'] = True

    try:
        socket.getaddrinfo(domain, 25)
        result['has_mx'] = True
    except socket.gaierror:
        result['has_mx'] = False

    hibp_key = os.environ.get('HIBP_API_KEY', '')
    if hibp_key:
        try:
            resp = httpx.get(
                f'https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=false',
                headers={'hibp-api-key': hibp_key, 'User-Agent': 'Iveras-OSINT-Dashboard/3.0'},
                timeout=15
            )
            if resp.status_code == 200:
                breaches = resp.json()
                result['hibp_found'] = True
                result['hibp_breaches'] = [{
                    'name': b.get('Name'), 'domain': b.get('Domain'),
                    'date': b.get('BreachDate'), 'data_classes': b.get('DataClasses', []),
                    'description': b.get('Description', '')[:200],
                } for b in breaches]
            elif resp.status_code == 404:
                result['hibp_found'] = False
            elif resp.status_code == 401:
                result['hibp_found'] = False
                logger.warning("HIBP API key rejected")
        except Exception as e:
            logger.warning(f"HIBP lookup failed ({type(e).__name__}): {e}")

    try:
        eresp = httpx.get(
            f'https://emailrep.io/{email}',
            headers={'User-Agent': 'Iveras-OSINT-Dashboard/3.0'},
            timeout=10
        )
        if eresp.status_code == 200:
            result['emailrep'] = eresp.json()
    except Exception as e:
        logger.warning(f"EmailRep lookup failed ({type(e).__name__}): {e}")

    result['search_links'] = [
        {'label': 'Have I Been Pwned', 'url': f'https://haveibeenpwned.com/account/{email}'},
        {'label': 'EmailRep', 'url': f'https://emailrep.io/{email}'},
        {'label': 'Hunter.io', 'url': f'https://hunter.io/search/{domain}'},
        {'label': 'Dehashed', 'url': f'https://dehashed.com/search?query={email}'},
        {'label': 'Google', 'url': f'https://www.google.com/search?q={email}'},
    ]

    logger.info(f"Email check: {email} -> valid={result['valid_format']}, mx={result['has_mx']}, hibp={result['hibp_found']}")
    return jsonify(result), 200


@cms_bp.route('/api/kadaster-lookup', methods=['POST'])
@login_required
@validate(KadasterLookupSchema)
def kadaster_lookup():
    """Look up a Dutch address in the BAG via PDOK API."""
    data = request.validated_data

    query = data.get('query', '')
    if not query:
        parts = []
        if data.get('street'): parts.append(data['street'])
        if data.get('number'): parts.append(data['number'])
        if data.get('zipcode'): parts.append(data['zipcode'])
        if data.get('town'): parts.append(data['town'])
        query = ' '.join(parts)

    if not query:
        return jsonify({'error': 'No address provided'}), 400

    try:
        import httpx
        pdok_url = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
        params = {'q': query, 'rows': 1, 'fl': '*'}

        resp = httpx.get(pdok_url, params=params, timeout=10)
        resp.raise_for_status()
        result = resp.json()

        docs = result.get('response', {}).get('docs', [])
        if not docs:
            logger.warning(f"Kadaster lookup not found: {query}")
            return jsonify({'found': False, 'message': 'Address not found in BAG registry', 'query': query}), 200

        doc = docs[0]
        logger.info(f"Kadaster lookup OK: {query} -> {doc.get('straatnaam')} {doc.get('huisnummer')}, {doc.get('postcode')} {doc.get('woonplaatsnaam')}")
        return jsonify({
            'found': True, 'query': query,
            'bag_data': {
                'street': doc.get('straatnaam'), 'number': doc.get('huisnummer'),
                'number_letter': doc.get('huisletter'), 'number_addition': doc.get('huisnummertoevoeging'),
                'zipcode': doc.get('postcode'), 'town': doc.get('woonplaatsnaam'),
                'municipality': doc.get('gemeentenaam'), 'province': doc.get('provincienaam'),
                'coordinates': doc.get('centroide_ll'), 'purpose': doc.get('gebruiksdoel'),
                'surface': doc.get('oppervlakte'), 'building_year': doc.get('bouwjaar'),
                'bag_id': doc.get('bag_id'), 'status': doc.get('status'), 'type': doc.get('type')
            }
        }), 200
    except httpx.RequestError as e:
        return jsonify({'error': f'Failed to lookup address: {str(e)}'}), 502
    except Exception as e:
        logger.error(f"Kadaster lookup unexpected error ({type(e).__name__}): {e}")
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


@cms_bp.route('/api/politiebureau-lookup', methods=['POST'])
@login_required
def politiebureau_lookup():
    """Look up nearest police station for an address."""
    data = request.get_json() if request.is_json else request.form

    lat = lon = None
    address_info = {}

    address_id = data.get('address_id')
    if address_id:
        addr = db.session.get(Address, address_id)
        if not addr:
            return jsonify({'error': 'Address not found'}), 404
        addr.decrypt_fields()
        address_info = {
            'street': addr.street, 'number': addr.number,
            'zipcode': addr.zipcode, 'town': addr.town, 'country': addr.country
        }

        if addr.kadaster_data:
            coords_str = addr.kadaster_data.get('coordinates')
            if coords_str and 'POINT(' in coords_str:
                c = coords_str.replace('POINT(', '').replace(')', '').strip().split(' ')
                if len(c) == 2:
                    lon, lat = float(c[0]), float(c[1])
            if not lat and addr.kadaster_data.get('lat') and addr.kadaster_data.get('lon'):
                lat = float(addr.kadaster_data['lat'])
                lon = float(addr.kadaster_data['lon'])

        if not lat or not lon:
            query = ' '.join(filter(None, [addr.street, addr.number, addr.zipcode, addr.town]))
            if query:
                try:
                    import httpx
                    pdok_url = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/free'
                    r = httpx.get(pdok_url, params={'q': query, 'rows': 1, 'fl': '*'}, timeout=10)
                    r.raise_for_status()
                    docs = r.json().get('response', {}).get('docs', [])
                    if docs:
                        cs = docs[0].get('centroide_ll')
                        if cs and 'POINT(' in cs:
                            c = cs.replace('POINT(', '').replace(')', '').strip().split(' ')
                            if len(c) == 2:
                                lon, lat = float(c[0]), float(c[1])
                except Exception as e:
                    logger.debug(f"Politiebureau lookup geocode (from address_id) failed ({type(e).__name__}): {e}")

    if not lat or not lon:
        lat = data.get('lat')
        lon = data.get('lon')

    if not lat or not lon:
        query = data.get('query') or ''
        if query:
            try:
                import httpx
                pdok_url = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/free'
                r = httpx.get(pdok_url, params={'q': query, 'rows': 1, 'fl': '*'}, timeout=10)
                r.raise_for_status()
                docs = r.json().get('response', {}).get('docs', [])
                if docs and docs[0].get('centroide_ll'):
                    cs = docs[0]['centroide_ll']
                    if 'POINT(' in cs:
                        c = cs.replace('POINT(', '').replace(')', '').strip().split(' ')
                        if len(c) == 2:
                            lon, lat = float(c[0]), float(c[1])
            except Exception as e:
                logger.debug(f"Politiebureau lookup geocode (from query) failed ({type(e).__name__}): {e}")

    if not lat or not lon:
        return jsonify({'error': 'Could not determine coordinates for this address'}), 400

    try:
        import httpx
        r = httpx.get('https://api.politie.nl/politiebureaus/v1', params={'lat': lat, 'lon': lon}, timeout=10)
        r.raise_for_status()
        result = r.json()
        stations = result.get('politiebureaus', [])
        if not stations:
            return jsonify({'found': False, 'message': 'Geen politiebureaus gevonden in de buurt'}), 200

        s = stations[0]
        addr_bezoek = s.get('bezoekadres', {})
        station_addr = None
        if addr_bezoek.get('adres'):
            station_addr = f"{addr_bezoek['adres']}, {addr_bezoek.get('postcode', '')} {addr_bezoek.get('plaats', '')}"

        return jsonify({
            'found': True,
            'station': {
                'name': s.get('naam'), 'address': station_addr, 'phone': s.get('telefoonnummer'),
                'opening_hours': s.get('openingstijden'), 'url': s.get('url'),
                'location': s.get('locaties', [{}])[0] if s.get('locaties') else None
            },
            'address': address_info, 'coordinates': {'lat': lat, 'lon': lon}
        }), 200
    except httpx.RequestError as e:
        return jsonify({'error': f'Failed to lookup police station: {str(e)}'}), 502
    except Exception as e:
        logger.error(f"Politiebureau lookup error: {e}")
        return jsonify({'error': str(e)}), 500


# ── RDW helpers ──────────────────────────────────────────────────────────────────

RDW_API_BASE = 'https://opendata.rdw.nl/resource/m9d7-ebf2.json'


def _normalize_kenteken(kenteken: str) -> str:
    return kenteken.upper().replace('-', '').replace(' ', '')


def _denormalize_kenteken(kenteken: str) -> str:
    kenteken = kenteken.upper().replace('-', '').replace(' ', '')
    if len(kenteken) == 6:
        return f"{kenteken[:2]}-{kenteken[2:5]}-{kenteken[5:]}"
    elif len(kenteken) == 5:
        return f"{kenteken[:2]}-{kenteken[2:4]}-{kenteken[4:]}"
    return kenteken


@cms_bp.route('/check-rdw-vehicle', methods=['POST'])
@login_required
@validate(RDWCheckSchema)
def check_rdw_vehicle():
    """Check vehicle data from RDW (Dutch Road Transport Authority)."""
    data = request.validated_data
    kenteken = data['kenteken'].strip()
    subject_id = data.get('subject_id')

    if not kenteken:
        return jsonify({'error': 'Kenteken (license plate) is required'}), 400

    kenteken_normalized = _normalize_kenteken(kenteken)

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; OSINT-CMS/1.0)', 'Accept': 'application/json'}
        url = f'{RDW_API_BASE}?kenteken={kenteken_normalized}'
        r = http_requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            return jsonify({'error': f'RDW API returned status {r.status_code}', 'kenteken': kenteken_normalized}), 502

        results = r.json()
        if not results:
            return jsonify({
                'found': False, 'kenteken': kenteken_normalized,
                'kenteken_display': _denormalize_kenteken(kenteken_normalized),
                'message': 'No vehicle found for this license plate'
            }), 200

        vehicle = results[0]
        vehicle_data = {
            'found': True,
            'kenteken': vehicle.get('kenteken', ''),
            'kenteken_display': _denormalize_kenteken(vehicle.get('kenteken', '')),
            'voertuigsoort': vehicle.get('voertuigsoort', ''),
            'merk': vehicle.get('merk', ''),
            'handelsbenaming': vehicle.get('handelsbenaming', ''),
            'inrichting': vehicle.get('inrichting', ''),
            'type': vehicle.get('type', ''),
            'variant': vehicle.get('variant', ''),
            'uitvoering': vehicle.get('uitvoering', ''),
            'kleur': vehicle.get('eerste_kleur', ''),
            'tweede_kleur': vehicle.get('tweede_kleur', ''),
            'aantal_deuren': vehicle.get('aantal_deuren', ''),
            'aantal_zitplaatsen': vehicle.get('aantal_zitplaatsen', ''),
            'cilinderinhoud': vehicle.get('cilinderinhoud', ''),
            'aantal_cilinders': vehicle.get('aantal_cilinders', ''),
            'vermogen': vehicle.get('vermogen_massarijklaar', ''),
            'massa_ledig': vehicle.get('massa_ledig_voertuig', ''),
            'maximum_massa': vehicle.get('toegestane_maximum_massa_voertuig', ''),
            'wielbasis': vehicle.get('wielbasis', ''),
            'datum_eerste_toelating': vehicle.get('datum_eerste_toelating', ''),
            'datum_tenaamstelling': vehicle.get('datum_tenaamstelling', ''),
            'vervaldatum_apk': vehicle.get('vervaldatum_apk', ''),
            'europese_voertuigcategorie': vehicle.get('europese_voertuigcategorie', ''),
            'wam_verzekerd': vehicle.get('wam_verzekerd', ''),
            'taxi_indicator': vehicle.get('taxi_indicator', ''),
            'export_indicator': vehicle.get('export_indicator', ''),
            'zuinigheidsclassificatie': vehicle.get('zuinigheidsclassificatie', ''),
            'catalogusprijs': vehicle.get('catalogusprijs', ''),
            'bruto_bpm': vehicle.get('bruto_bpm', ''),
            'openstaande_terugroepactie': vehicle.get('openstaande_terugroepactie_indicator', ''),
            'typegoedkeuringsnummer': vehicle.get('typegoedkeuringsnummer', ''),
        }

        if subject_id:
            vehicle_data['subject_id'] = subject_id
            vehicle_data['suggested_update'] = {
                'brand': vehicle.get('merk', ''),
                'vehicle_type': vehicle.get('inrichting', ''),
                'notes': f"RDW Data: {vehicle.get('merk', '')} {vehicle.get('handelsbenaming', '')} ({_denormalize_kenteken(vehicle.get('kenteken', ''))})"
            }

        return jsonify(vehicle_data), 200
    except http_requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to connect to RDW API: {str(e)}'}), 503
    except Exception as e:
        logger.error(f"RDW check error: {e}")
        return jsonify({'error': f'Failed to check RDW data: {str(e)}'}), 500


@cms_bp.route('/subjects/<subject_id>/update-from-rdw', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
@validate(RDWUpdateSchema)
def update_subject_from_rdw(subject_id: str):
    """Update vehicle subject fields with data from RDW."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    if subject.subject_type != 'vehicle':
        return jsonify({'error': 'Subject is not a vehicle'}), 400

    data = request.validated_data

    kenteken = _normalize_kenteken(data.get('kenteken'))

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; OSINT-CMS/1.0)', 'Accept': 'application/json'}
        url = f'{RDW_API_BASE}?kenteken={kenteken}'
        r = http_requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200 or not r.json():
            return jsonify({'error': 'Vehicle not found in RDW database'}), 404

        vehicle = r.json()[0]

        if vehicle.get('merk'):
            subject.brand = vehicle.get('merk')
        if vehicle.get('inrichting'):
            subject.vehicle_type = vehicle.get('inrichting')

        rdw_notes = [f"Kenteken: {_denormalize_kenteken(kenteken)}"]
        if vehicle.get('merk'): rdw_notes.append(f"Merk: {vehicle.get('merk')}")
        if vehicle.get('handelsbenaming'): rdw_notes.append(f"Model: {vehicle.get('handelsbenaming')}")
        if vehicle.get('voertuigsoort'): rdw_notes.append(f"Type: {vehicle.get('voertuigsoort')}")
        if vehicle.get('inrichting'): rdw_notes.append(f"Inrichting: {vehicle.get('inrichting')}")
        if vehicle.get('kleur'): rdw_notes.append(f"Kleur: {vehicle.get('eerste_kleur')}")
        if vehicle.get('vervaldatum_apk'): rdw_notes.append(f"APK vervaldatum: {vehicle.get('vervaldatum_apk')}")
        if vehicle.get('wam_verzekerd'): rdw_notes.append(f"Verzekerd (WAM): {vehicle.get('wam_verzekerd')}")

        rdw_comment = Comment(
            subject_id=subject.id,
            content='[RDW Data]\n' + '\n'.join(rdw_notes),
            comment_type='note',
            author_id=current_user.id
        )
        db.session.add(rdw_comment)

        AuditLog.log(
            user_id=current_user.id, action='update', entity_type='subject',
            entity_id=subject_id, ip_address=request.remote_addr,
            description=f"Updated vehicle data from RDW for: {_denormalize_kenteken(kenteken)}"
        )
        db.session.commit()

        return jsonify({'message': 'Subject updated from RDW data', 'subject': subject.to_dict()}), 200
    except Exception as e:
        logger.error(f"RDW update error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ── Vessel Lookup ────────────────────────────────────────────────────────────────

from ..vessel_service import lookup_vessel
VESSEL_SERVICE_AVAILABLE = True


@cms_bp.route('/api/vessel-lookup', methods=['POST'])
@login_required
@validate(VesselLookupSchema)
def vessel_lookup():
    """Look up vessel data from MarinePlan, KVNR, Binnenvaart.eu, Equasis."""
    if not VESSEL_SERVICE_AVAILABLE:
        return jsonify({'error': 'Vessel service not available'}), 503

    try:
        data = request.validated_data

        name = (data.get('name') or '').strip()
        imo = (data.get('imo') or '').strip()
        mmsi = (data.get('mmsi') or '').strip()
        eni = (data.get('eni') or '').strip()

        if not name and not imo and not mmsi and not eni:
            return jsonify({'error': 'Provide at least name, IMO, MMSI, or ENI'}), 400

        result = lookup_vessel(imo=imo or None, mmsi=mmsi or None, eni=eni or None, name=name or None)

        subject_id = data.get('subject_id')
        if subject_id and result.get('found'):
            subject = db.session.get(Subject, subject_id)
            if subject:
                result['suggested_update'] = {
                    'imo_number': result.get('imo'), 'mmsi': result.get('mmsi'),
                    'eni_number': result.get('eni'), 'vessel_nationality': result.get('flag'),
                    'vessel_data': result.get('source_data')
                }

        return jsonify(result), 200
    except Exception as e:
        logger.exception(f"Vessel lookup error ({type(e).__name__}): {e}")
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/api/vessel/update-subject', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
@validate(VesselUpdateSubjectSchema)
def update_subject_from_vessel():
    """Update subject with vessel data from lookup."""
    data = request.validated_data

    subject = db.session.get(Subject, data['subject_id']) or abort(404)
    if subject.subject_type != 'vessel':
        return jsonify({'error': 'Subject is not a vessel'}), 400

    changes = {}
    vessel_fields = ['imo_number', 'mmsi', 'eni_number', 'vessel_nationality']
    for field in vessel_fields:
        if data.get(field):
            setattr(subject, field, encryptor.encrypt(str(data[field])))
            changes[field] = {'old': 'updated', 'new': str(data[field])}

    if data.get('vessel_data'):
        vd = data['vessel_data']
        if isinstance(vd, str):
            try:
                vd = json.loads(vd)
            except json.JSONDecodeError:
                pass
        subject.vessel_data = vd if isinstance(vd, dict) else {}
        changes['vessel_data'] = {'old': 'updated', 'new': 'Vessel data updated'}

    subject.updated_at = datetime.now(timezone.utc)

    AuditLog.log(
        user_id=current_user.id, action='update', entity_type='subject',
        entity_id=subject.id, changes=changes, ip_address=request.remote_addr,
        description=f"Updated vessel subject: {subject.name}"
    )
    db.session.commit()

    return jsonify({'message': 'Vessel subject updated', 'subject': subject.to_dict()}), 200


@cms_bp.route('/api/findings/from-vessel', methods=['POST'])
@login_required
@validate(VesselFindingSchema)
def create_finding_from_vessel():
    """Create a Finding from vessel lookup data."""
    data = request.validated_data
    case_id = data.get('case_id')
    subject_id = data.get('subject_id')

    if not case_id:
        return jsonify({'error': 'case_id is required'}), 400

    vessel_info = data.get('vessel_data', {})
    source = data.get('source', 'vessel_lookup')

    if not vessel_info or not isinstance(vessel_info, dict):
        return jsonify({'error': 'vessel_data is required'}), 400

    content_parts = ['Vessel Lookup Results', '=' * 30]
    name = vessel_info.get('name') or 'Unknown'
    content_parts.append(f"Name: {name}")
    content_parts.append(f"IMO: {vessel_info.get('imo', 'N/A')}")
    content_parts.append(f"MMSI: {vessel_info.get('mmsi', 'N/A')}")
    content_parts.append(f"ENI: {vessel_info.get('eni', 'N/A')}")
    content_parts.append(f"Flag: {vessel_info.get('flag', 'N/A')}")
    content_parts.append(f"Ship Type: {vessel_info.get('ship_type', 'N/A')}")
    content_parts.append(f"Length: {vessel_info.get('length', 'N/A')}")
    content_parts.append(f"Beam: {vessel_info.get('beam', 'N/A')}")
    content_parts.append(f"Year Built: {vessel_info.get('year_built', 'N/A')}")
    content_parts.append(f"Callsign: {vessel_info.get('callsign', 'N/A')}")
    content_parts.append(f"Destination: {vessel_info.get('destination', 'N/A')}")

    pos = vessel_info.get('position')
    if pos:
        content_parts.append(f"Position: {pos.get('lat', '?')}, {pos.get('lon', '?')}")
    if vessel_info.get('speed'):
        content_parts.append(f"Speed: {vessel_info['speed']} km/h")
    if vessel_info.get('builder'):
        content_parts.append(f"Builder: {vessel_info['builder']}")

    sources = vessel_info.get('sources', [])
    content_parts.append(f"\nSources: {', '.join(sources)}")

    sources_data = vessel_info.get('source_data', {})
    if sources_data.get('vesselfinder'):
        content_parts.append(f"\nVesselFinder: {sources_data['vesselfinder'].get('source_url', '')}")
    if sources_data.get('marineplan'):
        content_parts.append(f"\nMarinePlan: {sources_data['marineplan'].get('source_url', '')}")
    if sources_data.get('kvnr'):
        content_parts.append(f"KVNR: {sources_data['kvnr'].get('source_url', '')}")
    if sources_data.get('binnenvaart'):
        content_parts.append(f"Binnenvaart.eu: {sources_data['binnenvaart'].get('source_url', '')}")
    if sources_data.get('equasis'):
        content_parts.append(f"Equasis: {sources_data['equasis'].get('source_url', '')}")

    tags = ['vessel', source]
    if vessel_info.get('imo'):
        tags.append(f'imo:{vessel_info["imo"]}')

    finding = Finding(
        case_id=case_id, subject_id=subject_id, title=f"Vessel Check: {name}"[:300],
        content='\n'.join(content_parts), source_url=data.get('source_url', ''),
        source_type=source, finding_type='vessel', reliability_score=6,
        confidence_level='medium', tags=tags, created_by=current_user.id
    )
    db.session.add(finding)

    AuditLog.log(
        user_id=current_user.id, action='create', entity_type='finding',
        entity_id=finding.id, new_values={'title': finding.title, 'source_type': source},
        ip_address=request.remote_addr, case_id=case_id,
        description=f"Created vessel finding: {finding.title}"
    )
    db.session.commit()

    return jsonify({'message': f'Bevinding opgeslagen: {name}', 'finding': finding.to_dict()}), 201


# ── Interpol + Politie ───────────────────────────────────────────────────────────

_last_interpol_call = 0


def _check_interpol_rate_limit():
    global _last_interpol_call
    elapsed = time.time() - _last_interpol_call
    if _last_interpol_call > 0 and elapsed < 60:
        return 60 - elapsed
    _last_interpol_call = time.time()
    return 0


def _interpol_headers():
    return {'User-Agent': 'Mozilla/5.0 (compatible; OSINT-CMS/2.0)', 'Accept': 'application/json'}


@cms_bp.route('/check-policie-data', methods=['POST'])
@login_required
@validate(CheckPolicieDataSchema)
def check_policie_data():
    """Check subject against INTERPOL Red Notices + Yellow Notices + politie.nl."""
    data = request.validated_data

    subject_name = data.get('name', '').strip()
    subject_id = data.get('subject_id')

    if not subject_name:
        return jsonify({'error': 'Subject name is required'}), 400

    name_parts = subject_name.lower().split()
    forename = name_parts[0] if len(name_parts) > 0 else ''
    surname = name_parts[-1] if len(name_parts) > 1 else ''

    results = {
        'subject_name': subject_name, 'subject_id': subject_id,
        'missing_persons': [], 'wanted_persons': [], 'opsporingsberichten': [],
        'api_available': True, 'source': 'interpol', 'error': None
    }

    wait = _check_interpol_rate_limit()
    if wait > 0:
        return jsonify({
            'subject_name': subject_name, 'subject_id': subject_id,
            'missing_persons': [], 'wanted_persons': [], 'api_available': False,
            'source': 'interpol',
            'error': f'Interpol API rate limit: wacht {wait:.0f} seconden voor volgende aanvraag',
            'retry_after': int(wait)
        }), 429

    interpol_403 = False
    try:
        import httpx
        client = httpx.Client(headers=_interpol_headers(), timeout=15)

        try:
            red_params = {'resultPerPage': 10}
            if surname: red_params['name'] = surname
            if forename: red_params['forename'] = forename
            r = client.get('https://ws-public.interpol.int/notices/v1/red', params=red_params)
            if r.status_code == 200:
                red_data = r.json()
                for notice in red_data.get('_embedded', {}).get('notices', []):
                    nid = notice['entity_id'].replace('/', '-')
                    detail = None
                    try:
                        dr = client.get(f'https://ws-public.interpol.int/notices/v1/red/{nid}')
                        if dr.status_code == 200: detail = dr.json()
                    except Exception as e:
                        logger.debug(f"INTERPOL Red Notice detail fetch failed for {nid} ({type(e).__name__}): {e}")
                    charge = ''
                    issuing = ''
                    if detail and detail.get('arrest_warrants'):
                        aw = detail['arrest_warrants'][0]
                        charge = aw.get('charge', '')
                        issuing = aw.get('issuing_country_id', '')
                    results['wanted_persons'].append({
                        'name': f"{notice.get('forename', '')} {notice.get('name', '')}".strip(),
                        'forename': notice.get('forename', ''), 'surname': notice.get('name', ''),
                        'date_of_birth': notice.get('date_of_birth', ''),
                        'nationality': ', '.join(notice.get('nationalities', [])),
                        'charge': charge, 'issuing_country': issuing,
                        'url': notice.get('_links', {}).get('self', {}).get('href', ''),
                        'thumbnail': notice.get('_links', {}).get('thumbnail', {}).get('href', ''),
                        'type': 'Red Notice (Wanted)', 'source': 'INTERPOL'
                    })
            elif r.status_code == 403:
                interpol_403 = True
        except Exception as e:
            logger.warning(f"Interpol Red Notice lookup error: {e}")

        try:
            yellow_params = {'resultPerPage': 10}
            if surname: yellow_params['name'] = surname
            if forename: yellow_params['forename'] = forename
            r = client.get('https://ws-public.interpol.int/notices/v1/yellow', params=yellow_params)
            if r.status_code == 200:
                yellow_data = r.json()
                for notice in yellow_data.get('_embedded', {}).get('notices', []):
                    nid = notice['entity_id'].replace('/', '-')
                    detail = None
                    try:
                        dr = client.get(f'https://ws-public.interpol.int/notices/v1/yellow/{nid}')
                        if dr.status_code == 200: detail = dr.json()
                    except Exception as e:
                        logger.debug(f"INTERPOL Yellow Notice detail fetch failed for {nid} ({type(e).__name__}): {e}")
                    results['missing_persons'].append({
                        'name': f"{notice.get('forename', '')} {notice.get('name', '')}".strip(),
                        'forename': notice.get('forename', ''), 'surname': notice.get('name', ''),
                        'date_of_birth': notice.get('date_of_birth', ''),
                        'nationality': ', '.join(notice.get('nationalities', [])),
                        'date_missing': detail.get('date_of_event', '') if detail else '',
                        'place': detail.get('place', '') if detail else '',
                        'countries_likely_to_visit': ', '.join(detail.get('countries_likely_to_be_visited', [])) if detail else '',
                        'url': notice.get('_links', {}).get('self', {}).get('href', ''),
                        'thumbnail': notice.get('_links', {}).get('thumbnail', {}).get('href', ''),
                        'type': 'Yellow Notice (Missing)', 'source': 'INTERPOL'
                    })
            elif r.status_code == 403:
                interpol_403 = True
        except Exception as e:
            logger.warning(f"Interpol Yellow Notice lookup error: {e}")

        if len(results['wanted_persons']) == 0 and len(results['missing_persons']) == 0 and len(name_parts) >= 1:
            try:
                vermist_resp = httpx.get('https://www.politie.nl/vermist', headers=_interpol_headers(), timeout=10, follow_redirects=True)
                if vermist_resp.status_code == 200:
                    case_links = re.findall(r'href="(/vermist/[^"]+)"', vermist_resp.text)
                    for link in case_links[:20]:
                        try:
                            detail = httpx.get(f'https://www.politie.nl{link}', headers=_interpol_headers(), timeout=10, follow_redirects=True)
                            if detail.status_code == 200:
                                text_lower = detail.text.lower()
                                if any(part in text_lower for part in name_parts):
                                    title_match = re.search(r'<h1[^>]*>([^<]+)</h1>', detail.text)
                                    title = title_match.group(1).strip() if title_match else 'Unknown'
                                    results['missing_persons'].append({
                                        'name': title, 'source': 'politie.nl/vermist',
                                        'url': f'https://www.politie.nl{link}',
                                        'type': 'Missing Person (Netherlands)',
                                        'description': 'Matching name parts found on politie.nl'
                                    })
                        except Exception as e:
                            logger.debug(f"Politie.nl/vermist detail page fetch failed for {link} ({type(e).__name__}): {e}")
            except Exception as e:
                logger.warning(f"Politie.nl/vermist main page scrape failed ({type(e).__name__}): {e}")

        results['api_available'] = not interpol_403
        if interpol_403 and len(results['wanted_persons']) == 0 and len(results['missing_persons']) == 0:
            results['error'] = 'INTERPOL API is tijdelijk geblokkeerd (Akamai). Politie.nl check uitgevoerd als fallback.'
            results['source'] = 'politie.nl (fallback)'

        try:
            from cms.politie_scraper import search_opsporingsberichten
            gezocht = search_opsporingsberichten(forename=forename, surname=surname, max_pages=2)
            results['opsporingsberichten'] = gezocht.get('matches', [])
        except Exception as e:
            logger.warning(f"Opsporingsberichten check error: {e}")

        return jsonify(results), 200
    except Exception as e:
        logger.error(f"Interpol data check error: {e}")
        return jsonify({'error': f'Failed to check data: {str(e)}', 'api_available': False}), 500


@cms_bp.route('/check-policie-data-status', methods=['GET'])
@login_required
def check_policie_api_status():
    """Check if INTERPOL API is available."""
    wait = _check_interpol_rate_limit()
    if wait > 0:
        return jsonify({
            'available': False, 'status_code': 429,
            'error': f'Rate limited, retry in {wait:.0f}s', 'retry_after': int(wait)
        }), 200
    try:
        import httpx
        r = httpx.get('https://ws-public.interpol.int/notices/v1/red',
                       params={'resultPerPage': 1}, headers=_interpol_headers(), timeout=10)
        return jsonify({
            'available': r.status_code == 200, 'status_code': r.status_code,
            'api_url': 'https://ws-public.interpol.int/notices/v1/', 'source': 'INTERPOL'
        }), 200
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)}), 200


@cms_bp.route('/api/findings/from-interpol', methods=['POST'])
@login_required
@validate(InterpolFindingSchema)
def create_findings_from_interpol():
    """Save Interpol/politie check results as findings."""
    data = request.validated_data

    case_id = data.get('case_id')
    subject_id = data.get('subject_id')
    wanted = data.get('wanted_persons', [])
    missing = data.get('missing_persons', [])
    opsporingen = data.get('opsporingsberichten', [])

    if not case_id:
        return jsonify({'error': 'case_id is required'}), 400
    if not wanted and not missing and not opsporingen:
        return jsonify({'error': 'No results to save'}), 400

    case = db.session.get(Case, case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404

    created = []
    for p in wanted:
        content_parts = ["Type: Red Notice (Wanted)"]
        if p.get('date_of_birth'): content_parts.append(f"DOB: {p['date_of_birth']}")
        if p.get('nationality'): content_parts.append(f"Nationality: {p['nationality']}")
        if p.get('charge'): content_parts.append(f"Charge: {p['charge']}")
        if p.get('issuing_country'): content_parts.append(f"Issued by: {p['issuing_country']}")
        if p.get('url'): content_parts.append(f"URL: {p['url']}")

        finding = Finding(
            case_id=case_id, subject_id=subject_id,
            title=f"INTERPOL Red Notice: {p.get('name', 'Unknown')}",
            content='\n'.join(content_parts), source_url=p.get('url', ''),
            source_type='interpol', finding_type='identity', reliability_score=7,
            confidence_level='medium', tags=['interpol', 'red_notice', 'wanted'],
            created_by=current_user.id
        )
        db.session.add(finding)
        created.append(finding)

    for p in missing:
        content_parts = [f"Type: {p.get('type', 'Missing Person')}"]
        if p.get('date_of_birth'): content_parts.append(f"DOB: {p['date_of_birth']}")
        if p.get('nationality'): content_parts.append(f"Nationality: {p['nationality']}")
        if p.get('date_missing'): content_parts.append(f"Missing since: {p['date_missing']}")
        if p.get('place'): content_parts.append(f"Place: {p['place']}")
        if p.get('countries_likely_to_visit'): content_parts.append(f"Likely locations: {p['countries_likely_to_visit']}")
        if p.get('source') and p['source'] != 'INTERPOL': content_parts.append(f"Source: {p['source']}")
        if p.get('description'): content_parts.append(f"Info: {p['description']}")
        if p.get('url'): content_parts.append(f"URL: {p['url']}")

        tags = ['interpol', 'yellow_notice', 'missing'] if p.get('source') == 'INTERPOL' else ['interpol', 'vermist', 'missing']
        finding = Finding(
            case_id=case_id, subject_id=subject_id,
            title=f"INTERPOL / Vermist: {p.get('name', 'Unknown')}",
            content='\n'.join(content_parts), source_url=p.get('url', ''),
            source_type='interpol', finding_type='identity', reliability_score=7,
            confidence_level='medium', tags=tags, created_by=current_user.id
        )
        db.session.add(finding)
        created.append(finding)

    for p in opsporingen:
        content_parts = ["Type: Opsporingsbericht (Politie.nl)"]
        if p.get('location'): content_parts.append(f"Locatie: {p['location']}")
        if p.get('date'): content_parts.append(f"Datum: {p['date']}")
        if p.get('url'): content_parts.append(f"URL: {p['url']}")

        finding = Finding(
            case_id=case_id, subject_id=subject_id,
            title=f"Opsporingsbericht: {p.get('title', 'Unknown')}",
            content='\n'.join(content_parts), source_url=p.get('url', ''),
            source_type='politie', finding_type='identity', reliability_score=6,
            confidence_level='medium', tags=['politie', 'opsporingsbericht', 'gezocht'],
            created_by=current_user.id
        )
        db.session.add(finding)
        created.append(finding)

    AuditLog.log(
        user_id=current_user.id, action='create', entity_type='finding',
        entity_id=None, ip_address=request.remote_addr, case_id=case_id,
        new_values={'count': len(created), 'source': 'interpol_check'},
        description=f"Added {len(created)} Interpol findings to case {case.case_number}"
    )
    db.session.commit()

    return jsonify({
        'message': f'{len(created)} bevinding(en) opgeslagen',
        'count': len(created), 'findings': [f.to_dict() for f in created]
    }), 201
