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
)
from ..auth import get_accessible_case_ids
from ..notifications import notify_search_restricted

logger = logging.getLogger(__name__)


def _accessible_ids():
    """Get cached set of accessible case IDs for current user."""
    return set(get_accessible_case_ids(current_user))


def _filter_by_case_access(items, case_id_attr):
    """Filter a list of ORM objects by checking their case_id against accessible set."""
    accessible = _accessible_ids()
    return [i for i in items if getattr(i, case_id_attr, None) in accessible]


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
    owners_str = ", ".join(sorted(owner_names))
    flask.flash(
        f'🔍 "{query}" is gevonden maar heeft toegangsrestricties. '
        f"Case-eigenaar ({owners_str}) is op de hoogte gesteld "
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
            total_cases = (
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
                .limit(20)
                .all()
            )
            filtered = [c for c in total_cases if c.id in accessible_ids]
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
                for c in filtered
            ]
            if len(filtered) < len(total_cases):
                restricted_case_numbers = [
                    c.case_number for c in total_cases if c.id not in accessible_ids
                ]
                _record_restricted(
                    query,
                    "cases",
                    len(total_cases),
                    len(filtered),
                    restricted_case_numbers,
                )

        if entity_type in ["all", "clients"]:
            total_clients = (
                Client.query.filter(
                    Client.is_deleted == False, Client.name.ilike(f"%{query}%")
                )
                .limit(20)
                .all()
            )

            # Filter clients: keep if any of their cases are accessible
            def _client_accessible(c):
                for case in c.cases:
                    if not case.is_deleted and case.id in accessible_ids:
                        return True
                return False

            if current_user.is_admin:
                filtered_clients = total_clients
            else:
                filtered_clients = [c for c in total_clients if _client_accessible(c)]
            results["clients"] = [
                {
                    "id": c.id,
                    "name": c.name,
                    "contact_person": c.contact_person,
                    "is_company": c.is_company,
                    "is_active": c.is_active,
                    "contract_number": c.contract_number,
                }
                for c in filtered_clients
            ]

        if entity_type in ["all", "subjects"]:
            total_subjects = (
                Subject.query.filter(
                    Subject.is_deleted == False,
                    db.or_(
                        Subject.name.ilike(f"%{query}%"),
                        Subject.identification_number.ilike(f"%{query}%"),
                    ),
                )
                .limit(20)
                .all()
            )
            # Filter subjects: keep if linked to an accessible case
            from ..models import case_subjects

            def _subject_accessible(s):
                linked = (
                    db.session.query(case_subjects.c.case_id)
                    .filter(case_subjects.c.subject_id == s.id)
                    .all()
                )
                for (cid,) in linked:
                    if cid in accessible_ids:
                        return True
                return False

            if current_user.is_admin:
                filtered_subjects = total_subjects
            else:
                filtered_subjects = [
                    s for s in total_subjects if _subject_accessible(s)
                ]
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
                for s in filtered_subjects
            ]

        if entity_type in ["all", "findings"]:
            total_findings = (
                Finding.query.options(db.joinedload(Finding.case))
                .join(Case)
                .filter(
                    Finding.is_deleted == False,
                    db.or_(
                        Finding.title.ilike(f"%{query}%"),
                        Finding.content.ilike(f"%{query}%"),
                    ),
                )
                .limit(20)
                .all()
            )
            filtered_findings = _filter_by_case_access(total_findings, "case_id")
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
                for f in filtered_findings
            ]
            if len(filtered_findings) < len(total_findings):
                rcn = list(
                    set(
                        f.case.case_number
                        for f in total_findings
                        if f.case and f.case_id not in accessible_ids
                    )
                )
                _record_restricted(
                    query, "findings", len(total_findings), len(filtered_findings), rcn
                )

        if entity_type in ["all", "financials"]:
            total_financials = (
                FinancialRecord.query.options(db.joinedload(FinancialRecord.case))
                .join(Case)
                .filter(
                    FinancialRecord.is_deleted == False,
                    db.or_(
                        FinancialRecord.description.ilike(f"%{query}%"),
                        FinancialRecord.source_reference.ilike(f"%{query}%"),
                    ),
                )
                .limit(20)
                .all()
            )
            filtered_financials = _filter_by_case_access(total_financials, "case_id")
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
                for f in filtered_financials
            ]

        if entity_type in ["all", "comments"]:
            total_comments = (
                Comment.query.options(db.joinedload(Comment.author))
                .filter(
                    Comment.is_deleted == False, Comment.content.ilike(f"%{query}%")
                )
                .limit(20)
                .all()
            )
            # Batch-load cases for comments
            comment_case_ids = {c.case_id for c in total_comments if c.case_id}
            comment_cases_map = {}
            if comment_case_ids:
                for _c in Case.query.filter(Case.id.in_(comment_case_ids)).all():
                    comment_cases_map[_c.id] = _c
            filtered_comments = []
            for c in total_comments:
                if c.case_id and c.case_id in accessible_ids:
                    filtered_comments.append(c)
                elif current_user.is_admin:
                    filtered_comments.append(c)
            results["comments"] = []
            for c in filtered_comments:
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
            total_subject_notes = (
                Subject.query.filter(
                    Subject.is_deleted == False, Subject.notes.ilike(f"%{query}%")
                )
                .limit(10)
                .all()
            )
            from ..models import case_subjects

            def _subject_note_accessible(s):
                linked = (
                    db.session.query(case_subjects.c.case_id)
                    .filter(case_subjects.c.subject_id == s.id)
                    .all()
                )
                for (cid,) in linked:
                    if cid in accessible_ids:
                        return True
                return False

            filtered_subject_notes = (
                total_subject_notes
                if current_user.is_admin
                else [s for s in total_subject_notes if _subject_note_accessible(s)]
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
                for s in filtered_subject_notes
            ]
            total_comment_notes = (
                Comment.query.options(db.joinedload(Comment.author))
                .filter(
                    Comment.is_deleted == False,
                    Comment.subject_id.isnot(None),
                    Comment.content.ilike(f"%{query}%"),
                )
                .order_by(Comment.created_at.desc())
                .limit(10)
                .all()
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
                    # Check case access for this comment's case
                    if (
                        c.case_id
                        and c.case_id not in accessible_ids
                        and not current_user.is_admin
                    ):
                        continue
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
                db.or_(
                    Case.title.ilike(f"%{query}%"), Case.case_number.ilike(f"%{query}%")
                ),
            )
            .limit(5)
            .all()
        )
        filtered = [c for c in total_cases if c.id in accessible_ids]
        results["cases"] = [
            {"id": c.id, "title": c.title, "case_number": c.case_number, "type": "case"}
            for c in filtered
        ]

    if not entity_type or entity_type == "clients":
        total_clients = (
            Client.query.filter(
                Client.is_deleted == False, Client.name.ilike(f"%{query}%")
            )
            .limit(5)
            .all()
        )

        def _client_accessible(c):
            for case in c.cases:
                if not case.is_deleted and case.id in accessible_ids:
                    return True
            return False

        filtered_clients = (
            total_clients
            if current_user.is_admin
            else [c for c in total_clients if _client_accessible(c)]
        )
        results["clients"] = [
            {"id": c.id, "name": c.name, "type": "client"} for c in filtered_clients
        ]

    if not entity_type or entity_type == "subjects":
        total_subjects = (
            Subject.query.filter(
                Subject.is_deleted == False, Subject.name.ilike(f"%{query}%")
            )
            .limit(5)
            .all()
        )
        from ..models import case_subjects

        def _subject_accessible(s):
            linked = (
                db.session.query(case_subjects.c.case_id)
                .filter(case_subjects.c.subject_id == s.id)
                .all()
            )
            for (cid,) in linked:
                if cid in accessible_ids:
                    return True
            return False

        filtered_subjects = (
            total_subjects
            if current_user.is_admin
            else [s for s in total_subjects if _subject_accessible(s)]
        )
        results["subjects"] = [
            {
                "id": s.id,
                "name": s.name,
                "type": "subject",
                "subject_type": s.subject_type,
            }
            for s in filtered_subjects
        ]

    return jsonify({"results": results})
