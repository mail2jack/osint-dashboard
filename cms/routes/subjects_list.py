import logging

import flask
from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Subject, Case, Finding, SocialAccount, case_subjects
from .utils import find_similar_subjects, find_similar_clients, check_for_exact_match
from ..auth import subject_access_required, audit_read

logger = logging.getLogger(__name__)


@cms_bp.route("/subjects")
@login_required
def subjects() -> str:
    """List all subjects with search, filtering, and sorting."""
    page = request.args.get("page", 1, type=int)
    per_page = 30
    search = request.args.get("search", "")
    subject_type = request.args.get("type", "")
    case_filter = request.args.get("case_filter", "all")
    sort = request.args.get("sort", "name")
    order = request.args.get("order", "asc")
    fmt = request.args.get("format", "")

    query = Subject.query.filter_by(is_deleted=False)

    # Non-admin users only see subjects linked to cases they can access
    linked_subject_ids = None
    if not current_user.is_admin:
        accessible_case_ids = _get_accessible_case_ids()
        if accessible_case_ids is not None:
            linked_subject_ids = [
                row.subject_id
                for row in db.session.query(case_subjects.c.subject_id)
                .filter(case_subjects.c.case_id.in_(accessible_case_ids))
                .distinct()
                .all()
            ]
            if linked_subject_ids:
                query = query.filter(Subject.id.in_(linked_subject_ids))
            else:
                query = query.filter(db.text("1=0"))
        else:
            query = query.filter(db.text("1=0"))

    if search:
        from sqlalchemy import exists as sa_exists

        social_match = sa_exists().where(
            db.and_(
                SocialAccount.subject_id == Subject.id,
                SocialAccount.username.ilike(f"%{search}%"),
            )
        )
        query = query.filter(
            db.or_(
                Subject.name.ilike(f"%{search}%"),
                social_match,
            )
        )

        # Non-admin: detect restricted search results and notify case owners
        if not current_user.is_admin and linked_subject_ids is not None:
            from cms.notifications import notify_search_restricted

            accessible_count = query.count()
            total_q = Subject.query.filter(Subject.is_deleted == False).filter(
                db.or_(
                    Subject.name.ilike(f"%{search}%"),
                    sa_exists().where(
                        db.and_(
                            SocialAccount.subject_id == Subject.id,
                            SocialAccount.username.ilike(f"%{search}%"),
                        )
                    ),
                )
            )
            total_count = total_q.count()
            restricted = total_count - accessible_count
            if restricted > 0:
                if linked_subject_ids:
                    restricted_subjects = Subject.query.filter(
                        Subject.is_deleted == False,
                        Subject.name.ilike(f"%{search}%"),
                        ~Subject.id.in_(linked_subject_ids),
                    ).all()
                else:
                    restricted_subjects = Subject.query.filter(
                        Subject.is_deleted == False,
                        Subject.name.ilike(f"%{search}%"),
                    ).all()
            restricted_case_numbers = set()
            if restricted_subjects:
                restricted_ids = [s.id for s in restricted_subjects]
                case_mappings = (
                    db.session.query(
                        case_subjects.c.subject_id, case_subjects.c.case_id
                    )
                    .filter(case_subjects.c.subject_id.in_(restricted_ids))
                    .all()
                )
                all_case_ids = list(set(m.case_id for m in case_mappings))
                if all_case_ids:
                    cases_map = {
                        c.id: c
                        for c in Case.query.filter(
                            Case.id.in_(all_case_ids), Case.is_deleted.is_(False)
                        ).all()
                    }
                    for mapping in case_mappings:
                        case = cases_map.get(mapping.case_id)
                        if case:
                            restricted_case_numbers.add(case.case_number)
                owner_names = notify_search_restricted(
                    user_id=current_user.id,
                    query=search,
                    restricted_case_numbers=list(restricted_case_numbers),
                    restricted_count=restricted,
                    searching_username=current_user.username,
                )
                owners_str = ", ".join(sorted(owner_names))
                flask.flash(
                    f'🔍 "{search}" is gevonden maar heeft toegangsrestricties. '
                    f"Case-eigenaar ({owners_str}) is op de hoogte gesteld "
                    f"en zal indien nodig contact met je opnemen.",
                    "warning",
                )

    if subject_type:
        query = query.filter_by(subject_type=subject_type)

    if case_filter == "has_case":
        query = query.filter(Subject.cases.any())
    elif case_filter == "no_case":
        query = query.filter(~Subject.cases.any())

    sort_columns = {
        "name": Subject.name,
        "type": Subject.subject_type,
        "risk": Subject.risk_score,
    }

    sort_col = sort_columns.get(sort, Subject.name)
    if order == "desc":
        sort_col = sort_col.desc()

    if fmt == "json":
        search_q = request.args.get("q", "").strip()
        if search_q:
            query = query.filter(Subject.name.ilike(f"%{search_q}%"))
        subjects_list = query.order_by(sort_col).limit(200).all()
        return jsonify(
            {
                "subjects": [
                    {"id": s.id, "name": s.name, "type": s.subject_type}
                    for s in subjects_list
                ],
                "total": len(subjects_list),
                "has_more": query.order_by(sort_col).count() > 200,
            }
        )

    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        "cms/subjects/list.html",
        subjects=pagination.items,
        pagination=pagination,
        filters={
            "search": search,
            "type": subject_type,
            "case_filter": case_filter,
            "sort": sort,
            "order": order,
        },
    )


