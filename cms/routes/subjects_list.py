import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify, render_template, redirect, url_for, abort
from flask_login import login_required, current_user
from . import cms_bp
from ..models import (
    db,
    Subject,
    Case,
    Finding,
    SocialAccount,
    Address,
    Contact,
    AuditLog,
    case_subjects,
    subject_relations,
)
from .utils import find_similar_subjects, find_similar_clients, check_for_exact_match
from ..auth import admin_required, subject_access_required, audit_read, apply_tenant_filter
from ..tier_limits import check_feature
from ..services.subject_service import subject_service
from ..workflow.actions.helpers import presets_for_subject
from ..workflow.research import ACTION_REGISTRY


def _search_subjects_by_name(search_term: str, tenant_id: str = None) -> list[str]:
    """Search subjects by name using SQL ILIKE on plaintext name fields.

    Subject.name, achternaam, voornamen are plaintext columns with indexes.
    No decryption needed — this is an O(log n) database lookup.
    """
    pattern = f"%{search_term}%"
    q = Subject.query.filter(
        Subject.is_deleted == False,
        db.or_(
            Subject.name.ilike(pattern),
            Subject.achternaam.ilike(pattern),
            Subject.voornamen.ilike(pattern),
        ),
    )
    if tenant_id:
        q = q.filter(Subject.tenant_id == tenant_id)
    return [row[0] for row in q.with_entities(Subject.id).all()]


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

    # Tenant isolation (SQLite compat)
    query = apply_tenant_filter(query, Subject)

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
        # Encrypted search: decrypt names in Python, filter in Python
        from sqlalchemy import exists as sa_exists

        matched_name_ids = _search_subjects_by_name(search, current_user.tenant_id)
        social_match = sa_exists().where(
            db.and_(
                SocialAccount.subject_id == Subject.id,
                SocialAccount.username.ilike(f"%{search}%"),
            )
        )
        if matched_name_ids:
            query = query.filter(
                db.or_(
                    Subject.id.in_(matched_name_ids),
                    social_match,
                )
            )
        else:
            query = query.filter(social_match)

        # Non-admin: detect restricted search results and notify case owners
        if not current_user.is_admin and linked_subject_ids is not None:
            from cms.notifications import notify_search_restricted

            accessible_count = query.count()
            restricted_case_numbers = set()
            # Total matches (accessible + restricted) = name matches + social matches
            total_social_q = Subject.query.filter(
                Subject.is_deleted == False,
                social_match,
            )
            total_social_q = apply_tenant_filter(total_social_q, Subject)
            total_count = len(matched_name_ids) + total_social_q.count()
            restricted = total_count - accessible_count
            if restricted > 0:
                if linked_subject_ids:
                    restricted_subjects = (
                        Subject.query.filter(
                            Subject.is_deleted == False,
                            Subject.id.in_(matched_name_ids),
                            ~Subject.id.in_(linked_subject_ids),
                        )
                        .filter(Subject.tenant_id == current_user.tenant_id)
                        .all()
                    )
                else:
                    restricted_subjects = (
                        Subject.query.filter(
                            Subject.is_deleted == False,
                            Subject.id.in_(matched_name_ids),
                        )
                        .filter(Subject.tenant_id == current_user.tenant_id)
                        .all()
                    )
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
                    _cases_q = Case.query.filter(
                        Case.id.in_(all_case_ids), Case.is_deleted.is_(False)
                    )
                    _cases_q = apply_tenant_filter(_cases_q, Case)
                    cases_map = {c.id: c for c in _cases_q.all()}
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
                owners_str = ", ".join(sorted(owner_names)) if owner_names else ""
                owners_part = f" ({owners_str})" if owners_str else ""
                flask.flash(
                    f'🔍 "{search}" was found but has access restrictions. '
                    f"The case owner{owners_part} has been notified "
                    f"and will contact you if needed.",
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
            matched_ids = _search_subjects_by_name(search_q, current_user.tenant_id)
            if matched_ids:
                query = query.filter(Subject.id.in_(matched_ids))
            else:
                query = query.filter(db.text("1=0"))
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

    _accessible_q = Case.query.with_entities(Case.id).filter(
        Case.is_deleted == False,
        db.or_(
            Case.created_by == user.id,
            Case.lead_investigator_id == user.id,
            Case.assigned_to == user.id,
        ),
    )
    _accessible_q = apply_tenant_filter(_accessible_q, Case)
    case_ids = [row.id for row in _accessible_q.all()]
    assigned_ids = [c.id for c in user.assigned_cases]
    return list(set(case_ids + assigned_ids))


@cms_bp.route("/subjects/<subject_id>")
@login_required
@subject_access_required
@audit_read("subject")
def view_subject(subject_id: str) -> str:
    """View subject details (legacy screen; PR8 fallback)."""
    subject = Subject.query.filter_by(id=subject_id).first() or abort(404)
    # ADR-0001: wrap in no_autoflush to prevent before_flush from
    # re-encrypting freshly decrypted identifiers during template render.
    with db.session.no_autoflush:
        subject.decrypt_identifiers()
        subject.vessel_data = subject.vessel_data or {}
        for addr in list(subject.addresses):
            addr.decrypt_fields()
        for c in list(subject.contacts):
            c.decrypt_fields()

        findings_page = request.args.get("findings_page", 1, type=int)
        findings_per_page = 20
        findings_pagination = (
            subject.findings.filter_by(is_deleted=False, archived_at=None)
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
            profile_enabled=check_feature(
                "subject_first_investigations", current_user.tenant_id
            ),
            action_presets=presets_for_subject(subject.subject_type),
            action_labels={k: v["label"] for k, v in ACTION_REGISTRY.items()},
        )


@cms_bp.route("/subjects/<subject_id>/profile")
@login_required
@subject_access_required
@audit_read("subject")
def subject_profile(subject_id: str) -> str:
    """Tabbed Subject Profile (ADR-0001 PR7a, behind the feature flag).

    Rendered from the ``SubjectService.profile_view`` read-model. The legacy
    ``view_subject`` screen stays available as the fallback until rollout (PR8).
    """
    if not check_feature("subject_first_investigations", current_user.tenant_id):
        return redirect(url_for("cms.view_subject", subject_id=subject_id))

    subject = Subject.query.filter_by(id=subject_id).first() or abort(404)
    include_archived = request.args.get("show_archived", "").strip() == "1"

    # Case isolation: compute accessible case IDs for this user.
    # Admins/super_admins see everything; investigators see only assigned cases.
    accessible_case_ids = None
    if not current_user.is_admin and not current_user.is_super_admin:
        from cms.models import Case as _Case

        subject_case_ids = [
            r.case_id
            for r in db.session.execute(
                case_subjects.select().where(
                    case_subjects.c.subject_id == subject.id
                )
            ).fetchall()
        ]
        if subject_case_ids:
            all_cases = _Case.query.filter(
                _Case.id.in_(subject_case_ids), _Case.is_deleted.is_(False)
            ).all()
            accessible_case_ids = {
                c.id for c in all_cases if current_user.can_access_case(c)
            }
        else:
            accessible_case_ids = set()

    profile = subject_service.profile_view(
        subject,
        include_archived=include_archived,
        accessible_case_ids=accessible_case_ids,
    )

    # Candidate subjects for the Relations tab add-form (same tenant,
    # excluding this subject and subjects already related).
    related_ids = {
        row.related_subject_id if row.subject_id == subject.id else row.subject_id
        for row in db.session.execute(
            subject_relations.select().where(
                db.or_(
                    subject_relations.c.subject_id == subject.id,
                    subject_relations.c.related_subject_id == subject.id,
                )
            )
        ).fetchall()
    }
    candidates = (
        Subject.query.filter(
            Subject.is_deleted.is_(False),
            Subject.tenant_id == current_user.tenant_id,
            Subject.id != subject.id,
            ~Subject.id.in_(related_ids or [""]),
        )
        .order_by(Subject.name)
        .limit(500)
        .with_entities(Subject.id, Subject.name, Subject.subject_type)
        .all()
    )

    return render_template(
        "cms/subjects/profile.html",
        subject=subject,
        profile=profile,
        can_edit=current_user.role != "viewer",
        relation_candidates=[
            {"id": c.id, "name": c.name, "subject_type": c.subject_type}
            for c in candidates
        ],
        action_presets=presets_for_subject(subject.subject_type),
        action_labels={k: v["label"] for k, v in ACTION_REGISTRY.items()},
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


@cms_bp.route("/subjects/<target_id>/merge", methods=["POST"])
@login_required
@subject_access_required
@admin_required
def merge_subjects(target_id: str):
    """Merge a source subject into a target subject.

    Transfers case links, relations, addresses, contacts, social accounts,
    and findings from source to target. Merges non-empty fields (target wins).
    Soft-deletes the source subject. Logs the merge in AuditLog.
    """
    data = request.get_json(force=True)
    source_id = data.get("source_id")
    if not source_id:
        return jsonify({"error": "source_id required"}), 400

    target = Subject.query.filter_by(id=target_id, is_deleted=False).first()
    source = Subject.query.filter_by(id=source_id, is_deleted=False).first()

    if not target or not source:
        return jsonify({"error": "Subject not found"}), 404

    if target.tenant_id != current_user.tenant_id:
        return jsonify({"error": "Access denied"}), 403

    if source.tenant_id != current_user.tenant_id:
        return jsonify({"error": "Source subject not found"}), 404

    if target_id == source_id:
        return jsonify({"error": "Cannot merge a subject into itself"}), 400

    # Decrypt both subjects for field comparison
    with db.session.no_autoflush:
        target.decrypt_identifiers()
        source.decrypt_identifiers()

        # --- Merge encrypted fields (target wins: only fill blanks) ---
        merged_fields = []
        for field in Subject.ENCRYPTED_FIELDS:
            source_val = getattr(source, field, None)
            target_val = getattr(target, field, None)
            if source_val and not target_val:
                setattr(target, field, source_val)
                merged_fields.append(field)

        # --- Merge plain fields (target wins) ---
        for field in [
            "subject_type",
            "risk_score",
            "photo_path",
            "vessel_data",
            "rdw_data",
        ]:
            source_val = getattr(source, field, None)
            target_val = getattr(target, field, None)
            if source_val and not target_val:
                setattr(target, field, source_val)
                merged_fields.append(field)

        # --- Re-parent case_subjects ---
        existing_target_case_ids = set(
            row.case_id
            for row in db.session.query(case_subjects.c.case_id)
            .filter(case_subjects.c.subject_id == target_id)
            .all()
        )
        source_case_rows = (
            db.session.query(case_subjects)
            .filter(case_subjects.c.subject_id == source_id)
            .all()
        )
        case_links_transferred = 0
        case_links_skipped = 0
        for row in source_case_rows:
            if row.case_id in existing_target_case_ids:
                case_links_skipped += 1
            else:
                db.session.execute(
                    case_subjects.insert().values(
                        case_id=row.case_id,
                        subject_id=target_id,
                        role_in_case=row.role_in_case,
                        status=row.status,
                        note=row.note,
                    )
                )
                case_links_transferred += 1
        # Delete source case links
        db.session.execute(
            case_subjects.delete().where(case_subjects.c.subject_id == source_id)
        )

        # --- Re-parent subject_relations ---
        # Relations where source is subject_id → update to target
        db.session.execute(
            subject_relations.update()
            .where(subject_relations.c.subject_id == source_id)
            .values(subject_id=target_id)
        )
        # Relations where source is related_subject_id → update to target
        db.session.execute(
            subject_relations.update()
            .where(subject_relations.c.related_subject_id == source_id)
            .values(related_subject_id=target_id)
        )

        # --- Re-parent child records ---
        Address.query.filter_by(subject_id=source_id).update({"subject_id": target_id})
        Contact.query.filter_by(subject_id=source_id).update({"subject_id": target_id})
        SocialAccount.query.filter_by(subject_id=source_id).update(
            {"subject_id": target_id}
        )
        Finding.query.filter_by(subject_id=source_id).update({"subject_id": target_id})

        # --- Soft-delete source ---
        source.is_deleted = True
        source.deleted_at = datetime.now(timezone.utc)

        # --- Audit log ---
        AuditLog.log(
            user_id=current_user.id,
            action="merge_subjects",
            entity_type="subject",
            entity_id=target_id,
            ip_address=request.remote_addr,
            description=(
                f"Merged subject {source_id} ({source.name}) into {target_id} ({target.name}). "
                f"Fields merged: {', '.join(merged_fields) or 'none'}. "
                f"Case links transferred: {case_links_transferred}, skipped: {case_links_skipped}."
            ),
        )

        db.session.commit()

    flask.flash(
        f"Subject '{source.name}' merged into '{target.name}'. "
        f"({len(merged_fields)} field(s) filled, {case_links_transferred} case link(s) transferred)",
        "success",
    )
    return jsonify(
        {
            "ok": True,
            "merged_fields": merged_fields,
            "case_links_transferred": case_links_transferred,
            "case_links_skipped": case_links_skipped,
        }
    )
