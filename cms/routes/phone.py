import logging
import re
import copy
import base64
import httpx
from datetime import datetime, timezone

import flask
from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Setting
from ..validation import validate, PhoneLookupSchema
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..api_key_auth import api_key_required
from ..feature_flags import tool_enabled

logger = logging.getLogger(__name__)


@cms_bp.route('/api/phone-lookup-stored', methods=['GET'])
@login_required
def phone_lookup_stored() -> flask.Response:
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
@csrf.exempt
@api_key_required
@tool_enabled('phone')
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='phone')
@validate(PhoneLookupSchema)
def phone_lookup() -> flask.Response:
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
            logger.debug("Phone country lookup failed")

        try:
            result['region'] = geocoder.description_for_number(parsed, 'nl')
        except Exception:
            logger.debug("Phone region lookup failed")

        try:
            result['carrier'] = carrier.name_for_number(parsed, 'nl')
        except Exception:
            logger.debug("Phone carrier lookup failed")

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
            logger.debug("Phone line_type lookup failed")

        try:
            tz = pn_tz.time_zones_for_number(parsed)
            result['timezone'] = tz[0] if tz else None
        except Exception:
            logger.debug("Phone timezone lookup failed")

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
            f"Phone lookup: {phone} \u2192 valid={result['valid']}, carrier={result['carrier']}, region={result['region']}, wa={result['services'].get('whatsapp', {}).get('exists')}")
        return jsonify(result), 200

    except ImportError:
        return jsonify({'error': 'phonenumbers library not installed'}), 500
    except Exception as e:
        logger.error(f"Phone lookup error ({type(e).__name__}): {e}")
        return jsonify({'error': f'Phone lookup failed: {str(e)}'}), 500
