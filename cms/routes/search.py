import logging

import flask
from flask import abort, request, jsonify, render_template
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
    Tenant,
    AuditLog,
    case_subjects,
)
from ..auth import get_accessible_case_ids, apply_tenant_filter, viewer_required
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
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning("Failed to log restricted search audit entry")
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
        f'🔍 "{query}" was found but has access restrictions. '
        f"The case owner{owners_part} has been notified "
        f"and will contact you if needed.",
        "warning",
    )


@cms_bp.route("/search")
@login_required
@viewer_required
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
            clients_q = apply_tenant_filter(clients_q, Client)
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
                restricted_q = Finding.query.join(Case).filter(
                    Finding.is_deleted == False,
                    ~Case.id.in_(accessible_ids),
                    db.or_(
                        Finding.title.ilike(f"%{query}%"),
                        Finding.content.ilike(f"%{query}%"),
                    ),
                )
                restricted_q = apply_tenant_filter(restricted_q, Finding)
                restricted = restricted_q.limit(20).all()
                rcn = list(set(f.case.case_number for f in restricted if f.case))
                _record_restricted(query, "findings", total_count, len(findings), rcn)

        if entity_type in ["all", "financials"]:
            financials_q = (
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
            )
            financials_q = apply_tenant_filter(financials_q, FinancialRecord)
            total_financials = financials_q.limit(20).all()
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

        if entity_type in ["all", "comments", "notes"]:
            # Case-scoped comments
            comments_q = Comment.query.options(db.joinedload(Comment.author)).filter(
                Comment.is_deleted == False,
                Comment.content.ilike(f"%{query}%"),
            )
            comments_q = apply_tenant_filter(comments_q, Comment)
            if not current_user.is_admin:
                comments_q = comments_q.filter(Comment.case_id.in_(accessible_ids))
            total_comments = comments_q.limit(20).all()

            comment_case_ids = {c.case_id for c in total_comments if c.case_id}
            comment_cases_map = {}
            if comment_case_ids:
                _cases_q = Case.query.filter(Case.id.in_(comment_case_ids))
                _cases_q = apply_tenant_filter(_cases_q, Case)
                for _c in _cases_q.all():
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
                        "_source": "comment",
                    }
                )

            # Subject.notes matches
            subject_notes_q = Subject.query.filter(
                Subject.is_deleted == False, Subject.notes.ilike(f"%{query}%")
            )
            subject_notes_q = apply_tenant_filter(subject_notes_q, Subject)
            if not current_user.is_admin:
                subject_notes_q = subject_notes_q.filter(
                    db.select(case_subjects.c.case_id)
                    .where(
                        case_subjects.c.subject_id == Subject.id,
                        case_subjects.c.case_id.in_(accessible_ids),
                    )
                    .exists()
                )
            for s in subject_notes_q.limit(10).all():
                results["comments"].append(
                    {
                        "id": s.id,
                        "content": (s.notes[:200] + "...")
                        if s.notes and len(s.notes) > 200
                        else (s.notes or ""),
                        "comment_type": "note",
                        "case_id": None,
                        "subject_id": s.id,
                        "client_id": None,
                        "case_number": None,
                        "author_name": s.name,
                        "created_at": None,
                        "_source": "subject_note",
                    }
                )

            # Subject-scoped comments
            comment_notes_q = Comment.query.options(
                db.joinedload(Comment.author)
            ).filter(
                Comment.is_deleted == False,
                Comment.subject_id.isnot(None),
                Comment.content.ilike(f"%{query}%"),
            )
            comment_notes_q = apply_tenant_filter(comment_notes_q, Comment)
            if not current_user.is_admin:
                comment_notes_q = comment_notes_q.filter(
                    Comment.case_id.in_(accessible_ids)
                )
            total_comment_notes = (
                comment_notes_q.order_by(Comment.created_at.desc()).limit(10).all()
            )
            note_subject_ids = {
                c.subject_id for c in total_comment_notes if c.subject_id
            }
            note_subjects_map = {}
            if note_subject_ids:
                _subjects_q = Subject.query.filter(Subject.id.in_(note_subject_ids))
                _subjects_q = apply_tenant_filter(_subjects_q, Subject)
                for _s in _subjects_q.all():
                    note_subjects_map[_s.id] = _s
            for c in total_comment_notes:
                sub = note_subjects_map.get(c.subject_id)
                if sub and not sub.is_deleted:
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
                            "case_number": None,
                            "author_name": c.author.full_name if c.author else sub.name,
                            "created_at": c.created_at.strftime("%Y-%m-%d")
                            if c.created_at
                            else None,
                            "_source": "subject_comment",
                            "_subject_name": sub.name,
                        }
                    )

        AuditLog.log(
            user_id=current_user.id,
            action="search",
            entity_type="global_search",
            ip_address=request.remote_addr,
            description=f"Searched for: {query}",
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.warning("Failed to log search audit entry")

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
        cases_q = Case.query.filter(
            Case.is_deleted == False,
            Case.id.in_(accessible_ids),
            db.or_(
                Case.title.ilike(f"%{query}%"), Case.case_number.ilike(f"%{query}%")
            ),
        )
        cases_q = apply_tenant_filter(cases_q, Case)
        total_cases = cases_q.limit(5).all()
        results["cases"] = [
            {"id": c.id, "title": c.title, "case_number": c.case_number, "type": "case"}
            for c in total_cases
        ]

    if not entity_type or entity_type == "clients":
        clients_q = Client.query.filter(
            Client.is_deleted == False,
            Client.name.ilike(f"%{query}%"),
        )
        clients_q = apply_tenant_filter(clients_q, Client)
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
                "type": "subject",
                "subject_type": s.subject_type,
            }
            for s in subjects_q.limit(5).all()
        ]

    return jsonify({"results": results})


