import threading
import uuid
import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..validation import validate, StartOSINTSearchSchema, AddOSINTFindingsSchema
from ..models import db, Case, AuditLog, Finding
from ..auth import case_access_required
from ..search_manager import search_manager

logger = logging.getLogger(__name__)

def run_osint_search(search_id: str, case_id: str, query: str, name: str) -> None:
    """Run OSINT search in background thread."""
    from cms.services.search_service import person_dorks_search

    search_info = search_manager.get_search(search_id)
    if not search_info:
        return

    cancel_event = search_info['cancel_event']
    results = None

    try:
        logger.info(f"OSINT search {search_id} started for query: {name}")

        # Run the dorks search
        results = person_dorks_search(name)

        # Check if cancelled before setting results
        if cancel_event and cancel_event.is_set():
            logger.info(f"OSINT search {search_id} was cancelled")
            search_manager.cleanup(search_id)
            return

        # Count results
        total_results = 0
        if results and 'categories' in results:
            for cat, items in results.get('categories', {}).items():
                total_results += len(items) if items else 0

        # Persist to DB
        search_manager.set_results(search_id, results)
        logger.info(
            f"OSINT search {search_id} completed with {total_results} dork results, {len(results.get('search_links', []))} search links")

    except Exception as e:
        logger.error(f"OSINT search {search_id} failed ({type(e).__name__}): {str(e)}")
        logger.exception(e)
        search_manager.set_error(search_id, str(e))
    finally:
        def delayed_cleanup():
            import time
            time.sleep(300)
            search_manager.cleanup(search_id)

        cleanup_thread = threading.Thread(
            target=delayed_cleanup, daemon=True)
        cleanup_thread.start()


@cms_bp.route('/cases/<case_id>/osint-search', methods=['POST'])
@csrf.exempt
@login_required
@case_access_required
@validate(StartOSINTSearchSchema)
def start_osint_search(case_id: str) -> flask.Response:
    """Start a background OSINT search for a person."""
    db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400

    if len(name.split()) < 2:
        return jsonify({'error': 'Please enter a full name (first and last name)'}), 400

    # Create search
    search_id = str(uuid.uuid4())
    search_manager.create_search(case_id, search_id, name)

    # Log the search start
    AuditLog.log(
        user_id=current_user.id,
        action='osint_search_start',
        entity_type='case',
        entity_id=case_id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Started OSINT search for: {name}"
    )
    db.session.commit()

    # Start background thread
    thread = threading.Thread(
        target=run_osint_search,
        args=(search_id, case_id, name, name),
        daemon=True
    )
    thread.start()

    return jsonify({
        'search_id': search_id,
        'status': 'started',
        'message': f'Search started for: {name}'
    })


@cms_bp.route('/osint-search/<search_id>/status')
@login_required
def get_search_status(search_id: str) -> flask.Response:
    """Get the status of a background search."""
    status = search_manager.get_status(search_id)

    if not status:
        return jsonify({'error': 'Search not found'}), 404

    return jsonify({
        'search_id': search_id,
        **status
    })


@cms_bp.route('/osint-search/<search_id>/cancel', methods=['POST'])
@csrf.exempt
@login_required
def cancel_search(search_id: str) -> flask.Response:
    """Cancel a running search."""
    search_info = search_manager.get_search(search_id)

    if not search_info:
        return jsonify({'error': 'Search not found'}), 404

    if search_info['status'] == 'completed':
        return jsonify({
            'search_id': search_id,
            'status': 'completed',
            'message': 'Search already completed'
        })

    if search_info['status'] == 'cancelled':
        return jsonify({
            'search_id': search_id,
            'status': 'cancelled',
            'message': 'Search already cancelled'
        })

    search_manager.cancel_search(search_id)

    # Log cancellation
    AuditLog.log(
        user_id=current_user.id,
        action='osint_search_cancel',
        entity_type='osint_search',
        entity_id=search_id,
        ip_address=request.remote_addr,
        case_id=search_info.get('case_id'),
        description=f"Cancelled OSINT search for: {search_info.get('query')}"
    )
    db.session.commit()

    # Cleanup
    search_manager.cleanup(search_id)

    return jsonify({
        'search_id': search_id,
        'status': 'cancelled',
        'message': 'Search cancelled'
    })


@cms_bp.route('/osint-search/<search_id>/results')
@login_required
def get_search_results(search_id: str):
    """Get results from a completed search."""
    status = search_manager.get_status(search_id)

    if not status:
        return jsonify({'error': 'Search not found'}), 404

    if status['status'] == 'running':
        return jsonify({
            'search_id': search_id,
            'status': 'running',
            'results': None
        })

    return jsonify({
        'search_id': search_id,
        'status': status['status'],
        'results': status.get('results'),
        'completed_at': status.get('completed_at')
    })


@cms_bp.route('/cases/<case_id>/osint-search/add-findings', methods=['POST'])
@csrf.exempt
@login_required
@case_access_required
@validate(AddOSINTFindingsSchema)
def add_osint_findings(case_id: str):
    """Add selected OSINT results as findings to a case."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    selected_results = data.get('results', [])
    if not selected_results:
        return jsonify({'error': 'No results selected'}), 400

    subject_id = data.get('subject_id')
    created_findings = []

    # Batch dedup: collect URLs, check which ones already exist
    from datetime import timedelta
    all_urls = [r.get('url', '') for r in selected_results if r.get('url')]
    existing_urls = set()
    if all_urls:
        dup_check = Finding.query.filter(
            Finding.case_id == case_id,
            Finding.source_url.in_(all_urls),
            Finding.created_at >= datetime.now(timezone.utc) - timedelta(seconds=60),
            Finding.is_deleted == False
        ).with_entities(Finding.source_url).all()
        existing_urls = {row[0] for row in dup_check}

    for result in selected_results:
        domain = result.get('domain', 'Unknown')
        query = result.get('query', '')
        source = result.get('source', '')
        category = result.get('category', 'general')

        if source == 'search_link':
            title = f"OSINT: {domain} - Search Link"
        elif query:
            # Extract key part of query (first 40 chars max)
            query_short = query[:40] + "..." if len(query) > 40 else query
            title = f"OSINT: {domain} - {query_short}"
        else:
            title = f"OSINT: {domain}"

        # Create content with full details
        content_parts = []
        if query:
            content_parts.append(f"Search Query: {query}")
        content_parts.append(
            f"Source: {source.upper() if source else 'Unknown'}")
        content_parts.append(f"URL: {result.get('url', 'N/A')}")
        content = '\n'.join(content_parts)

        # Build tags
        tags = ['osint', source.lower() if source else 'unknown']
        if category:
            tags.append(category.lower())
        if domain:
            tags.append(domain.split('.')[0])

        # Dedup: skip if same URL saved within last 60 seconds (batch-checked above)
        url = result.get('url', '')
        if url and url in existing_urls:
            continue

        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=title,
            content=content,
            source_url=result.get('url', ''),
            source_type='osint',
            finding_type='identity',
            reliability_score=5,
            confidence_level='medium',
            created_by=current_user.id,
            tags=tags
        )

        db.session.add(finding)
        created_findings.append(finding)

    # Log the action
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='finding',
        entity_id=None,
        ip_address=request.remote_addr,
        case_id=case_id,
        new_values={'count': len(created_findings), 'source': 'osint_search'},
        description=f"Added {len(created_findings)} OSINT findings to case {case.case_number}"
    )
    db.session.commit()

    return jsonify({
        'message': f'{len(created_findings)} findings added',
        'findings': [f.to_dict() for f in created_findings]
    }), 201
