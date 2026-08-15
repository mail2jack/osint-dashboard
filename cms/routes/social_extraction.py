import logging

import flask
from flask import abort, jsonify, request
from flask_login import login_required

from cms.services.http_utils import jittered_get

from ..auth import apply_tenant_filter, ensure_tenant_access
from ..models import Finding, SocialAccount, Subject, db
from ..validation import ExtractSocialIdSchema, UpdateSocialIdsSchema, validate
from . import cms_bp
from .utils import is_safe_url

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

            from cms.services.playwright_stealth import (
                apply_stealth_to_context,
                stealth_for_domain,
            )

            with sync_playwright() as p:
                stealth = stealth_for_domain(url)
                launch_kwargs: dict = {"headless": True}
                if stealth:
                    launch_kwargs["args"] = list(stealth["launch_args"])
                    launch_kwargs["args"].append(
                        f"--window-size={stealth['viewport']['width']},{stealth['viewport']['height']}"
                    )
                browser = p.chromium.launch(**launch_kwargs)
                context_kwargs: dict = {}
                if stealth:
                    context_kwargs["user_agent"] = stealth["user_agent"]
                    context_kwargs["viewport"] = dict(stealth["viewport"])
                    context_kwargs["locale"] = stealth["locale"]
                    context_kwargs["timezone_id"] = stealth["timezone_id"]
                    context_kwargs["color_scheme"] = stealth["color_scheme"]
                page = browser.new_page(**context_kwargs)
                if stealth:
                    apply_stealth_to_context(page.context)
                if not is_safe_url(url):
                    raise ValueError("Invalid or blocked URL")
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
                html = page.content()
                browser.close()
            import socid_extractor

            extracted = socid_extractor.extract(html)
        except ImportError:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            }
            if not is_safe_url(url):
                raise ValueError("Invalid or blocked URL")
            r = jittered_get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                html = r.text
                try:
                    import socid_extractor

                    extracted = socid_extractor.extract(html)
                except ImportError:
                    logger.debug("socid_extractor not available")
            import re

            fb_p = [
                r'"entity_id":"(\d+)"',
                r'"profile_id":"(\d+)"',
                r"fb://page/\?id=(\d+)",
                r"&amp;id=(\d{10,})",
                r'"uid":(\d{10,})',
            ]
            for p in fb_p:
                m = re.findall(p, html)
                if m:
                    extracted["facebook_id"] = m[0]
                    break
            ig_p = [r'"user_id":"(\d+)"', r'"id":"(\d{10,})"']
            for p in ig_p:
                m = re.findall(p, html)
                if m:
                    extracted["instagram_id"] = m[0]
                    break
            tw_p = [
                r'data-user-id="(\d+)"',
                r'"rest_id":"(\d+)"',
                r"\"id_str\":\"(\d+)\"",
            ]
            for p in tw_p:
                m = re.findall(p, html)
                if m:
                    extracted["twitter_id"] = m[0]
                    break
            tk_p = [r"\"id\":\"(\d+)\".*uniqueId", r'user-id="(\d+)"', r'"uid_(\d+)"']
            for p in tk_p:
                m = re.findall(p, html)
                if m:
                    extracted["tiktok_id"] = m[0]
                    break
            rd_p = [r'"id":"(\w+)"', r'"name":"\w+","id":"(\w+)"']
            for p in rd_p:
                m = re.findall(p, html)
                if m:
                    extracted["reddit_id"] = m[0]
                    break
    except Exception as e:
        logger.warning(f"Social ID extraction failed ({type(e).__name__}): {e}")

    # Always add social-links username when URL is a known social profile
    if sl_username and "username" not in extracted:
        extracted["username"] = sl_username
    if not any(k.endswith("id") for k in extracted):
        extracted["source_platform"] = sl_platform or "unknown"

    if subject:
        platform = sl_platform or extracted.get("source_platform")
        account_id = next(
            (v for k, v in extracted.items() if k.endswith("_id")),
            None,
        )
        username = (
            sl_username or extracted.get("username") or url.rstrip("/").split("/")[-1]
        )
        if platform and username:
            existing_sa = SocialAccount.query.filter(
                SocialAccount.subject_id == subject.id,
                SocialAccount.platform == platform,
            ).first()
            if existing_sa:
                if account_id:
                    existing_sa.account_id = account_id
                existing_sa.username = username
                existing_sa.url = url
            else:
                db.session.add(
                    SocialAccount(
                        subject_id=subject.id,
                        platform=platform,
                        username=username,
                        url=url,
                        account_id=account_id,
                    )
                )
        else:
            existing = subject.social_media_ids or {}
            for key, value in extracted.items():
                if key in ["links", "created_at", "updated_at", "fullname", "tagline"]:
                    continue
                if key.endswith("id") or key in [
                    "facebook",
                    "vk",
                    "instagram",
                    "twitter",
                    "tiktok",
                    "linkedin",
                    "reddit",
                    "platform",
                    "username",
                    "source_platform",
                ]:
                    existing[key] = value
            subject.social_media_ids = existing
        db.session.commit()

    return extracted


