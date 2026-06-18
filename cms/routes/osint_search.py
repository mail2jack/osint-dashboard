import threading
import uuid
import logging
import concurrent.futures
from datetime import datetime, timezone

import flask
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..validation import validate, StartOSINTSearchSchema, AddOSINTFindingsSchema
from ..models import db, Case, Subject, AuditLog, Finding
from ..auth import case_access_required
from ..search_manager import search_manager

from .response import api_error

logger = logging.getLogger(__name__)


def run_osint_search(
    search_id: str, case_id: str, query: str, name: str, _app=None
) -> None:
    """Run OSINT search in background thread."""
    from cms.services.search_service import person_dorks_search

    ctx = _app.app_context() if _app else None
    if ctx:
        ctx.push()

    try:
        search_info = search_manager.get_search(search_id)
        if not search_info:
            return

        cancel_event = search_info["cancel_event"]
        db_row = search_info.get("db_row")
        subject_id = db_row.subject_id if db_row else None
        results = None
        logger.info(f"OSINT search {search_id} started for query: {name}")

        # Start SpiderFoot scan in a background thread with 30s timeout
        # so the dorks search below starts immediately regardless of SF speed
        def _init_sf():
            ctx2 = _app.app_context() if _app else None
            if ctx2:
                ctx2.push()
            try:
                SettingModel = __import__("cms.models", fromlist=["Setting"]).Setting
                sf_url = SettingModel.get("spiderfoot_url")
                if not sf_url:
                    search_manager.set_sf_status(search_id, "failed")
                    return
                search_manager.set_sf_status(search_id, "running")
                sf_user = SettingModel.get("spiderfoot_username", "admin") or "admin"
                sf_pass = SettingModel.get("spiderfoot_password", "") or ""

                from cms.spiderfoot_service import (
                    SpiderFootService,
                    SpiderFootConfig,
                    ScanTarget,
                )

                sf_service = SpiderFootService(
                    SpiderFootConfig(
                        base_url=sf_url,
                        username=sf_user,
                        password=sf_pass,
                    )
                )

                if not sf_service.is_available():
                    search_manager.set_sf_status(search_id, "failed")
                    return

                sf_target_obj = None
                if subject_id:
                    subject_row = db.session.get(Subject, subject_id)
                    if subject_row:
                        sf_target_obj = ScanTarget.from_subject(subject_row.to_dict())

                if not sf_target_obj:
                    # No subject context — use quoted name as HUMAN_NAME target
                    target = f'"{name}"'
                    target_type = "HUMAN_NAME"
                else:
                    target = sf_target_obj.value
                    target_type = sf_target_obj.target_type

                scan_result = sf_service.start_scan(
                    target=target,
                    target_type=target_type,
                    scan_name=f"OSINT Search: {name}",
                    use_case="passive",
                    profile="investigation",
                )

                if scan_result and scan_result.get("scan_id"):
                    search_manager.set_sf_scan(search_id, scan_result["scan_id"])
                    logger.info(
                        f"OSINT search {search_id}: started SF scan {scan_result['scan_id']} for {target} ({target_type})"
                    )
                else:
                    search_manager.set_sf_status(search_id, "failed")
            except Exception:
                logger.warning(
                    "OSINT search %s: SF scan init failed", search_id, exc_info=True
                )
                try:
                    search_manager.set_sf_status(search_id, "failed")
                except Exception:
                    pass
            finally:
                if ctx2:
                    ctx2.pop()

        sf_thread = threading.Thread(target=_init_sf, daemon=True)
        sf_thread.start()
        sf_thread.join(timeout=30)
        if sf_thread.is_alive():
            logger.warning("OSINT search %s: SF init timed out after 30s", search_id)
            search_manager.set_sf_status(search_id, "failed")

        # Run the dorks search with a 2-minute total timeout
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(person_dorks_search, name)
            try:
                results = future.result(timeout=120)
            except concurrent.futures.TimeoutError:
                logger.warning(f"OSINT search {search_id} timed out after 120s")
                search_manager.set_error(search_id, "Search timed out after 2 minutes")
                return

            # Check if cancelled before setting results
            if cancel_event and cancel_event.is_set():
                logger.info(f"OSINT search {search_id} was cancelled")
                search_manager.cleanup(search_id)
                return

            total_results = results.get("total_results", 0) if results else 0

            # Persist to DB
            search_manager.set_results(search_id, results)
            logger.info(
                f"OSINT search {search_id} completed with {total_results} dork results, {len(results.get('search_links', [])) if results else 0} search links"
            )
        finally:
            executor.shutdown(wait=False)

    except Exception:
        logger.exception("OSINT search %s failed", search_id)
        try:
            search_manager.set_error(search_id, "Search failed")
        except Exception:
            logger.error("Failed to persist search error to DB")
    finally:
        if ctx:
            ctx.pop()

        def delayed_cleanup():
            import time

            time.sleep(300)
            search_manager.cleanup(search_id)

        cleanup_thread = threading.Thread(target=delayed_cleanup, daemon=True)
        cleanup_thread.start()


