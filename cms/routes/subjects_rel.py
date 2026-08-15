import logging

import flask
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Subject, subject_relations, AuditLog
from ..auth import roles_required, subject_access_required, apply_tenant_filter
from ..validation import validate, AddRelationSchema, RemoveRelationSchema

from .response import api_success, api_error

logger = logging.getLogger(__name__)

# ADR-0001 PR3: relations are stored as a single canonical row per pair with a
# direction and a relation_type in the family|business|other vocabulary.
_FAMILY_TYPES = {
    "family",
    "family_member",
    "relative",
    "parent",
    "child",
    "spouse",
    "partner",
    "sibling",
    "cousin",
    "in_law",
    "stepfamily",
    "grandparent",
    "grandchild",
    "uncle",
    "aunt",
    "nephew",
    "niece",
}
_BUSINESS_TYPES = {
    "business",
    "business_partner",
    "businesspartner",
    "colleague",
    "coworker",
    "work",
    "employer",
    "employee",
    "company",
    "co_owner",
    "client",
    "supplier",
    "accountant",
}


def _normalize_relation_type(relationship_type: str | None) -> str:
    value = (relationship_type or "related").strip().lower()
    if value in _FAMILY_TYPES:
        return "family"
    if value in _BUSINESS_TYPES:
        return "business"
    return "other"


def _other_id(subject_id: str, row) -> str:
    return row.related_subject_id if row.subject_id == subject_id else row.subject_id


def _sorted_pair_id(a: str, b: str) -> str:
    return f"{min(a, b)}-{max(a, b)}"


@cms_bp.route("/subjects/<subject_id>/relationships")
@login_required
@subject_access_required
def get_subject_relationships(subject_id: str) -> flask.Response:
    """Get relationship network data for a subject."""
    try:
        subject = db.session.get(Subject, subject_id) or abort(404)

        # Single-row storage per pair (ADR-0001 PR3): a relation may be stored
        # with this subject on either side of the canonical pair.
        related_rows = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == subject.id)
                | (subject_relations.c.related_subject_id == subject.id)
            )
        ).fetchall()

        related_ids = list({_other_id(subject.id, row) for row in related_rows})
        related = (
            apply_tenant_filter(
                Subject.query.filter(
                    Subject.id.in_(related_ids), Subject.is_deleted == False
                ),
                Subject,
            ).all()
            if related_ids
            else []
        )
        related_by_id = {s.id: s for s in related}

        # Build nodes and edges for visualization
        nodes = [
            {
                "id": subject.id,
                "name": subject.name,
                "type": subject.subject_type,
                "isMain": True,
            }
        ]

        edges = []
        edge_ids = set()

        for row in related_rows:
            rel_id = _other_id(subject.id, row)
            rel = related_by_id.get(rel_id)
            if rel is None:
                continue
            if not any(n["id"] == rel.id for n in nodes):
                nodes.append(
                    {
                        "id": rel.id,
                        "name": rel.name,
                        "type": rel.subject_type,
                        "isMain": False,
                    }
                )
            edge_id = _sorted_pair_id(subject.id, rel.id)
            if edge_id not in edge_ids:
                edges.append(
                    {
                        "id": edge_id,
                        "source": subject.id,
                        "target": rel.id,
                        "type": row.relation_type or "related",
                    }
                )
                edge_ids.add(edge_id)

        # Get second-degree connections (friends of friends)
        second_degree_rows = []
        for row in related_rows:
            rel_id = _other_id(subject.id, row)
            if rel_id not in related_by_id:
                continue
            second_degree_rows.extend(
                db.session.execute(
                    subject_relations.select().where(
                        (subject_relations.c.subject_id == rel_id)
                        | (subject_relations.c.related_subject_id == rel_id)
                    )
                ).fetchall()
            )

        second_ids = {
            _other_id(row.subject_id, row)
            for row in second_degree_rows
            if _other_id(row.subject_id, row) not in (subject.id, *related_ids)
        }
        rel_related = (
            apply_tenant_filter(
                Subject.query.filter(
                    Subject.id.in_(second_ids),
                    Subject.is_deleted == False,
                ),
                Subject,
            ).all()
            if second_ids
            else []
        )
        rel_related_by_id = {s.id: s for s in rel_related}

        for row in second_degree_rows:
            pair_a = row.subject_id
            pair_b = row.related_subject_id
            # Only draw edges that involve a first-degree subject
            first_degree = None
            other = None
            for candidate in (pair_a, pair_b):
                if candidate in related_by_id:
                    first_degree = candidate
                elif candidate in rel_related_by_id:
                    other = candidate
            if first_degree is None or other is None:
                continue

            rr = rel_related_by_id.get(other)
            if not any(n["id"] == rr.id for n in nodes):
                nodes.append(
                    {
                        "id": rr.id,
                        "name": rr.name,
                        "type": rr.subject_type,
                        "isMain": False,
                    }
                )

            edge_id = _sorted_pair_id(first_degree, rr.id)
            if edge_id not in edge_ids:
                edges.append(
                    {
                        "id": edge_id,
                        "source": first_degree,
                        "target": rr.id,
                        "type": row.relation_type or "connected",
                    }
                )
                edge_ids.add(edge_id)

        return jsonify(
            {
                "subject": {
                    "id": subject.id,
                    "name": subject.name,
                    "type": subject.subject_type,
                },
                "nodes": nodes,
                "edges": edges,
            }
        )
    except Exception:
        logger.exception("Error in get_subject_relationships")
        return jsonify({"error": "Internal server error"}), 500


