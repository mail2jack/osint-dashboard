from .response import api_success, api_error

"""
SpiderFoot OSINT Integration Routes
====================================
SpiderFoot scan management, status, results, import, and settings.
"""

import logging
from datetime import datetime, timezone
import flask
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..validation import (
    validate,
    SpiderFootScanSchema,
    SpiderFootImportSchema,
    SpiderFootSettingsSchema,
    SpiderFootTestSchema,
    SpiderFootScanSubjectSchema,
)
from ..models import db, SpiderFootScan, Setting, Case, Subject, Finding, AuditLog
from ..auth import (
    roles_required,
    admin_required,
    apply_tenant_filter,
    ensure_tenant_access,
)

try:
    from ..spiderfoot_service import SpiderFootService, ScanTarget

    SPIDERFOOT_AVAILABLE = True
except ImportError:
    SPIDERFOOT_AVAILABLE = False
    SpiderFootService = None
    ScanTarget = None

logger = logging.getLogger(__name__)


def get_spiderfoot_config() -> dict:
    """Get SpiderFoot configuration from settings."""
    from ..spiderfoot_service import SpiderFootConfig
    from ..setting_cache import cached_setting_get

    base_url = (
        cached_setting_get("spiderfoot_url", "http://localhost:5001")
        or "http://localhost:5001"
    )
    username = cached_setting_get("spiderfoot_username", "admin") or "admin"
    password = cached_setting_get("spiderfoot_password", "") or ""

    return SpiderFootConfig(base_url=base_url, username=username, password=password)


def get_spiderfoot_service() -> object | None:
    """Get SpiderFoot service instance."""
    if not SPIDERFOOT_AVAILABLE:
        return None
    return SpiderFootService(get_spiderfoot_config())


