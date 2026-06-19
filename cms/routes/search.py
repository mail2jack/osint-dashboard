import logging

import flask
from flask import request, jsonify, render_template
from flask_login import login_required, current_user

from . import cms_bp
from ..models import (
    db,
    Case,
    Client,
    Subject,
    Finding,
    FinancialRecord,
    Comment,
    AuditLog,
    case_subjects,
)
from ..auth import get_accessible_case_ids, apply_tenant_filter
from ..notifications import notify_search_restricted

logger = logging.getLogger(__name__)


def _accessible_ids():
    """Get cached set of accessible case IDs for current user."""
    return set(get_accessible_case_ids(current_user))


def _record_restricted(
    query, entity_type, total_count, filtered_count, accessible_case_numbers
):
    """Log and notify about restricted search results."""
    if filtered_count >= total_count:
        return  # no restriction
    restricted = total_count - filtered_count
    if restricted <= 0:
        return
    AuditLog.log(
        user_id=current_user.id,
        action="search_restricted",
        entity_type=entity_type,
        ip_address=request.remote_addr,
        description=f"Search '{query}' for {entity_type}: {restricted} result(s) restricted "
        f"in cases: {', '.join(accessible_case_numbers)}",
    )
    db.session.commit()
    owner_names = notify_search_restricted(
        user_id=current_user.id,
        query=query,
        restricted_case_numbers=accessible_case_numbers,
        restricted_count=restricted,
        searching_username=current_user.username,
    )
    owners_str = ", ".join(sorted(owner_names)) if owner_names else ""
    owners_part = f" ({owners_str})" if owners_str else ""
    flask.flash(
        f'🔍 "{query}" is gevonden maar heeft toegangsrestricties. '
        f"Case-eigenaar{owners_part} is op de hoogte gesteld "
        f"en zal indien nodig contact met je opnemen.",
        "warning",
    )