def _get_accessible_case_ids():
    """Get case IDs the current user has access to. Returns None if admin (no filter)."""
    user = current_user
    if user.is_admin:
        return None
    from ..models import Case

    case_ids = [
        c.id
        for c in Case.query.filter(
            Case.is_deleted == False,
            db.or_(
                Case.created_by == user.id,
                Case.lead_investigator_id == user.id,
                Case.assigned_to == user.id,
            ),
        ).all()
    ]
    assigned_ids = [c.id for c in user.assigned_cases]
    return list(set(case_ids + assigned_ids))


@cms_bp.route("/subjects/<subject_id>")
@login_required
@subject_access_required
@audit_read("subject")
def view_subject(subject_id: str) -> str:
    """View subject details."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    subject.decrypt_identifiers()
    subject.vessel_data = subject.vessel_data or {}
    for addr in subject.addresses:
        addr.decrypt_fields()
    for c in subject.contacts:
        c.decrypt_fields()

    findings_page = request.args.get("findings_page", 1, type=int)
    findings_per_page = 20
    findings_pagination = (
        subject.findings.filter_by(is_deleted=False)
        .order_by(Finding.created_at.desc())
        .paginate(page=findings_page, per_page=findings_per_page, error_out=False)
    )

    linked_case_ids = [
        row.case_id
        for row in db.session.query(case_subjects.c.case_id)
        .filter(case_subjects.c.subject_id == subject.id)
        .all()
    ]
    linked_cases = []
    first_case_id = None
    if linked_case_ids:
        for case in Case.query.filter(
            Case.id.in_(linked_case_ids), Case.is_deleted.is_(False)
        ).all():
            case_info = {
                "id": case.id,
                "case_number": case.case_number,
                "title": case.title,
            }
            linked_cases.append(case_info)
            if first_case_id is None:
                first_case_id = case.id

    return render_template(
        "cms/subjects/view.html",
        subject=subject,
        findings=findings_pagination.items,
        findings_pagination=findings_pagination,
        linked_cases=linked_cases,
        first_case_id=first_case_id,
    )


@cms_bp.route("/api/check-duplicate")
@login_required
def check_duplicate() -> flask.Response:
    """Check for duplicate subjects or clients by name (for real-time lookup)."""
    name = request.args.get("name", "").strip()
    entity_type = request.args.get("type", "subject")  # 'subject' or 'client'

    if len(name) < 2:
        return jsonify({"duplicates": [], "exact": None})

    if entity_type == "subject":
        exact = check_for_exact_match(name, "subject")
        similar = find_similar_subjects(name)[:5]
        return jsonify({"duplicates": similar, "exact": exact})
    else:
        exact = check_for_exact_match(name, "client")
        similar = find_similar_clients(name)[:5]
        return jsonify({"duplicates": similar, "exact": exact})