@cms_bp.route("/admin/global-search")
@login_required
def global_search():
    """Super-admin cross-tenant search across all entities."""
    if not current_user.is_super_admin:
        abort(403)

    q = request.args.get("q", "")
    entity_type = request.args.get("type", "all")

    results = {
        "cases": [],
        "clients": [],
        "subjects": [],
        "findings": [],
        "financials": [],
        "comments": [],
    }

    def _tenant_name(tid):
        t = db.session.get(Tenant, tid)
        return t.name if t else "Unknown"

    if q and len(q) >= 2:
        if entity_type in ("all", "cases"):
            cases = (
                Case.query.options(db.contains_eager(Case.client))
                .join(Client)
                .filter(
                    Case.is_deleted == False,
                    db.or_(
                        Case.title.ilike(f"%{q}%"),
                        Case.case_number.ilike(f"%{q}%"),
                        Case.description.ilike(f"%{q}%"),
                    ),
                )
                .limit(20)
                .all()
            )
            results["cases"] = [
                {
                    "id": c.id,
                    "title": c.title,
                    "case_number": c.case_number,
                    "status": c.status,
                    "priority": c.priority,
                    "client_name": c.client.name if c.client else None,
                    "tenant_name": _tenant_name(c.tenant_id),
                    "tenant_id": c.tenant_id,
                    "created_at": c.created_at.strftime("%Y-%m-%d")
                    if c.created_at
                    else None,
                }
                for c in cases
            ]

        if entity_type in ("all", "clients"):
            clients = (
                Client.query.filter(
                    Client.is_deleted == False,
                    Client.name.ilike(f"%{q}%"),
                )
                .limit(20)
                .all()
            )
            results["clients"] = [
                {
                    "id": c.id,
                    "name": c.name,
                    "contact_person": c.contact_person,
                    "is_company": c.is_company,
                    "is_active": c.is_active,
                    "contract_number": c.contract_number,
                    "tenant_name": _tenant_name(c.tenant_id),
                    "tenant_id": c.tenant_id,
                }
                for c in clients
            ]

        if entity_type in ("all", "subjects"):
            subjects = (
                Subject.query.filter(
                    Subject.is_deleted == False,
                    db.or_(
                        Subject.name.ilike(f"%{q}%"),
                        Subject.identification_number.ilike(f"%{q}%"),
                    ),
                )
                .limit(20)
                .all()
            )
            results["subjects"] = [
                {
                    "id": s.id,
                    "name": s.name,
                    "subject_type": s.subject_type,
                    "risk_score": s.risk_score,
                    "tenant_name": _tenant_name(s.tenant_id),
                    "tenant_id": s.tenant_id,
                    "created_at": s.created_at.strftime("%Y-%m-%d")
                    if s.created_at
                    else None,
                }
                for s in subjects
            ]

        if entity_type in ("all", "findings"):
            findings = (
                Finding.query.options(db.joinedload(Finding.case))
                .join(Case)
                .filter(
                    Finding.is_deleted == False,
                    db.or_(
                        Finding.title.ilike(f"%{q}%"),
                        Finding.content.ilike(f"%{q}%"),
                    ),
                )
                .limit(20)
                .all()
            )
            results["findings"] = [
                {
                    "id": f.id,
                    "title": f.title,
                    "case_id": f.case_id,
                    "case_number": f.case.case_number if f.case else None,
                    "finding_type": f.finding_type,
                    "source_type": f.source_type,
                    "tenant_name": _tenant_name(f.tenant_id),
                    "tenant_id": f.tenant_id,
                    "created_at": f.created_at.strftime("%Y-%m-%d")
                    if f.created_at
                    else None,
                }
                for f in findings
            ]

        if entity_type in ("all", "financials"):
            financials = (
                FinancialRecord.query.options(db.joinedload(FinancialRecord.case))
                .join(Case)
                .filter(
                    FinancialRecord.is_deleted == False,
                    db.or_(
                        FinancialRecord.description.ilike(f"%{q}%"),
                        FinancialRecord.source_reference.ilike(f"%{q}%"),
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
                    "tenant_name": _tenant_name(f.tenant_id),
                    "tenant_id": f.tenant_id,
                }
                for f in financials
            ]

        if entity_type in ("all", "comments", "notes"):
            comments = (
                Comment.query.options(db.joinedload(Comment.author))
                .filter(
                    Comment.is_deleted == False,
                    Comment.content.ilike(f"%{q}%"),
                )
                .limit(20)
                .all()
            )
            comment_case_ids = {c.case_id for c in comments if c.case_id}
            case_map = {}
            if comment_case_ids:
                for _c in Case.query.filter(Case.id.in_(comment_case_ids)).all():
                    case_map[_c.id] = _c
            results["comments"] = [
                {
                    "id": c.id,
                    "content": (c.content[:200] + "...")
                    if c.content and len(c.content) > 200
                    else (c.content or ""),
                    "comment_type": c.comment_type,
                    "case_id": c.case_id,
                    "subject_id": c.subject_id,
                    "client_id": c.client_id,
                    "case_number": case_map[c.case_id].case_number
                    if c.case_id in case_map
                    else None,
                    "author_name": c.author.full_name if c.author else "Unknown",
                    "tenant_name": _tenant_name(c.tenant_id),
                    "tenant_id": c.tenant_id,
                    "created_at": c.created_at.strftime("%Y-%m-%d")
                    if c.created_at
                    else None,
                    "_source": "comment",
                }
                for c in comments
            ]

            # Subject.notes matches
            subject_notes = (
                Subject.query.filter(
                    Subject.is_deleted == False,
                    Subject.notes.ilike(f"%{q}%"),
                )
                .limit(10)
                .all()
            )
            for s in subject_notes:
                results["comments"].append(
                    {
                        "id": s.id,
                        "content": (s.notes[:200] + "...")
                        if s.notes and len(s.notes) > 200
                        else (s.notes or ""),
                        "comment_type": "note",
                        "case_id": None,
                        "subject_id": s.id,
                        "client_id": None,
                        "case_number": None,
                        "author_name": s.name,
                        "created_at": None,
                        "tenant_name": _tenant_name(s.tenant_id),
                        "tenant_id": s.tenant_id,
                        "_source": "subject_note",
                    }
                )

            # Subject-scoped comments
            comment_notes = (
                Comment.query.options(db.joinedload(Comment.author))
                .filter(
                    Comment.is_deleted == False,
                    Comment.subject_id.isnot(None),
                    Comment.content.ilike(f"%{q}%"),
                )
                .order_by(Comment.created_at.desc())
                .limit(10)
                .all()
            )
            note_subject_ids = {c.subject_id for c in comment_notes if c.subject_id}
            subject_map = {}
            if note_subject_ids:
                for _s in Subject.query.filter(Subject.id.in_(note_subject_ids)).all():
                    subject_map[_s.id] = _s
            for c in comment_notes:
                sub = subject_map.get(c.subject_id)
                if sub and not sub.is_deleted:
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
                            "case_number": None,
                            "author_name": c.author.full_name if c.author else sub.name,
                            "created_at": c.created_at.strftime("%Y-%m-%d")
                            if c.created_at
                            else None,
                            "tenant_name": _tenant_name(c.tenant_id),
                            "tenant_id": c.tenant_id,
                            "_source": "subject_comment",
                            "_subject_name": sub.name,
                        }
                    )

        AuditLog.log(
            user_id=current_user.id,
            action="search",
            entity_type="global_search_super",
            ip_address=request.remote_addr,
            description=f"Super-admin global search for: {q}",
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.warning("Failed to log super-admin search audit entry")

    return render_template(
        "cms/global_search.html", query=q, results=results, active_filter=entity_type
    )