@cms_bp.route("/spiderfoot")
@login_required
@roles_required("admin", "senior_investigator")
def spiderfoot_index() -> str:
    """SpiderFoot integration dashboard."""
    try:
        sf_service = get_spiderfoot_service()
        available = sf_service.is_available() if sf_service else False
        server_info = sf_service.get_server_info() if sf_service and available else None
    except Exception as e:
        logger.warning(f"SpiderFoot dashboard init failed ({type(e).__name__}): {e}")
        sf_service = None
        available = False
        server_info = None

    try:
        db.session.rollback()

        db_scans = (
            apply_tenant_filter(
                SpiderFootScan.query.filter_by(is_deleted=False),
                SpiderFootScan,
            )
            .order_by(SpiderFootScan.created_at.desc())
            .limit(10)
            .all()
        )

        sf_scans = []
        if available:
            try:
                sf_scans = sf_service.get_scan_list() or []
            except Exception as e:
                logger.debug(
                    f"Failed to fetch SpiderFoot scan list ({type(e).__name__}): {e}"
                )

        db_sf_ids = {s.scan_id for s in db_scans}
        recent_scans = list(db_scans)
        for sf_scan in sf_scans:
            if isinstance(sf_scan, list) and len(sf_scan) >= 7:
                sf_id = sf_scan[0]
                if sf_id and sf_id not in db_sf_ids:
                    status_raw = sf_scan[6]
                    api_status = status_raw.lower() if status_raw else "unknown"
                    mapped_status = {
                        "finished": "completed",
                        "error": "failed",
                        "aborted": "cancelled",
                    }.get(api_status, api_status)
                    recent_scans.append(
                        {
                            "id": sf_id,
                            "scan_id": sf_id,
                            "scan_name": sf_scan[1]
                            if len(sf_scan) > 1
                            else "SpiderFoot Scan",
                            "target_value": sf_scan[2] if len(sf_scan) > 2 else "",
                            "target_type": "",
                            "status": mapped_status,
                            "progress": 100 if mapped_status == "completed" else 0,
                            "result_count": sf_scan[7] if len(sf_scan) > 7 else 0,
                            "profile": "",
                            "use_case": "",
                            "created_at": sf_scan[3] if len(sf_scan) > 3 else None,
                            "from_spiderfoot": True,
                        }
                    )
            elif isinstance(sf_scan, dict):
                sf_id = sf_scan.get("scan_id") or sf_scan.get("id")
                if sf_id and sf_id not in db_sf_ids:
                    recent_scans.append(
                        {
                            "id": sf_id,
                            "scan_id": sf_id,
                            "scan_name": sf_scan.get(
                                "scan_name", sf_scan.get("title", "SpiderFoot Scan")
                            ),
                            "target_value": sf_scan.get(
                                "target", sf_scan.get("target_value", "")
                            ),
                            "target_type": sf_scan.get("target_type", ""),
                            "status": (sf_scan.get("status") or "").lower(),
                            "progress": sf_scan.get("progress", 0),
                            "result_count": sf_scan.get(
                                "resultCount", sf_scan.get("result_count", 0)
                            ),
                            "profile": sf_scan.get("profile", ""),
                            "use_case": sf_scan.get("use_case", ""),
                            "created_at": None,
                            "from_spiderfoot": True,
                        }
                    )
        for s in recent_scans:
            if isinstance(s, dict) and isinstance(s.get("created_at"), datetime):
                s["created_at"] = s["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        recent_scans.sort(
            key=lambda s: (
                s.get("created_at", "")
                if isinstance(s, dict)
                else (
                    s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else ""
                )
            ),
            reverse=True,
        )
        recent_scans = recent_scans[:10]

        status_counts = {"running": 0, "completed": 0, "pending": 0, "failed": 0}
        db_counts = {
            "running": apply_tenant_filter(
                SpiderFootScan.query.filter_by(status="running", is_deleted=False),
                SpiderFootScan,
            ).count(),
            "completed": apply_tenant_filter(
                SpiderFootScan.query.filter_by(status="completed", is_deleted=False),
                SpiderFootScan,
            ).count(),
            "pending": apply_tenant_filter(
                SpiderFootScan.query.filter_by(status="pending", is_deleted=False),
                SpiderFootScan,
            ).count(),
            "failed": apply_tenant_filter(
                SpiderFootScan.query.filter_by(status="failed", is_deleted=False),
                SpiderFootScan,
            ).count(),
        }
        status_map = {
            "finished": "completed",
            "running": "running",
            "pending": "pending",
            "failed": "failed",
            "error": "failed",
            "aborted": "cancelled",
            "cancelled": "cancelled",
        }
        for s in sf_scans:
            raw_st = (
                (s[6] or "").lower()
                if isinstance(s, list) and len(s) >= 7
                else ((s.get("status") or "").lower() if isinstance(s, dict) else "")
            )
            mapped_st = status_map.get(raw_st, raw_st)
            if mapped_st in status_counts:
                status_counts[mapped_st] += 1
        for k in status_counts:
            status_counts[k] += db_counts.get(k, 0)

        profiles = SpiderFootService.INVESTIGATION_PROFILES if SpiderFootService else {}
        use_cases = SpiderFootService.USE_CASES if SpiderFootService else {}
        target_types = SpiderFootService.TARGET_TYPES if SpiderFootService else {}

        return render_template(
            "cms/spiderfoot/index.html",
            available=available,
            server_info=server_info,
            recent_scans=recent_scans,
            status_counts=status_counts,
            profiles=profiles,
            use_cases=use_cases,
            target_types=target_types,
        )
    except Exception as e:
        logger.exception(f"SpiderFoot index failed ({type(e).__name__}): {e}")
        return render_template("cms/500.html"), 500


