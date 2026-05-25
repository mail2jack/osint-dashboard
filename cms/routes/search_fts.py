import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_, func

from . import cms_bp
from .. import csrf
from ..models import db, Subject, Case, Finding
from ..validation import validate, FTSSearchSchema
from ..api_key_auth import api_key_required

logger = logging.getLogger(__name__)


def _fts_query(model, columns, query: str, limit: int = 20) -> list:
    """Cross-database full-text search using ILIKE (works on PostgreSQL + SQLite)."""
    conditions = [col.ilike(f'%{query}%') for col in columns if col is not None]
    if not conditions:
        return []
    return model.query.filter(or_(*conditions)).order_by(
        model.updated_at.desc() if hasattr(model, 'updated_at') else model.created_at.desc()
    ).limit(limit).all()


@cms_bp.route('/api/search/fts', methods=['POST'])
@csrf.exempt
@api_key_required
@login_required
@validate(FTSSearchSchema)
def full_text_search() -> flask.Response:
    """Full-text search across subjects, cases, and findings."""
    data = request.validated_data
    query = data.get('query', '').strip()
    scope = data.get('scope', 'all')
    limit = min(int(data.get('limit', 20)), 100)

    if not query or len(query) < 2:
        return jsonify({'error': 'Query must be at least 2 characters'}), 400

    results = {}

    if scope in ('all', 'subjects'):
        subjects = _fts_query(Subject, [
            Subject.name,
            Subject.email,
            Subject.phone,
            Subject.identification_number,
            Subject.license_plate,
            Subject.notes,
        ], query, limit)
        results['subjects'] = [s.to_dict() for s in subjects]

    if scope in ('all', 'cases'):
        cases = _fts_query(Case, [
            Case.title,
            Case.description,
            Case.case_number,
        ], query, limit)
        results['cases'] = [c.to_dict() for c in cases]

    if scope in ('all', 'findings'):
        findings = _fts_query(Finding, [
            Finding.title,
            Finding.content,
            Finding.source_url,
        ], query, limit)
        results['findings'] = [f.to_dict() for f in findings]

    results['query'] = query
    results['scope'] = scope
    results['total'] = sum(len(v) for k, v in results.items() if isinstance(v, list))
    return jsonify(results), 200