@cms_bp.route("/subjects/<subject_id>/add-relationship", methods=["POST"])
@login_required
@subject_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@validate(AddRelationSchema)
def add_subject_relationship(subject_id: str) -> flask.Response:
    """Add a bidirectional relationship between two subjects."""
    try:
        subject = db.session.get(Subject, subject_id) or abort(404)
        related_id = request.validated_data.get("related_subject_id")
        relationship_type = request.validated_data.get("relationship_type", "related")
        relation_type = _normalize_relation_type(relationship_type)

        if not related_id:
            return api_error("Related subject ID required", 400)

        if related_id == subject_id:
            return api_error("Cannot create relationship with self", 400)

        related = db.session.get(Subject, related_id)
        if not related:
            return api_error("Related subject not found", 404)
        if related.tenant_id != current_user.tenant_id:
            return api_error("Related subject not found", 404)

        # Single canonical row per pair (ADR-0001 PR3); direction = mutual.
        canonical_a, canonical_b = sorted([subject.id, related_id])
        existing = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == canonical_a)
                & (subject_relations.c.related_subject_id == canonical_b)
            )
        ).first()

        if existing:
            return api_error("Relationship already exists", 400)

        db.session.execute(
            subject_relations.insert().values(
                subject_id=canonical_a,
                related_subject_id=canonical_b,
                relation_type=relation_type,
                direction="mutual",
                status="candidate",
            )
        )

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="subject_relation",
            entity_id=f"{canonical_a}-{canonical_b}",
            ip_address=request.remote_addr,
            description=f"Added bidirectional {relation_type} relationship between {subject.name} and {related.name}",
        )
        db.session.commit()

        return jsonify(
            {
                "message": "Relationship added",
                "relationship": {
                    "subject_id": subject.id,
                    "related_subject_id": related_id,
                    "type": relation_type,
                    "bidirectional": True,
                },
            }
        )
    except Exception:
        db.session.rollback()
        logger.exception("Error adding relationship")
        return jsonify({"error": "Internal server error"}), 500


@cms_bp.route("/subjects/<subject_id>/remove-relationship", methods=["POST"])
@login_required
@subject_access_required
@roles_required("admin", "owner", "senior_investigator")
@validate(RemoveRelationSchema)
def remove_subject_relationship(subject_id: str) -> flask.Response:
    """Remove a relationship between two subjects."""
    try:
        subject = db.session.get(Subject, subject_id) or abort(404)
        related_id = request.validated_data.get("related_subject_id")

        if not related_id:
            return api_error("Related subject ID required", 400)

        db.session.execute(
            subject_relations.delete().where(
                (
                    (subject_relations.c.subject_id == subject.id)
                    & (subject_relations.c.related_subject_id == related_id)
                )
                | (
                    (subject_relations.c.subject_id == related_id)
                    & (subject_relations.c.related_subject_id == subject.id)
                )
            )
        )

        AuditLog.log(
            user_id=current_user.id,
            action="delete",
            entity_type="subject_relation",
            entity_id=f"{subject.id}-{related_id}",
            ip_address=request.remote_addr,
            description=f"Removed relationship between {subject.name} and subject {related_id}",
        )
        db.session.commit()

        return api_success({}, "Relationship removed")
    except Exception:
        db.session.rollback()
        logger.exception("Error removing relationship")
        return jsonify({"error": "Internal server error"}), 500
