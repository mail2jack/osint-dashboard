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


@cms_bp.route("/subjects/<subject_id>/relationships")
@login_required
@subject_access_required
def get_subject_relationships(subject_id: str) -> flask.Response:
    """Get relationship network data for a subject."""
    try:
        subject = db.session.get(Subject, subject_id) or abort(404)

        # Get ALL relationships for this subject (both directions now)
        related_rows = db.session.execute(
            subject_relations.select().where(
                subject_relations.c.subject_id == subject.id
            )
        ).fetchall()

        # Build a map of related subjects
        related_ids = [row.related_subject_id for row in related_rows]
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
        edge_ids = set()  # Use sorted IDs to avoid duplicates

        # Helper to get sorted edge ID
        def sorted_edge_id(a, b):
            return f"{min(a, b)}-{max(a, b)}"

        for rel in related:
            nodes.append(
                {
                    "id": rel.id,
                    "name": rel.name,
                    "type": rel.subject_type,
                    "isMain": False,
                }
            )

            # Get relationship type from either direction
            rel_type = "related"
            type_rows = db.session.execute(
                subject_relations.select().where(
                    (subject_relations.c.subject_id == subject.id)
                    & (subject_relations.c.related_subject_id == rel.id)
                )
            ).fetchall()
            if not type_rows:
                # Check reverse direction
                type_rows = db.session.execute(
                    subject_relations.select().where(
                        (subject_relations.c.subject_id == rel.id)
                        & (subject_relations.c.related_subject_id == subject.id)
                    )
                ).fetchall()
            if type_rows:
                rel_type = type_rows[0].relationship_type or "related"

            edge_id = sorted_edge_id(subject.id, rel.id)
            if edge_id not in edge_ids:
                edges.append(
                    {
                        "id": edge_id,
                        "source": subject.id,
                        "target": rel.id,
                        "type": rel_type,
                    }
                )
                edge_ids.add(edge_id)

        # Get second-degree connections (friends of friends)
        for rel in related:
            second_degree_rows = db.session.execute(
                subject_relations.select().where(
                    subject_relations.c.subject_id == rel.id
                )
            ).fetchall()

            second_degree_ids = [
                row.related_subject_id
                for row in second_degree_rows
                if row.related_subject_id != subject.id
            ]
            rel_related = (
                apply_tenant_filter(
                    Subject.query.filter(
                        Subject.id.in_(second_degree_ids),
                        Subject.is_deleted == False,
                        Subject.id != subject.id,
                    ),
                    Subject,
                ).all()
                if second_degree_ids
                else []
            )

            for rr in rel_related:
                # Check if node already exists
                if not any(n["id"] == rr.id for n in nodes):
                    nodes.append(
                        {
                            "id": rr.id,
                            "name": rr.name,
                            "type": rr.subject_type,
                            "isMain": False,
                        }
                    )

                edge_id = sorted_edge_id(rel.id, rr.id)
                if edge_id not in edge_ids:
                    rel_type = "connected"
                    type_rows = db.session.execute(
                        subject_relations.select().where(
                            (subject_relations.c.subject_id == rel.id)
                            & (subject_relations.c.related_subject_id == rr.id)
                        )
                    ).fetchall()
                    if not type_rows:
                        # Check reverse direction
                        type_rows = db.session.execute(
                            subject_relations.select().where(
                                (subject_relations.c.subject_id == rr.id)
                                & (subject_relations.c.related_subject_id == rel.id)
                            )
                        ).fetchall()
                    if type_rows:
                        rel_type = type_rows[0].relationship_type or "connected"

                    edges.append(
                        {
                            "id": edge_id,
                            "source": rel.id,
                            "target": rr.id,
                            "type": rel_type,
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

        if not related_id:
            return api_error("Related subject ID required", 400)

        if related_id == subject_id:
            return api_error("Cannot create relationship with self", 400)

        related = db.session.get(Subject, related_id)
        if not related:
            return api_error("Related subject not found", 404)
        if related.tenant_id != current_user.tenant_id:
            return api_error("Related subject not found", 404)

        existing_a = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == subject.id)
                & (subject_relations.c.related_subject_id == related_id)
            )
        ).first()

        existing_b = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == related_id)
                & (subject_relations.c.related_subject_id == subject.id)
            )
        ).first()

        if existing_a or existing_b:
            return api_error("Relationship already exists", 400)

        db.session.execute(
            subject_relations.insert().values(
                subject_id=subject.id,
                related_subject_id=related_id,
                relationship_type=relationship_type,
            )
        )
        db.session.execute(
            subject_relations.insert().values(
                subject_id=related_id,
                related_subject_id=subject.id,
                relationship_type=relationship_type,
            )
        )

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="subject_relation",
            entity_id=f"{subject.id}-{related_id}",
            ip_address=request.remote_addr,
            description=f"Added bidirectional {relationship_type} relationship between {subject.name} and {related.name}",
        )
        db.session.commit()

        return jsonify(
            {
                "message": "Relationship added",
                "relationship": {
                    "subject_id": subject.id,
                    "related_subject_id": related_id,
                    "type": relationship_type,
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