@cms_bp.route("/search")
@login_required
def search() -> str:
    """Global search across all entities with full page results."""
    query = request.args.get("q", "")
    entity_type = request.args.get("type", "all")

    results = {
        "cases": [],
        "clients": [],
        "subjects": [],
        "findings": [],
        "financials": [],
        "comments": [],
        "notes": [],
    }

    if query and len(query) >= 2:
        accessible_ids = _accessible_ids()

        if entity_type in ["all", "cases"]:
            base_q = (
                Case.query.options(db.contains_eager(Case.client))
                .join(Client)
                .filter(
                    Case.is_deleted == False,
                    db.or_(
                        Case.title.ilike(f"%{query}%"),
                        Case.case_number.ilike(f"%{query}%"),
                        Case.description.ilike(f"%{query}%"),
                    ),
                )
            )
            base_q = apply_tenant_filter(base_q, Case)
            total_count = base_q.count()
            cases = base_q.filter(Case.id.in_(accessible_ids)).limit(20).all()
            results["cases"] = [
                {
                    "id": c.id,
                    "title": c.title,
                    "case_number": c.case_number,
                    "status": c.status,
                    "priority": c.priority,
                    "client_name": c.client.name if c.client else None,
                    "created_at": c.created_at.strftime("%Y-%m-%d")
                    if c.created_at
                    else None,
                }
                for c in cases
            ]
            if len(cases) < total_count and not current_user.is_admin:
                restricted = base_q.filter(~Case.id.in_(accessible_ids)).limit(20).all()
                restricted_case_numbers = [
                    c.case_number for c in restricted if c.case_number
                ]
                _record_restricted(
                    query,
                    "cases",
                    total_count,
                    len(cases),
                    restricted_case_numbers,
                )

        if entity_type in ["all", "clients"]:
            clients_q = Client.query.filter(
                Client.is_deleted == False, Client.name.ilike(f"%{query}%")
            )
            if not current_user.is_admin:
                clients_q = clients_q.filter(
                    Client.cases.any(Case.id.in_(accessible_ids))
                )
            results["clients"] = [
                {
                    "id": c.id,
                    "name": c.name,
                    "contact_person": c.contact_person,
                    "is_company": c.is_company,
                    "is_active": c.is_active,
                    "contract_number": c.contract_number,
                }
                for c in clients_q.limit(20).all()
            ]

        if entity_type in ["all", "subjects"]:
            subjects_q = Subject.query.filter(
                Subject.is_deleted == False,
                db.or_(
                    Subject.name.ilike(f"%{query}%"),
                    Subject.identification_number.ilike(f"%{query}%"),
                ),
            )
            subjects_q = apply_tenant_filter(subjects_q, Subject)
            if not current_user.is_admin:
                subjects_q = subjects_q.filter(
                    db.select(case_subjects.c.case_id)
                    .where(
                        case_subjects.c.subject_id == Subject.id,
                        case_subjects.c.case_id.in_(accessible_ids),
                    )
                    .exists()
                )
            results["subjects"] = [
                {
                    "id": s.id,
                    "name": s.name,
                    "subject_type": s.subject_type,
                    "risk_score": s.risk_score,
                    "created_at": s.created_at.strftime("%Y-%m-%d")
                    if s.created_at
                    else None,
                }
                for s in subjects_q.limit(20).all()
            ]

        if entity_type in ["all", "findings"]:
            base_q = (
                Finding.query.options(db.joinedload(Finding.case))
                .join(Case)
                .filter(
                    Finding.is_deleted == False,
                    db.or_(
                        Finding.title.ilike(f"%{query}%"),
                        Finding.content.ilike(f"%{query}%"),
                    ),
                )
            )
            base_q = apply_tenant_filter(base_q, Case)
            total_count = base_q.count()
            findings = base_q.filter(Case.id.in_(accessible_ids)).limit(20).all()
            results["findings"] = [
                {
                    "id": f.id,
                    "title": f.title,
                    "case_id": f.case_id,
                    "case_number": f.case.case_number if f.case else None,
                    "finding_type": f.finding_type,
                    "source_type": f.source_type,
                    "created_at": f.created_at.strftime("%Y-%m-%d")
                    if f.created_at
                    else None,
                }
                for f in findings
            ]
            if len(findings) < total_count and not current_user.is_admin:
                restricted = (
                    Finding.query.join(Case)
                    .filter(
                        Finding.is_deleted == False,
                        ~Case.id.in_(accessible_ids),
                        db.or_(
                            Finding.title.ilike(f"%{query}%"),
                            Finding.content.ilike(f"%{query}%"),
                        ),
                    )
                    .limit(20)
                    .all()
                )
                rcn = list(set(f.case.case_number for f in restricted if f.case))
                _record_restricted(query, "findings", total_count, len(findings), rcn)

        if entity_type in ["all", "financials"]:
            total_financials = (
                FinancialRecord.query.options(db.joinedload(FinancialRecord.case))
                .join(Case)
                .filter(
                    FinancialRecord.is_deleted == False,
                    Case.id.in_(accessible_ids),
                    db.or_(
                        FinancialRecord.description.ilike(f"%{query}%"),
                        FinancialRecord.source_reference.ilike(f"%{query}%"),
                    ),
                )
                .limit(20)
                .all()
            )
            results["financials"] = [
                {
                    "id": f.id,
                    "amount": float(f.amount) if f.amount else 0,
                    "currency": f.currency or "EUR",
                    "case_id": f.case_id,
                    "case_number": f.case.case_number if f.case else None,
                    "transaction_type": f.transaction_type,
                    "transaction_date": f.transaction_date.strftime("%Y-%m-%d")
                    if f.transaction_date
                    else "",
                    "description": f.description[:100] if f.description else None,
                }
                for f in total_financials
            ]

        if entity_type in ["all", "comments"]:
            comments_q = Comment.query.options(db.joinedload(Comment.author)).filter(
                Comment.is_deleted == False,
                Comment.content.ilike(f"%{query}%"),
            )
            if not current_user.is_admin:
                comments_q = comments_q.filter(Comment.case_id.in_(accessible_ids))
            total_comments = comments_q.limit(20).all()

            # Batch-load cases for comments
            comment_case_ids = {c.case_id for c in total_comments if c.case_id}
            comment_cases_map = {}
            if comment_case_ids:
                for _c in Case.query.filter(Case.id.in_(comment_case_ids)).all():
                    comment_cases_map[_c.id] = _c
            results["comments"] = []
            for c in total_comments:
                _case = comment_cases_map.get(c.case_id)
                results["comments"].append(
                    {
                        "id": c.id,
                        "content": (c.content[:200] + "...")
                        if c.content and len(c.content) > 200
                        else (c.content or ""),
                        "comment_type": c.comment_type,
                        "case_id": c.case_id,
                        "subject_id": c.subject_id,
                        "client_id": c.client_id,
                        "case_number": _case.case_number if _case else None,
                        "author_name": c.author.full_name if c.author else "Unknown",
                        "created_at": c.created_at.strftime("%Y-%m-%d")
                        if c.created_at
                        else None,
                    }
                )

        if entity_type in ["all", "notes"]:
            subject_notes_q = Subject.query.filter(
                Subject.is_deleted == False, Subject.notes.ilike(f"%{query}%")
            )
            if not current_user.is_admin:
                subject_notes_q = subject_notes_q.filter(
                    db.select(case_subjects.c.case_id)
                    .where(
                        case_subjects.c.subject_id == Subject.id,
                        case_subjects.c.case_id.in_(accessible_ids),
                    )
                    .exists()
                )
            results["notes"] = [
                {
                    "id": s.id,
                    "name": s.name,
                    "subject_type": s.subject_type,
                    "note_preview": s.notes[:150]
                    + ("..." if len(s.notes) > 150 else "")
                    if s.notes
                    else None,
                    "entity_type": "subject",
                }
                for s in subject_notes_q.limit(10).all()
            ]
            comment_notes_q = Comment.query.options(
                db.joinedload(Comment.author)
            ).filter(
                Comment.is_deleted == False,
                Comment.subject_id.isnot(None),
                Comment.content.ilike(f"%{query}%"),
            )
            if not current_user.is_admin:
                comment_notes_q = comment_notes_q.filter(
                    Comment.case_id.in_(accessible_ids)
                )
            total_comment_notes = (
                comment_notes_q.order_by(Comment.created_at.desc()).limit(10).all()
            )
            # Batch-load subjects for comment notes
            note_subject_ids = {
                c.subject_id for c in total_comment_notes if c.subject_id
            }
            note_subjects_map = {}
            if note_subject_ids:
                for _s in Subject.query.filter(Subject.id.in_(note_subject_ids)).all():
                    note_subjects_map[_s.id] = _s
            for c in total_comment_notes:
                sub = note_subjects_map.get(c.subject_id)
                if sub and not sub.is_deleted:
                    results["notes"].append(
                        {
                            "id": sub.id,
                            "name": sub.name + f" (comment: {c.comment_type})",
                            "subject_type": sub.subject_type,
                            "note_preview": c.content[:150]
                            + ("..." if len(c.content) > 150 else "")
                            if c.content
                            else None,
                            "entity_type": "subject",
                            "comment_date": c.created_at.isoformat()
                            if c.created_at
                            else None,
                        }
                    )

        AuditLog.log(
            user_id=current_user.id,
            action="search",
            entity_type="global_search",
            ip_address=request.remote_addr,
            description=f"Searched for: {query}",
        )
        db.session.commit()

    return render_template(
        "cms/search.html", query=query, results=results, active_filter=entity_type
    )


