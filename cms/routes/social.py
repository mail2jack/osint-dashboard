import logging
import re
import requests as http_requests
from urllib.parse import urlparse

from flask import request, jsonify, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Subject, Finding, AuditLog
from ..validation import validate, CheckExistingUrlsSchema, AddSocialAccountSchema, SaveFindingAsSocialAccountSchema, SaveUsernameFindingsSchema, CreateSubjectFromUsernameSchema, ExtractSocialIdSchema

logger = logging.getLogger(__name__)


def _extract_social_ids_from_url(url, subject=None):
    """Extract social media IDs from a URL. Returns dict of extracted IDs.
    Optionally merges into subject.social_media_ids if subject is provided.
    Always adds username/platform from social-links when URL is a known social profile.
    """
    extracted = {}
    html = None

    # Pre-check: is this a social media URL?
    from ..social_extractor import detect_platform, extract_username
    sl_platform = detect_platform(url)
    sl_username = extract_username(url, platform=sl_platform)

    # Skip scraping entirely for non-social URLs
    if not sl_platform and not sl_username:
        return extracted

    try:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(2000)
                html = page.content()
                browser.close()
            import socid_extractor
            extracted = socid_extractor.extract(html)
        except ImportError:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
            r = http_requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                html = r.text
                try:
                    import socid_extractor
                    extracted = socid_extractor.extract(html)
                except ImportError:
                    pass
        except Exception as e:
            logger.warning(f"Playwright extraction failed ({type(e).__name__}): {e}")
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
            r = http_requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                html = r.text
                try:
                    import socid_extractor
                    extracted = socid_extractor.extract(html)
                except ImportError:
                    pass

        if not extracted and html:
            import re
            fb_p = [r'"entity_id":"(\d+)"', r'"profile_id":"(\d+)"', r'fb://page/\?id=(\d+)', r'&amp;id=(\d{10,})', r'"uid":(\d{10,})']
            for p in fb_p:
                m = re.findall(p, html)
                if m: extracted['facebook_id'] = m[0]; break
            ig_p = [r'"user_id":"(\d+)"', r'"id":"(\d{10,})"']
            for p in ig_p:
                m = re.findall(p, html)
                if m: extracted['instagram_id'] = m[0]; break
            tw_p = [r'data-user-id="(\d+)"', r'"rest_id":"(\d+)"', r'\"id_str\":\"(\d+)\"']
            for p in tw_p:
                m = re.findall(p, html)
                if m: extracted['twitter_id'] = m[0]; break
            tk_p = [r'\"id\":\"(\d+)\".*uniqueId', r'user-id="(\d+)"', r'"uid_(\d+)"']
            for p in tk_p:
                m = re.findall(p, html)
                if m: extracted['tiktok_id'] = m[0]; break
            rd_p = [r'"id":"(\w+)"', r'"name":"\w+","id":"(\w+)"']
            for p in rd_p:
                m = re.findall(p, html)
                if m: extracted['reddit_id'] = m[0]; break
    except Exception as e:
        logger.warning(f"Social ID extraction failed ({type(e).__name__}): {e}")

    # Always add social-links username when URL is a known social profile
    if sl_username and 'username' not in extracted:
        extracted['username'] = sl_username
    if not any(k.endswith('id') for k in extracted):
        extracted['source_platform'] = sl_platform or 'unknown'

    if subject:
        existing = subject.social_media_ids or {}
        for key, value in extracted.items():
            if key in ['links', 'created_at', 'updated_at', 'fullname', 'tagline']:
                continue
            if key.endswith('id') or key in ['facebook', 'vk', 'instagram', 'twitter', 'tiktok', 'linkedin', 'reddit', 'platform', 'username', 'source_platform']:
                existing[key] = value
        subject.social_media_ids = existing
        db.session.commit()

    return extracted


@cms_bp.route('/api/findings/check-existing-urls', methods=['POST'])
@login_required
@validate(CheckExistingUrlsSchema)
def check_existing_finding_urls():
    """Check which OSINT result URLs already have findings."""
    data = request.validated_data
    case_id = data.get('case_id')
    urls = data.get('urls', [])
    if not case_id or not urls:
        return jsonify({'existing': []})
    existing = Finding.query.filter(
        Finding.case_id == case_id,
        Finding.source_url.in_(urls),
        Finding.is_deleted == False
    ).with_entities(Finding.source_url).distinct().all()
    return jsonify({'existing': [r[0] for r in existing]})


@cms_bp.route('/api/subjects/<subject_id>/social-accounts', methods=['POST'])
@login_required
@validate(AddSocialAccountSchema)
def add_social_account(subject_id: str):
    """Add a social account (username) to a subject."""
    from ..models import SocialAccount
    subject = db.session.get(Subject, subject_id) or abort(404)
    data = request.validated_data
    platform = (data.get('platform') or '').strip().lower()
    username = (data.get('username') or '').strip()
    account = SocialAccount(
        subject_id=subject.id, platform=platform, username=username,
        url=(data.get('url') or '').strip(), account_id=(data.get('account_id') or '').strip(),
    )
    db.session.add(account)
    db.session.commit()
    return jsonify({'message': 'Social account added', 'account': account.to_dict()}), 201


