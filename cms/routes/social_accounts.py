import logging
import re
from urllib.parse import urlparse

import flask
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Subject, Finding, AuditLog
from ..validation import validate, CheckExistingUrlsSchema, AddSocialAccountSchema, SaveFindingAsSocialAccountSchema, SaveUsernameFindingsSchema, CreateSubjectFromUsernameSchema

logger = logging.getLogger(__name__)


@cms_bp.route('/api/findings/check-existing-urls', methods=['POST'])
@csrf.exempt
@login_required
@validate(CheckExistingUrlsSchema)
def check_existing_finding_urls() -> flask.Response:
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
@csrf.exempt
@login_required
@validate(AddSocialAccountSchema)
def add_social_account(subject_id: str) -> flask.Response:
    """Add a social account (username) to a subject."""
    from ..models import SocialAccount
    subject = db.session.get(Subject, subject_id)
    if not subject:
        return jsonify({'error': 'Subject not found'}), 404
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
def delete_social_account(subject_id: str, account_id: str) -> flask.Response:
    """Delete a social account."""
    from ..models import SocialAccount
    account = db.session.get(SocialAccount, account_id)
    if not account or str(account.subject_id) != subject_id:
        return jsonify({'error': 'Social account not found'}), 404
    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'Social account deleted'})


@cms_bp.route('/api/findings/save-as-social-account', methods=['POST'])
@csrf.exempt
@login_required
@validate(SaveFindingAsSocialAccountSchema)
def save_finding_as_social_account() -> flask.Response:
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
@csrf.exempt
@login_required
@validate(SaveUsernameFindingsSchema)
def save_username_findings(subject_id: str) -> flask.Response:
    """Save RapidAPI username check results as Findings + Social Accounts."""
    from ..models import SocialAccount
    data = request.validated_data
    results = data.get('results', [])

    case_id = data.get('case_id') or ''
    subject = db.session.get(Subject, subject_id) or abort(404)

    # Batch-load existing social accounts for dedup
    existing_accounts = {
        (a.platform, a.username)
        for a in SocialAccount.query.filter_by(subject_id=subject_id).all()
    }

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

        if (platform, username) not in existing_accounts:
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
@csrf.exempt
@login_required
@validate(CreateSubjectFromUsernameSchema)
def create_subject_from_username() -> flask.Response:
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