@cms_bp.route("/spiderfoot/scan", methods=["GET", "POST"])
@login_required
@roles_required("admin", "senior_investigator")
@validate(SpiderFootScanSchema)
def spiderfoot_scan() -> str | flask.Response:
    """Start a new SpiderFoot scan."""
    if request.method == "GET":
        # Show scan form
        search_q = request.args.get("q", "").strip()
        case_query = apply_tenant_filter(Case.query.filter_by(is_deleted=False), Case)
        subject_query = apply_tenant_filter(
            Subject.query.filter_by(is_deleted=False), Subject
        )
        if search_q:
            case_query = case_query.filter(Case.title.ilike(f"%{search_q}%"))
            subject_query = subject_query.filter(Subject.name.ilike(f"%{search_q}%"))
        cases = case_query.order_by(Case.case_number.desc()).limit(500).all()
        subjects = subject_query.order_by(Subject.name).limit(500).all()

        profiles = SpiderFootService.INVESTIGATION_PROFILES if SpiderFootService else {}
        use_cases = SpiderFootService.USE_CASES if SpiderFootService else {}
        target_types = SpiderFootService.TARGET_TYPES if SpiderFootService else {}

        # Get recent unique targets for quick-select
        recent_scans = (
            apply_tenant_filter(
                SpiderFootScan.query.filter_by(is_deleted=False),
                SpiderFootScan,
            )
            .order_by(SpiderFootScan.created_at.desc())
            .limit(20)
            .all()
        )
        seen = set()
        recent_targets = []
        for s in recent_scans:
            key = (s.target_value or "", s.target_type or "")
            if key not in seen:
                seen.add(key)
                recent_targets.append({"target": s.target_value, "type": s.target_type})

        return render_template(
            "cms/spiderfoot/scan.html",
            cases=cases,
            subjects=subjects,
            profiles=profiles,
            use_cases=use_cases,
            target_types=target_types,
            recent_targets=recent_targets,
        )

    # POST - Start scan
    try:
        return _start_spiderfoot_scan()
    except Exception as e:
        logger.exception("SpiderFoot scan start failed")
        if request.is_json:
            return api_error(f"Internal error: {e}", 500)
        flash(f"Failed to start scan: {e}", "error")
        return redirect(url_for("cms.spiderfoot_index"))