@cms_bp.route("/api/search")
@login_required
def api_search() -> flask.Response:
    """API endpoint for autocomplete/typeahead search."""
    query = request.args.get("q", "")
    entity_type = request.args.get("type", "")

    if not query or len(query) < 2:
        return jsonify({"results": []})

    results = {"cases": [], "clients": [], "subjects": []}
    accessible_ids = _accessible_ids()

    if not entity_type or entity_type == "cases":
        total_cases = (
            Case.query.filter(
                Case.is_deleted == False,
                Case.id.in_(accessible_ids),
                db.or_(
                    Case.title.ilike(f"%{query}%"), Case.case_number.ilike(f"%{query}%")
                ),
            )
            .limit(5)
            .all()
        )
        results["cases"] = [
            {"id": c.id, "title": c.title, "case_number": c.case_number, "type": "case"}
            for c in total_cases
        ]

    if not entity_type or entity_type == "clients":
        clients_q = Client.query.filter(
            Client.is_deleted == False,
            Client.name.ilike(f"%{query}%"),
        )
        if not current_user.is_admin:
            clients_q = clients_q.filter(Client.cases.any(Case.id.in_(accessible_ids)))
        results["clients"] = [
            {"id": c.id, "name": c.name, "type": "client"}
            for c in clients_q.limit(5).all()
        ]

    if not entity_type or entity_type == "subjects":
        subjects_q = Subject.query.filter(
            Subject.is_deleted == False,
            Subject.name.ilike(f"%{query}%"),
        )
        if not current_user.is_admin:
            subjects_q = subjects_q.filter(
                db.select(case_subjects.c.case_id)
                .where(
                    case_subjects.c.subject_id == Subject.id,
                    case_subjects.c.case_id.in_(accessible_ids),
                )
                .exists()
            )
        results["subjects"] = [
            {
                "id": s.id,
                "name": s.name,
                "type": "subject",
                "subject_type": s.subject_type,
            }
            for s in subjects_q.limit(5).all()
        ]

    return jsonify({"results": results})
