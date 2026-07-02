import logging

import flask
from flask import request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_

from . import cms_bp
from .. import csrf
from ..models import db, Subject, Case, Finding, case_subjects
from ..validation import validate, FTSSearchSchema
from ..api_key_auth import api_key_required
from ..auth import get_accessible_case_ids, apply_tenant_filter

from .response import api_error

logger = logging.getLogger(__name__)


def _fts_query(model, columns, query: str, limit: int = 20) -> list:
    """Cross-database full-text search using ILIKE (works on PostgreSQL + SQLite)."""
    conditions = [col.ilike(f"%{query}%") for col in columns if col is not None]
    if not conditions:
        return []
    return (
        apply_tenant_filter(
            model.query.filter(or_(*conditions)),
            model,
        )
        .order_by(
            model.updated_at.desc()
            if hasattr(model, "updated_at")
            else model.created_at.desc()
        )
        .limit(limit)
        .all()
    )


@cms_bp.route("/api/search/fts", methods=["POST"])
@csrf.exempt
@api_key_required
@login_required
@validate(FTSSearchSchema)
def full_text_search() -> flask.Response:
    """Full-text search across subjects, cases, and findings."""
    data = request.validated_data
    query = data.get("query", "").strip()
    scope = data.get("scope", "all")
    limit = min(int(data.get("limit", 20)), 100)

    if not query or len(query) < 2:
        return api_error("Query must be at least 2 characters", 400)

    accessible_ids = set(get_accessible_case_ids(current_user))
    results = {}

    if scope in ("all", "subjects"):
        subjects = _fts_query(
            Subject,
            [
                Subject.name,
                Subject.email,
                Subject.phone,
                Subject.identification_number,
                Subject.license_plate,
                Subject.notes,
            ],
            query,
            limit,
        )
        if current_user.is_admin:
            filtered_subjects = subjects
        elif subjects:
            subject_ids = [s.id for s in subjects]
            subj_case_rows = (
                db.session.query(case_subjects.c.subject_id, case_subjects.c.case_id)
                .filter(
                    case_subjects.c.subject_id.in_(subject_ids),
                    case_subjects.c.case_id.in_(accessible_ids),
                )
                .all()
            )
            allowed_subject_ids = set(row[0] for row in subj_case_rows)
            filtered_subjects = [s for s in subjects if s.id in allowed_subject_ids]
        else:
            filtered_subjects = []
        results["subjects"] = [s.to_dict(decrypted=False) for s in filtered_subjects]

    if scope in ("all", "cases"):
        cases = _fts_query(
            Case,
            [
                Case.title,
                Case.description,
                Case.case_number,
            ],
            query,
            limit,
        )
        filtered_cases = (
            [c for c in cases if c.id in accessible_ids]
            if not current_user.is_admin
            else cases
        )
        results["cases"] = [c.to_dict() for c in filtered_cases]

    if scope in ("all", "findings"):
        findings = _fts_query(
            Finding,
            [
                Finding.title,
                Finding.content,
                Finding.source_url,
            ],
            query,
            limit,
        )
        filtered_findings = (
            [f for f in findings if f.case_id in accessible_ids]
            if not current_user.is_admin
            else findings
        )
        results["findings"] = [f.to_dict() for f in filtered_findings]

    results["query"] = query
    results["scope"] = scope
    results["total"] = sum(len(v) for k, v in results.items() if isinstance(v, list))
    return jsonify(results), 200