def _start_spiderfoot_scan() -> str | flask.Response:
    data = request.validated_data

    from ..tier_limits import check_feature, check_concurrent_spiderfoot_scans

    if not check_feature("spiderfoot"):
        if request.is_json:
            return api_error(
                "SpiderFoot is not available on your current plan. Upgrade to access this feature.",
                403,
            )
        flash("SpiderFoot is not available on your current plan.", "warning")
        return redirect(url_for("cms.settings", category="plan"))

    ok, running, maximum = check_concurrent_spiderfoot_scans()
    if not ok:
        if request.is_json:
            return api_error(
                f"Maximum concurrent SpiderFoot scans reached ({running}/{maximum}). Please wait for a scan to finish.",
                429,
            )
        flash(
            f"Maximum concurrent SpiderFoot scans reached ({running}/{maximum}). Please wait for a scan to finish.",
            "warning",
        )
        return redirect(url_for("cms.spiderfoot_index"))

    target = data.get("target")
    target_type = data.get("target_type", "DOMAIN_NAME")
    scan_name = data.get("scan_name")
    case_id = data.get("case_id")
    subject_id = data.get("subject_id")
    profile = data.get("profile")
    use_case = data.get("use_case", "passive")

    if not target:
        if request.is_json:
            return api_error("Target is required", 400)
        flash("Target is required.", "error")
        return redirect(url_for("cms.spiderfoot_scan"))

    sf_service = get_spiderfoot_service()

    if not sf_service or not sf_service.is_available():
        if request.is_json:
            return jsonify({"error": "SpiderFoot server is not available"}), 503
        flash("SpiderFoot server is not available. Please check the settings.", "error")
        return redirect(url_for("cms.spiderfoot_index"))

    # Start the scan
    result = sf_service.start_scan(
        target=target,
        target_type=target_type,
        scan_name=scan_name,
        use_case=use_case,
        profile=profile,
    )

    if not result or not result.get("scan_id"):
        if request.is_json:
            return jsonify({"error": "Failed to start scan"}), 500
        flash("Failed to start SpiderFoot scan.", "error")
        return redirect(url_for("cms.spiderfoot_index"))

    # Create local scan record
    scan_record = SpiderFootScan(
        scan_id=result["scan_id"],
        scan_name=scan_name or f"Scan of {target}",
        target_value=target,
        target_type=target_type,
        case_id=case_id if case_id else None,
        subject_id=subject_id if subject_id else None,
        use_case=use_case,
        profile=profile,
        module_ids=SpiderFootService.INVESTIGATION_PROFILES.get(profile, {}).get(
            "modules", []
        )
        if (SpiderFootService and profile)
        else [],
        status="pending",
        created_by=current_user.id,
    )
    scan_record.update_status("running", 0)

    db.session.add(scan_record)

    AuditLog.log(
        user_id=current_user.id,
        action="spiderfoot_scan_start",
        entity_type="spiderfoot_scan",
        entity_id=scan_record.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Started SpiderFoot scan: {scan_record.scan_name} for {target}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify({"message": "Scan started", "scan": scan_record.to_dict()}), 201

    flash("SpiderFoot scan started.", "success")
    return redirect(url_for("cms.spiderfoot_scan_status", scan_id=scan_record.id))


@cms_bp.route("/spiderfoot/scan/<scan_id>")
@login_required
@roles_required("admin", "senior_investigator")
def spiderfoot_scan_status(scan_id: str) -> flask.Response:
    """View SpiderFoot scan status and results."""

    # Try Iveras DB record first, fall back to direct SpiderFoot scan ID
    scan_record = db.session.get(SpiderFootScan, scan_id)
    ensure_tenant_access(scan_record)

    sf_service = get_spiderfoot_service()
    if not sf_service:
        if scan_record:
            return render_template(
                "cms/spiderfoot/view.html",
                scan=scan_record,
                sf_status=None,
                results=[],
                result_summary={},
                available=False,
            )
        abort(503)

    # Determine the actual SpiderFoot scan_id
    sf_scan_id = scan_record.scan_id if scan_record else scan_id

    # Refresh status from SpiderFoot
    sf_status = sf_service.get_scan_status(sf_scan_id)

    if sf_status:
        status = sf_status.get("status", "unknown")
        status_lower = status.lower()
        progress = sf_status.get("progress", 0)

        # Create or update DB record
        if not scan_record:
            scan_record = SpiderFootScan(
                id=scan_id,
                scan_id=sf_scan_id,
                scan_name=sf_status.get(
                    "scan_name", sf_status.get("title", f"Scan {sf_scan_id[:8]}")
                ),
                target_value=sf_status.get("target", sf_status.get("target_value", "")),
                target_type=sf_status.get("target_type", ""),
                status=status,
                progress=progress,
                created_by="system",
            )
            scan_record.created_at = datetime.now(timezone.utc)
            db.session.add(scan_record)
        else:
            scan_record.status = status
            scan_record.progress = progress

        # Map status
        if status_lower in ["completed", "finished"]:
            scan_record.update_status("completed")
        elif status_lower == "running":
            scan_record.update_status("running", progress)
        elif status_lower in ["failed", "error"]:
            scan_record.update_status("failed")
        elif status_lower in ["aborted", "cancelled"]:
            scan_record.update_status("cancelled")

        try:
            db.session.commit()
        except Exception as e:
            logger.warning(f"DB commit failed ({type(e).__name__}): {e}")
            db.session.rollback()

    # Get results if completed
    results = []
    result_summary = {}
    status_lower = (scan_record.status or "").lower()
    if status_lower in ["completed", "finished"]:
        results = sf_service.get_scan_results(sf_scan_id, limit=5000)
        result_summary = sf_service.get_result_summary(results)
        if scan_record:
            scan_record.result_count = len(results)
            scan_record.result_summary = result_summary
            try:
                db.session.commit()
            except Exception as e:
                logger.warning(f"DB commit failed ({type(e).__name__}): {e}")
                db.session.rollback()

    return render_template(
        "cms/spiderfoot/view.html",
        scan=scan_record,
        sf_status=sf_status,
        results=results[:100],  # Limit displayed results
        result_summary=result_summary,
        available=sf_service.is_available(),
    )


@cms_bp.route("/spiderfoot/scan/<scan_id>/refresh", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator")
def spiderfoot_refresh_scan(scan_id: str) -> flask.Response:
    """Refresh SpiderFoot scan status."""
    scan_record = db.session.get(SpiderFootScan, scan_id) or abort(404)
    ensure_tenant_access(scan_record)

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({"error": "SpiderFoot server not available"}), 503

    sf_status = sf_service.get_scan_status(scan_record.scan_id)

    if sf_status:
        status = sf_status.get("status", "unknown")
        status_lower = status.lower()  # Normalize to lowercase
        progress = sf_status.get("progress", 0)
        scan_record.status = status
        scan_record.progress = progress

        if status_lower in ["completed", "finished"]:
            results = sf_service.get_scan_results(scan_record.scan_id, limit=5000)
            result_summary = sf_service.get_result_summary(results)
            scan_record.result_count = len(results)
            scan_record.result_summary = result_summary
            scan_record.update_status("completed")
        elif status_lower in ["failed", "error"]:
            scan_record.update_status("failed")
        elif status_lower in ["aborted", "cancelled"]:
            scan_record.update_status("cancelled")

        db.session.commit()

    return jsonify(scan_record.to_dict())


@cms_bp.route("/spiderfoot/scan/<scan_id>/stop", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator")
def spiderfoot_stop_scan(scan_id: str) -> flask.Response:
    """Stop a running SpiderFoot scan."""
    scan_record = db.session.get(SpiderFootScan, scan_id) or abort(404)
    ensure_tenant_access(scan_record)

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({"error": "SpiderFoot server not available"}), 503

    if sf_service.stop_scan(scan_record.scan_id):
        scan_record.update_status("cancelled")
        db.session.commit()

        AuditLog.log(
            user_id=current_user.id,
            action="spiderfoot_scan_stop",
            entity_type="spiderfoot_scan",
            entity_id=scan_record.id,
            ip_address=request.remote_addr,
            description=f"Stopped SpiderFoot scan: {scan_record.scan_name}",
        )
        db.session.commit()

        return jsonify({"message": "Scan stopped", "scan": scan_record.to_dict()})

    return jsonify({"error": "Failed to stop scan"}), 500


@cms_bp.route("/spiderfoot/scan/<scan_id>/delete", methods=["POST"])
@login_required
@admin_required
def spiderfoot_delete_scan(scan_id: str) -> flask.Response:
    """Delete a SpiderFoot scan record."""
    scan_record = db.session.get(SpiderFootScan, scan_id) or abort(404)
    ensure_tenant_access(scan_record)

    sf_service = get_spiderfoot_service()

    # Try to delete from SpiderFoot as well
    try:
        if scan_record.status not in ["running"]:
            sf_service.delete_scan(scan_record.scan_id)
    except Exception as e:
        logger.warning(f"Could not delete SpiderFoot scan ({type(e).__name__}): {e}")

    scan_record.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action="spiderfoot_scan_delete",
        entity_type="spiderfoot_scan",
        entity_id=scan_record.id,
        ip_address=request.remote_addr,
        description=f"Deleted SpiderFoot scan record: {scan_record.scan_name}",
    )
    db.session.commit()

    if request.is_json:
        return api_success({}, "Scan deleted")

    flash("Scan record deleted.", "info")
    return redirect(url_for("cms.spiderfoot_index"))


@cms_bp.route("/spiderfoot/scan/<scan_id>/results")
@login_required
@roles_required("admin", "senior_investigator")
def spiderfoot_scan_results(scan_id: str) -> flask.Response:
    """Get full SpiderFoot scan results as JSON."""
    scan_record = db.session.get(SpiderFootScan, scan_id) or abort(404)
    ensure_tenant_access(scan_record)

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({"error": "SpiderFoot server not available"}), 503

    element_type = request.args.get("type")  # Filter by type
    limit = request.args.get("limit", 10000, type=int)

    results = sf_service.get_scan_results(
        scan_record.scan_id, element_type=element_type, limit=limit
    )
    summary = sf_service.get_result_summary(results)

    return jsonify(
        {
            "scan": scan_record.to_dict(),
            "results": results,
            "summary": summary,
            "total": len(results),
        }
    )


@cms_bp.route("/spiderfoot/scan/<scan_id>/import", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator")
@validate(SpiderFootImportSchema)
def spiderfoot_import_results(scan_id: str) -> flask.Response:
    """Import SpiderFoot scan results as Iveras findings."""
    scan_record = db.session.get(SpiderFootScan, scan_id) or abort(404)
    ensure_tenant_access(scan_record)

    if not scan_record.case_id:
        return jsonify(
            {"error": "Scan must be linked to a case to import findings"}
        ), 400

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({"error": "SpiderFoot server not available"}), 503

    data = request.validated_data

    # Filter options
    element_types = data.get("element_types", [])  # Only import these types
    min_length = data.get("min_length", 3)  # Minimum data length
    limit = data.get("limit", 1000)

    results = sf_service.get_scan_results(scan_record.scan_id, limit=limit)

    imported_count = 0
    skipped_count = 0

    for result in results:
        sf_type = result.get("type", "")

        # Filter by type if specified
        if element_types and sf_type not in element_types:
            skipped_count += 1
            continue

        # Filter by data length
        data_val = result.get("data", "") or result.get("dataTransformed", "")
        if len(data_val) < min_length:
            skipped_count += 1
            continue

        # Map to Iveras finding
        finding_data = sf_service.map_to_iveras_finding(
            result, case_id=scan_record.case_id, subject_id=scan_record.subject_id
        )

        finding = Finding(
            case_id=finding_data["case_id"],
            subject_id=finding_data.get("subject_id"),
            title=finding_data["title"][:300],
            content=finding_data["content"],
            source_url=finding_data.get("source_url"),
            source_type="spiderfoot",
            reliability_score=finding_data.get("reliability_score", 7),
            confidence_level=finding_data.get("confidence_level", "medium"),
            finding_type=finding_data.get("finding_type", "general"),
            tags=finding_data.get("tags", ["spiderfoot"]),
            created_by=current_user.id,
        )

        db.session.add(finding)
        imported_count += 1

    AuditLog.log(
        user_id=current_user.id,
        action="spiderfoot_import",
        entity_type="spiderfoot_scan",
        entity_id=scan_record.id,
        ip_address=request.remote_addr,
        case_id=scan_record.case_id,
        description=f"Imported {imported_count} findings from SpiderFoot scan",
    )
    db.session.commit()

    if request.is_json:
        return jsonify(
            {
                "message": f"Imported {imported_count} findings",
                "imported": imported_count,
                "skipped": skipped_count,
            }
        )

    flash(f"Imported {imported_count} findings.", "success")
    return redirect(url_for("cms.view_case", case_id=scan_record.case_id))


@cms_bp.route("/spiderfoot/scans")
@login_required
@roles_required("admin", "senior_investigator")
def spiderfoot_scans() -> str:
    """List all SpiderFoot scans."""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    status = request.args.get("status", "")
    search = request.args.get("search", "")

    query = apply_tenant_filter(
        SpiderFootScan.query.filter_by(is_deleted=False), SpiderFootScan
    )

    if status:
        query = query.filter_by(status=status)

    if search:
        query = query.filter(
            db.or_(
                SpiderFootScan.scan_name.ilike(f"%{search}%"),
                SpiderFootScan.target_value.ilike(f"%{search}%"),
            )
        )

    pagination = query.order_by(SpiderFootScan.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        "cms/spiderfoot/list.html",
        scans=pagination.items,
        pagination=pagination,
        filters={"status": status, "search": search},
    )


@cms_bp.route("/spiderfoot/settings", methods=["GET", "POST"])
@login_required
@admin_required
@validate(SpiderFootSettingsSchema)
def spiderfoot_settings() -> str:
    """Manage SpiderFoot settings."""
    from ..tier_limits import check_feature

    if not check_feature("spiderfoot"):
        flash("SpiderFoot is not available on your current plan.", "warning")
        return redirect(url_for("cms.settings", category="plan"))
    if request.method == "POST":
        data = request.validated_data

        # Update settings
        Setting.set(
            "spiderfoot_url",
            data.get("url", "http://localhost:5001"),
            description="SpiderFoot server URL",
            category="spiderfoot",
        )
        Setting.set(
            "spiderfoot_username",
            data.get("username", "admin"),
            description="SpiderFoot login username",
            category="spiderfoot",
        )
        Setting.set(
            "spiderfoot_password",
            data.get("password", ""),
            description="SpiderFoot login password",
            category="spiderfoot",
            encrypt=True,
        )

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="settings",
            entity_id="spiderfoot",
            ip_address=request.remote_addr,
            description="Updated SpiderFoot settings",
        )
        db.session.commit()

        if request.is_json:
            return api_success({}, "Settings saved")

        flash("SpiderFoot settings saved.", "success")
        return redirect(url_for("cms.spiderfoot_settings"))

    # GET - Show settings form
    from ..setting_cache import cached_setting_get

    settings = {
        "url": cached_setting_get("spiderfoot_url", "http://localhost:5001"),
        "username": cached_setting_get("spiderfoot_username", "admin"),
        "password": cached_setting_get("spiderfoot_password", ""),
    }

    # Test connection
    sf_service = get_spiderfoot_service()
    connection_ok = sf_service.is_available()
    server_info = sf_service.get_server_info() if connection_ok else None

    return render_template(
        "cms/spiderfoot/settings.html",
        settings=settings,
        connection_ok=connection_ok,
        server_info=server_info,
    )


@cms_bp.route("/spiderfoot/settings/test", methods=["POST"])
@login_required
@admin_required
@validate(SpiderFootTestSchema)
def spiderfoot_test_connection() -> flask.Response:
    """Test SpiderFoot connection."""
    data = request.validated_data

    url = data.get("url", "http://localhost:5001")
    username = data.get("username", "admin")
    password = data.get("password", "")

    from ..spiderfoot_service import SpiderFootConfig

    config = SpiderFootConfig(base_url=url, username=username, password=password)
    service = SpiderFootService(config)

    if service.is_available():
        info = service.get_server_info()
        return jsonify(
            {"success": True, "message": "Connection successful", "server_info": info}
        )
    else:
        return jsonify(
            {"success": False, "message": "Could not connect to SpiderFoot server"}
        ), 400


@cms_bp.route("/api/spiderfoot/status")
@login_required
@roles_required("admin", "senior_investigator")
def api_spiderfoot_status() -> flask.Response:
    """Get SpiderFoot server status."""
    sf_service = get_spiderfoot_service()
    available = sf_service.is_available()
    info = sf_service.get_server_info() if available else None

    # Count scans
    scan_counts = {
        "total": apply_tenant_filter(
            SpiderFootScan.query.filter_by(is_deleted=False), SpiderFootScan
        ).count(),
        "running": apply_tenant_filter(
            SpiderFootScan.query.filter_by(status="running", is_deleted=False),
            SpiderFootScan,
        ).count(),
        "completed": apply_tenant_filter(
            SpiderFootScan.query.filter_by(status="completed", is_deleted=False),
            SpiderFootScan,
        ).count(),
    }

    return jsonify(
        {"available": available, "server_info": info, "scan_counts": scan_counts}
    )


@cms_bp.route("/spiderfoot/subject/<subject_id>/scan", methods=["GET", "POST"])
@login_required
@roles_required("admin", "senior_investigator")
@validate(SpiderFootScanSubjectSchema)
def spiderfoot_scan_subject(subject_id: str) -> str:
    """Scan a subject with SpiderFoot."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    ensure_tenant_access(subject)
    subject.decrypt_identifiers()

    if request.method == "POST":
        data = request.validated_data

        from ..tier_limits import check_feature, check_concurrent_spiderfoot_scans

        if not check_feature("spiderfoot"):
            if request.is_json:
                return api_error(
                    "SpiderFoot is not available on your current plan. Upgrade to access this feature.",
                    403,
                )
            flash("SpiderFoot is not available on your current plan.", "warning")
            return redirect(url_for("cms.settings", category="plan"))

        ok, running, maximum = check_concurrent_spiderfoot_scans()
        if not ok:
            if request.is_json:
                return api_error(
                    f"Maximum concurrent SpiderFoot scans reached ({running}/{maximum}). Please wait for a scan to finish.",
                    429,
                )
            flash(
                f"Maximum concurrent SpiderFoot scans reached ({running}/{maximum}). Please wait for a scan to finish.",
                "warning",
            )
            return redirect(url_for("cms.spiderfoot_index"))

        profile = data.get("profile", "basic")
        use_case = data.get("use_case", "passive")
        case_id = data.get("case_id")

        # Try to determine target from subject
        target_info = ScanTarget.from_subject(subject.to_dict())

        if not target_info:
            if request.is_json:
                return jsonify(
                    {"error": "Could not determine scan target from subject"}
                ), 400
            flash("Could not determine scan target from subject type.", "error")
            return redirect(url_for("cms.view_subject", subject_id=subject_id))

        sf_service = get_spiderfoot_service()

        if not sf_service.is_available():
            if request.is_json:
                return jsonify({"error": "SpiderFoot server is not available"}), 503
            flash("SpiderFoot server is not available.", "error")
            return redirect(url_for("cms.spiderfoot_index"))

        # Start scan
        result = sf_service.start_scan(
            target=target_info.value,
            target_type=target_info.target_type,
            scan_name=f"Scan - {subject.name}",
            use_case=use_case,
            profile=profile,
        )

        if not result or not result.get("scan_id"):
            if request.is_json:
                return jsonify({"error": "Failed to start scan"}), 500
            flash("Failed to start SpiderFoot scan.", "error")
            return redirect(url_for("cms.spiderfoot_index"))

        # Create scan record
        scan_record = SpiderFootScan(
            scan_id=result["scan_id"],
            scan_name=f"Scan - {subject.name}",
            target_value=target_info.value,
            target_type=target_info.target_type,
            case_id=case_id,
            subject_id=subject_id,
            use_case=use_case,
            profile=profile,
            module_ids=SpiderFootService.INVESTIGATION_PROFILES.get(profile, {}).get(
                "modules", []
            )
            if (SpiderFootService and profile)
            else [],
            status="running",
            created_by=current_user.id,
        )
        scan_record.update_status("running", 0)

        db.session.add(scan_record)

        AuditLog.log(
            user_id=current_user.id,
            action="spiderfoot_scan_start",
            entity_type="spiderfoot_scan",
            entity_id=scan_record.id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Started SpiderFoot scan for subject: {subject.name} (subject={subject_id})",
        )
        db.session.commit()

        if request.is_json:
            return jsonify(
                {"message": "Scan started", "scan": scan_record.to_dict()}
            ), 201

        flash("SpiderFoot scan started.", "success")
        return redirect(url_for("cms.spiderfoot_scan_status", scan_id=scan_record.id))

    # GET - Show scan form with subject info
    cases = (
        apply_tenant_filter(Case.query.filter_by(is_deleted=False), Case)
        .order_by(Case.case_number.desc())
        .limit(500)
        .all()
    )

    return render_template(
        "cms/spiderfoot/scan_subject.html",
        subject=subject,
        cases=cases,
        profiles=SpiderFootService.INVESTIGATION_PROFILES if SpiderFootService else {},
        use_cases=SpiderFootService.USE_CASES if SpiderFootService else {},
    )