@cms_bp.route("/cases/<case_id>/osint-search", methods=["POST"])
@login_required
@case_access_required
@validate(StartOSINTSearchSchema)
def start_osint_search(case_id: str) -> flask.Response:
    """Start a background OSINT search for a person."""
    db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    name = data.get("name", "").strip()
    if not name:
        return api_error("Name is required", 400)

    if len(name.split()) < 2:
        return api_error("Please enter a full name (first and last name)", 400)

    subject_id = data.get("subject_id")

    # Create search
    search_id = str(uuid.uuid4())
    search_manager.create_search(case_id, search_id, name, subject_id=subject_id)

    # Log the search start
    AuditLog.log(
        user_id=current_user.id,
        action="osint_search_start",
        entity_type="case",
        entity_id=case_id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Started OSINT search for: {name}",
    )
    db.session.commit()

    # Start background thread with app context
    from flask import current_app

    _app = current_app._get_current_object()
    thread = threading.Thread(
        target=run_osint_search,
        args=(search_id, case_id, name, name, _app),
        daemon=True,
    )
    thread.start()

    return jsonify(
        {
            "search_id": search_id,
            "status": "started",
            "message": f"Search started for: {name}",
        }
    )


@cms_bp.route("/osint-search/<search_id>/status")
@login_required
def get_search_status(search_id: str) -> flask.Response:
    """Get the status of a background search."""
    status = search_manager.get_status(search_id)

    if not status:
        return api_error("Search not found", 404)

    return jsonify({"search_id": search_id, **status})


@cms_bp.route("/osint-search/<search_id>/cancel", methods=["POST"])
@login_required
def cancel_search(search_id: str) -> flask.Response:
    """Cancel a running search."""
    search_info = search_manager.get_search(search_id)

    if not search_info:
        return api_error("Search not found", 404)

    if search_info["status"] == "completed":
        return jsonify(
            {
                "search_id": search_id,
                "status": "completed",
                "message": "Search already completed",
            }
        )

    if search_info["status"] == "cancelled":
        return jsonify(
            {
                "search_id": search_id,
                "status": "cancelled",
                "message": "Search already cancelled",
            }
        )

    search_manager.cancel_search(search_id)

    AuditLog.log(
        user_id=current_user.id,
        action="osint_search_cancel",
        entity_type="osint_search",
        entity_id=search_id,
        ip_address=request.remote_addr,
        case_id=search_info.get("case_id"),
        description=f"Cancelled OSINT search for: {search_info.get('query')}",
    )
    db.session.commit()

    search_manager.cleanup(search_id)

    return jsonify(
        {"search_id": search_id, "status": "cancelled", "message": "Search cancelled"}
    )