@cms_bp.route('/api/subjects/<subject_id>/social-accounts/<account_id>', methods=['DELETE'])
@login_required
def delete_social_account(subject_id: str, account_id: str):
    """Delete a social account."""
    from ..models import SocialAccount
    account = db.session.get(SocialAccount, account_id)
    if not account or str(account.subject_id) != subject_id:
        abort(404)
    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'Social account deleted'})


@cms_bp.route('/api/findings/save-as-social-account', methods=['POST'])
@login_required
@validate(SaveFindingAsSocialAccountSchema)
def save_finding_as_social_account():
    """Save an OSINT finding's source URL as a social account on the linked subject."""
    from ..models import SocialAccount
    from ..social_extractor import detect_platform, extract_username
    data = request.validated_data

    finding_id = data.get('finding_id')
    subject_id = data.get('subject_id')
    if not finding_id and not subject_id:
        return jsonify({'error': 'finding_id or subject_id required'}), 400

    url = data.get('url') or ''
    platform = (data.get('platform') or '').strip().lower()
    username = (data.get('username') or '').strip()

    if finding_id:
        finding = db.session.get(Finding, finding_id) or abort(404)
        if not finding.subject_id:
            return jsonify({'error': 'Finding not linked to a subject'}), 400
        subject_id = finding.subject_id
        if not url:
            url = finding.source_url
        if not username:
            subj = db.session.get(Subject, subject_id)
            username = data.get('username') or (subj.name if subj else finding.title.strip())

    if not username and url:
        username = extract_username(url, platform=platform)
    if not username and url:
        path = re.sub(r'https?://', '', url).split('/')
        username = path[-1] if len(path) > 1 else path[0]
    if not username:
        username = url or 'unknown'

    if not platform and url:
        platform = detect_platform(url)
    if not platform:
        parsed = urlparse(url)
        platform = parsed.netloc.replace('www.', '').split('.')[0] if parsed.netloc else 'unknown'

    account = SocialAccount(
        subject_id=subject_id, platform=platform, username=username, url=url,
    )
    db.session.add(account)
    db.session.commit()
    return jsonify({'message': 'Social account created', 'account': account.to_dict()}), 201


@cms_bp.route('/api/subjects/<subject_id>/save-username-findings', methods=['POST'])
@login_required
@validate(SaveUsernameFindingsSchema)
def save_username_findings(subject_id: str):
    """Save RapidAPI username check results as Findings + Social Accounts."""
    from ..models import SocialAccount
    data = request.validated_data
    results = data.get('results', [])

    case_id = data.get('case_id') or ''
    subject = db.session.get(Subject, subject_id) or abort(404)
    findings_count = 0
    social_count = 0

    for r in results:
        platform = r.get('platform', '')
        url = r.get('url', '')
        username = r.get('username', '')
        if not platform or not url:
            continue

        finding = Finding(
            case_id=case_id if case_id else None, subject_id=subject_id,
            title=f"Username: {username} - {platform}",
            content=f"Username found on {platform}\nURL: {url}\nUsername: {username}\nSource: RapidAPI username check",
            source_url=url, source_type='osint', finding_type='identity',
            reliability_score=5, confidence_level='medium',
            created_by=current_user.id, tags=['username', platform.lower(), 'rapidapi']
        )
        db.session.add(finding)
        findings_count += 1

        existing = SocialAccount.query.filter_by(
            subject_id=subject_id, platform=platform, username=username
        ).first()
        if not existing:
            account = SocialAccount(
                subject_id=subject_id, platform=platform, username=username, url=url,
            )
            db.session.add(account)
            social_count += 1

    AuditLog.log(
        user_id=current_user.id, action='create', entity_type='finding',
        ip_address=request.remote_addr, case_id=case_id,
        description=f"Saved {findings_count} username findings + {social_count} social accounts for subject {subject.name}"
    )
    db.session.commit()

    return jsonify({
        'message': f'{findings_count} finding(s) added, {social_count} social account(s) created',
        'findings_count': findings_count, 'social_count': social_count,
    }), 201