@cms_bp.route("/extract-social-id", methods=["POST"])
@login_required
@validate(ExtractSocialIdSchema)
def extract_social_id() -> flask.Response:
    """Extract social media IDs from a URL using socid_extractor + Playwright."""
    data = request.validated_data

    url = data.get("url")
    subject_id = data.get("subject_id")
    finding_id = data.get("finding_id")
    subject = db.session.get(Subject, subject_id) if subject_id else None
    if subject:
        ensure_tenant_access(subject)
    if finding_id:
        finding = db.session.get(Finding, finding_id) or abort(404)
        ensure_tenant_access(finding)

    extracted = _extract_social_ids_from_url(url, subject=subject)

    from ..social_extractor import detect_platform
    from ..social_extractor import extract_username as ext_username

    sl_platform = detect_platform(url)
    sl_username = ext_username(url, platform=sl_platform)

    # Link SocialAccount to finding if finding_id was provided
    if finding_id and subject:
        platform = sl_platform or extracted.get("source_platform")
        username = sl_username or extracted.get("username")
        if platform and username:
            sa = SocialAccount.query.filter(
                SocialAccount.subject_id == subject.id,
                SocialAccount.platform == platform,
            ).first()
            if sa and not sa.finding_id:
                sa.finding_id = finding_id
                db.session.commit()

    if not extracted and (sl_platform or sl_username):
        extracted = {}
        if sl_username:
            extracted["username"] = sl_username
        if sl_platform:
            extracted["profile_platform"] = sl_platform
        if subject:
            existing = subject.social_media_ids or {}
            if sl_username:
                existing["username"] = sl_username
            if sl_platform:
                existing["profile_platform"] = sl_platform
            subject.social_media_ids = existing
            db.session.commit()

    if not extracted:
        return jsonify(
            {
                "message": "No social media IDs found on this page",
                "url": url,
                "extracted": {},
                "platform": sl_platform,
                "username": sl_username,
                "note": "Some sites (Facebook, Instagram) block automated access. Try manual extraction.",
            }
        ), 200

    if subject:
        return jsonify(
            {
                "message": "Social media IDs extracted and saved",
                "url": url,
                "extracted": extracted,
                "saved_to_subject": True,
                "subject_id": subject_id,
                "platform": sl_platform,
                "username": sl_username,
            }
        ), 200

    return jsonify(
        {
            "message": "Social media IDs extracted",
            "url": url,
            "extracted": extracted,
            "platform": sl_platform,
            "username": sl_username,
        }
    ), 200


@cms_bp.route("/subjects/<subject_id>/bulk-extract-social-ids", methods=["POST"])
@login_required
def bulk_extract_social_ids(subject_id: str) -> flask.Response:
    """Extract social media IDs from all findings linked to a subject."""
    from ..social_extractor import detect_platform

    subject = db.session.get(Subject, subject_id)
    if not subject:
        abort(404)
    ensure_tenant_access(subject)
    findings = apply_tenant_filter(
        Finding.query.filter_by(subject_id=subject_id)
        .filter(Finding.source_url.isnot(None))
        .filter(Finding.source_url != ""),
        Finding,
    ).all()

    if not findings:
        return jsonify(
            {"message": "No findings with URLs to scan", "found": 0, "total": 0}
        ), 200

    total_found = 0
    skipped = 0
    not_social = 0

    # Batch-load existing social accounts for dedup
    from ..models import SocialAccount

    existing_accounts = {
        (a.platform, a.url)
        for a in apply_tenant_filter(
            SocialAccount.query.filter_by(subject_id=subject_id), SocialAccount
        ).all()
    }

    for finding in findings:
        url = finding.source_url
        platform = detect_platform(url)
        if not platform:
            not_social += 1
            continue

        if (platform, url) in existing_accounts:
            skipped += 1
            continue

        from ..social_extractor import extract_username as ext_uname

        username = ext_uname(url, platform=platform)
        if not username:
            username = finding.title.strip()[:200] or url.split("/")[-1][:200]

        account_id = None
        for prefix in ("id=", "user_id="):
            idx = url.find(prefix)
            if idx != -1:
                end = idx + len(prefix)
                acc = ""
                while end < len(url) and url[end].isdigit():
                    acc += url[end]
                    end += 1
                if acc:
                    account_id = acc
                    break

        account = SocialAccount(
            subject_id=subject_id,
            platform=platform,
            username=username,
            url=url,
            account_id=account_id,
            finding_id=finding.id,
        )
        db.session.add(account)
        total_found += 1

    db.session.commit()
    return jsonify(
        {
            "message": f"{total_found} social accounts extracted, {skipped} skipped (duplicates), {not_social} not social",
            "found": total_found,
            "skipped": skipped,
            "not_social": not_social,
            "total": len(findings),
        }
    ), 200


@cms_bp.route("/subjects/<subject_id>/social-ids", methods=["GET"])
@login_required
def get_subject_social_ids(subject_id: str) -> flask.Response:
    """Get social media IDs for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    ensure_tenant_access(subject)

    return jsonify(
        {"subject_id": subject_id, "social_media_ids": subject.social_media_ids or {}}
    )


@cms_bp.route("/subjects/<subject_id>/social-ids", methods=["PUT"])
@login_required
@validate(UpdateSocialIdsSchema)
def update_subject_social_ids(subject_id: str) -> flask.Response:
    """Update social media IDs for a subject (manual entry)."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    ensure_tenant_access(subject)
    data = request.validated_data

    # Merge with existing
    existing = subject.social_media_ids or {}
    new_data = data.get("social_media_ids", {})

    for platform, info in new_data.items():
        existing[platform] = info

    subject.social_media_ids = existing
    db.session.commit()

    return jsonify(
        {
            "message": "Social media IDs updated",
            "social_media_ids": subject.social_media_ids,
        }
    )