@cms_bp.route("/osint-search/<search_id>/results")
@login_required
def get_search_results(search_id: str) -> flask.Response:
    """Get results from a completed search."""
    status = search_manager.get_status(search_id)

    if not status:
        return api_error("Search not found", 404)

    if status["status"] == "running":
        return jsonify({"search_id": search_id, "status": "running", "results": None})

    return jsonify(
        {
            "search_id": search_id,
            "status": status["status"],
            "results": status.get("results"),
            "completed_at": status.get("completed_at"),
            "spiderfoot_scan_id": status.get("spiderfoot_scan_id"),
            "sf_status": status.get("sf_status"),
        }
    )


@cms_bp.route("/osint-search/<search_id>/sf-results")
@login_required
def get_osint_sf_results(search_id: str) -> flask.Response:
    """Get SpiderFoot results linked to an OSINT search.

    Returns normalized results in the same format as the OSINT search results,
    so the frontend can display them alongside Brave/DDG results.
    """
    status = search_manager.get_status(search_id)
    if not status:
        return api_error("Search not found", 404)

    sf_scan_id = status.get("spiderfoot_scan_id")
    if not sf_scan_id:
        return jsonify({"status": "unavailable", "results": []})

    sf_status = status.get("sf_status")
    if sf_status == "completed":
        # Results already fetched and cached — return them from the stored search
        stored_results = status.get("results") or {}
        sf_results = stored_results.get("sf_results", [])
        return jsonify({"status": "completed", "results": sf_results})

    if sf_status == "failed":
        return jsonify({"status": "failed", "results": []})

    # Fetch status from SF server
    try:
        SettingModel = __import__("cms.models", fromlist=["Setting"]).Setting
        sf_url = SettingModel.get("spiderfoot_url")
        if not sf_url:
            return jsonify({"status": "unavailable", "results": []})

        sf_user = SettingModel.get("spiderfoot_username", "admin") or "admin"
        sf_pass = SettingModel.get("spiderfoot_password", "") or ""

        from cms.spiderfoot_service import SpiderFootService, SpiderFootConfig

        sf_service = SpiderFootService(
            SpiderFootConfig(
                base_url=sf_url,
                username=sf_user,
                password=sf_pass,
            )
        )

        if not sf_service.is_available():
            search_manager.set_sf_status(search_id, "failed")
            return jsonify(
                {"status": "failed", "results": [], "error": "SF unavailable"}
            )

        scan_status = sf_service.get_scan_status(sf_scan_id)
        if not scan_status:
            search_manager.set_sf_status(search_id, "failed")
            return jsonify(
                {"status": "failed", "results": [], "error": "Scan not found"}
            )

        sf_state = (scan_status.get("status") or "").lower()

        if sf_state in ("running", "pending", "starting"):
            progress = scan_status.get("progress", 0)
            return jsonify({"status": "running", "progress": progress})

        if sf_state in ("failed", "error", "aborted"):
            search_manager.set_sf_status(search_id, "failed")
            return jsonify({"status": "failed", "results": []})

        # Completed
        raw_results = sf_service.get_scan_results(sf_scan_id, limit=5000)
        normalized = sf_service.normalize_results(raw_results or [])

        # Convert to OSINT search result format
        sf_results = [sf_service.normalize_sf_result_for_osint(r) for r in normalized]

        # Cache results in the OsintSearch record
        row = (
            db.session.query(
                __import__("cms.models", fromlist=["OsintSearch"]).OsintSearch
            )
            .filter_by(search_id=search_id)
            .first()
        )
        if row:
            if not row.results:
                row.results = {}
            if isinstance(row.results, dict):
                row.results["sf_results"] = sf_results
            row.sf_status = "completed"
            db.session.commit()

        return jsonify({"status": "completed", "results": sf_results})

    except Exception:
        logger.exception("SF results fetch failed for search %s", search_id)
        search_manager.set_sf_status(search_id, "failed")
        return jsonify({"status": "failed", "results": [], "error": "SF fetch failed"})