@cms_bp.route('/api/subjects/create-from-username', methods=['POST'])
@login_required
@validate(CreateSubjectFromUsernameSchema)
def create_subject_from_username():
    """Create a subject from just a username (no full name needed)."""
    from ..models import SocialAccount
    from ..social_extractor import detect_platform, extract_username
    data = request.validated_data
    username = (data.get('username') or '').strip()
    platform = (data.get('platform') or '').strip().lower()
    url = (data.get('url') or '').strip()

    if not username:
        if url:
            username = extract_username(url) or ''
        if not username:
            return jsonify({'error': 'username required'}), 400

    if not platform:
        if url:
            platform = detect_platform(url) or ''
        if not platform:
            platform = 'other'

    display_name = f"{username} ({platform})" if platform != 'other' else username
    subject = Subject(
        name=display_name, subject_type='person',
        notes=f"Created from username '{username}' on {platform}",
    )
    db.session.add(subject)
    db.session.flush()

    account = SocialAccount(
        subject_id=subject.id, platform=platform, username=username, url=url,
    )
    db.session.add(account)

    case_id = data.get('case_id')
    if case_id:
        from ..models import Case
        case = db.session.get(Case, case_id)
        if case:
            subject.cases.append(case)

    db.session.commit()
    return jsonify({'message': 'Subject created', 'subject': subject.to_dict(), 'account': account.to_dict()}), 201


@cms_bp.route('/extract-social-id', methods=['POST'])
@login_required
@validate(ExtractSocialIdSchema)
def extract_social_id():
    """Extract social media IDs from a URL using socid_extractor + Playwright."""
    data = request.validated_data

    url = data.get('url')
    subject_id = data.get('subject_id')
    subject = db.session.get(Subject, subject_id) if subject_id else None

    extracted = _extract_social_ids_from_url(url, subject=subject)

    from ..social_extractor import detect_platform, extract_username as ext_username
    sl_platform = detect_platform(url)
    sl_username = ext_username(url, platform=sl_platform)

    if not extracted and (sl_platform or sl_username):
        extracted = {}
        if sl_username:
            extracted['username'] = sl_username
        if sl_platform:
            extracted['profile_platform'] = sl_platform
        if subject:
            existing = subject.social_media_ids or {}
            if sl_username:
                existing['username'] = sl_username
            if sl_platform:
                existing['profile_platform'] = sl_platform
            subject.social_media_ids = existing
            db.session.commit()

    if not extracted:
        return jsonify({
            'message': 'No social media IDs found on this page', 'url': url,
            'extracted': {}, 'platform': sl_platform, 'username': sl_username,
            'note': 'Some sites (Facebook, Instagram) block automated access. Try manual extraction.'
        }), 200

    if subject:
        return jsonify({
            'message': 'Social media IDs extracted and saved', 'url': url,
            'extracted': extracted, 'saved_to_subject': True, 'subject_id': subject_id,
            'platform': sl_platform, 'username': sl_username,
        }), 200

    return jsonify({
        'message': 'Social media IDs extracted', 'url': url, 'extracted': extracted,
        'platform': sl_platform, 'username': sl_username,
    }), 200


@cms_bp.route('/subjects/<subject_id>/bulk-extract-social-ids', methods=['POST'])
@login_required
def bulk_extract_social_ids(subject_id: str):
    """Extract social media IDs from all findings linked to a subject."""
    from ..social_extractor import detect_platform
    subject = db.session.get(Subject, subject_id) or abort(404)
    findings = Finding.query.filter_by(subject_id=subject_id).filter(
        Finding.source_url.isnot(None)
    ).filter(Finding.source_url != '').all()

    if not findings:
        return jsonify({'message': 'No findings with URLs to scan', 'found': 0, 'total': 0}), 200

    total_found = 0
    skipped = 0
    not_social = 0

    for finding in findings:
        url = finding.source_url
        platform = detect_platform(url)
        if not platform:
            not_social += 1
            continue

        from ..models import SocialAccount
        existing = SocialAccount.query.filter_by(
            subject_id=subject_id, platform=platform, url=url
        ).first()
        if existing:
            skipped += 1
            continue

        account = SocialAccount(
            subject_id=subject_id, platform=platform,
            username=finding.title.strip()[:200] or url.split('/')[-1][:200],
            url=url,
        )
        db.session.add(account)
        total_found += 1

    db.session.commit()
    return jsonify({
        'message': f'{total_found} social accounts extracted, {skipped} skipped (duplicates), {not_social} not social',
        'found': total_found, 'skipped': skipped, 'not_social': not_social, 'total': len(findings),
    }), 200


@cms_bp.route('/subjects/<subject_id>/social-ids', methods=['GET'])
@login_required
def get_subject_social_ids(subject_id: str):
    """Get social media IDs for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    return jsonify({
        'subject_id': subject_id,
        'social_media_ids': subject.social_media_ids or {}
    })


@cms_bp.route('/subjects/<subject_id>/social-ids', methods=['PUT'])
@login_required
def update_subject_social_ids(subject_id: str):
    """Update social media IDs for a subject (manual entry)."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Merge with existing
    existing = subject.social_media_ids or {}
    new_data = data.get('social_media_ids', {})

    for platform, info in new_data.items():
        existing[platform] = info

    subject.social_media_ids = existing
    db.session.commit()

    return jsonify({
        'message': 'Social media IDs updated',
        'social_media_ids': subject.social_media_ids
    })