@cms_bp.route("/cases/<case_id>/osint-search/add-findings", methods=["POST"])
@csrf.exempt
@login_required
@case_access_required
@validate(AddOSINTFindingsSchema)
def add_osint_findings(case_id: str) -> flask.Response:
    """Add selected OSINT results as findings to a case.

    Supports both regular OSINT results (Brave/DDG) and SpiderFoot results.
    Detects result type by the presence of 'sf_type' field.
    """
    case = db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    if not data:
        return api_error("No data provided", 400)

    selected_results = data.get("results", [])
    if not selected_results:
        return api_error("No results selected", 400)

    subject_id = data.get("subject_id")
    created_findings = []

    # Batch dedup: collect URLs, check which ones already exist
    from datetime import timedelta

    all_urls = [r.get("url", "") for r in selected_results if r.get("url")]
    existing_urls = set()
    if all_urls:
        dup_check = (
            Finding.query.filter(
                Finding.case_id == case_id,
                Finding.source_url.in_(all_urls),
                Finding.created_at
                >= datetime.now(timezone.utc) - timedelta(seconds=60),
                Finding.is_deleted == False,
            )
            .with_entities(Finding.source_url)
            .all()
        )
        existing_urls = {row[0] for row in dup_check}

    for result in selected_results:
        url = result.get("url", "")
        if url and url in existing_urls:
            continue

        # Check if this is a SpiderFoot result
        if result.get("sf_type"):
            sf_type = result["sf_type"]
            sf_module = result.get("sf_module", "Unknown")
            query_text = result.get("query", "")

            finding_type_map = {
                "EMAILADDR": "identity",
                "PHONE_NUMBER": "identity",
                "SOCIAL_MEDIA": "connection",
                "ACCOUNT": "connection",
                "USERNAME": "identity",
                "BREACH": "breach",
                "DOMAIN_NAME": "network",
                "IP_ADDRESS": "network",
                "LEAKS": "breach",
            }

            finding = Finding(
                case_id=case_id,
                subject_id=subject_id,
                title=f"[SpiderFoot] {sf_type}: {query_text[:100]}"
                if query_text
                else f"[SpiderFoot] {sf_type}",
                content=(
                    f"Source: SpiderFoot\n"
                    f"Type: {sf_type}\n"
                    f"Module: {sf_module}\n"
                    f"Data: {query_text}"
                ),
                source_url=url,
                source_type="spiderfoot",
                finding_type=finding_type_map.get(sf_type, "identity"),
                reliability_score=7,
                confidence_level="medium",
                created_by=current_user.id,
                tags=["spiderfoot", sf_type.lower()],
            )
        else:
            # Regular OSINT result (Brave/DDG)
            domain = result.get("domain", "Unknown")
            query = result.get("query", "")
            source = result.get("source", "")
            category = result.get("category", "general")

            if source == "search_link":
                title = f"OSINT: {domain} - Search Link"
            elif query:
                query_short = query[:40] + "..." if len(query) > 40 else query
                title = f"OSINT: {domain} - {query_short}"
            else:
                title = f"OSINT: {domain}"

            content_parts = []
            if query:
                content_parts.append(f"Search Query: {query}")
            content_parts.append(f"Source: {source.upper() if source else 'Unknown'}")
            content_parts.append(f"URL: {url or 'N/A'}")
            content = "\n".join(content_parts)

            tags = ["osint", source.lower() if source else "unknown"]
            if category:
                tags.append(category.lower())
            if domain:
                tags.append(domain.split(".")[0])

            finding = Finding(
                case_id=case_id,
                subject_id=subject_id,
                title=title,
                content=content,
                source_url=url,
                source_type="osint",
                finding_type="identity",
                reliability_score=5,
                confidence_level="medium",
                created_by=current_user.id,
                tags=tags,
            )

        db.session.add(finding)
        created_findings.append(finding)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="finding",
        entity_id=None,
        ip_address=request.remote_addr,
        case_id=case_id,
        new_values={"count": len(created_findings), "source": "osint_search"},
        description=f"Added {len(created_findings)} OSINT findings to case {case.case_number}",
    )
    db.session.commit()

    return jsonify(
        {
            "message": f"{len(created_findings)} findings added",
            "findings": [f.to_dict() for f in created_findings],
        }
    ), 201
