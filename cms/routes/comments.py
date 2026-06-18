import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Comment, CommentEditHistory, AuditLog
from ..validation import validate, CreateCommentSchema, UpdateCommentSchema

from .response import api_success, api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/api/comments", methods=["POST"])
@login_required
@validate(CreateCommentSchema)
def create_comment() -> flask.Response:
    """Create a new comment on any entity."""
    if not request.validated_data.get("content"):
        return api_error("Content is required", 400)

    # At least one entity must be specified
    entity_ids = {
        "case_id": request.validated_data.get("case_id"),
        "subject_id": request.validated_data.get("subject_id"),
        "client_id": request.validated_data.get("client_id"),
        "financial_record_id": request.validated_data.get("financial_record_id"),
    }

    if not any(entity_ids.values()):
        return api_error("At least one entity ID is required", 400)

    comment = Comment(
        content=request.validated_data["content"],
        comment_type=request.validated_data.get("comment_type", "note"),
        is_pinned=bool(request.validated_data.get("is_pinned", False)),
        author_id=current_user.id,
        **entity_ids,
    )

    db.session.add(comment)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="comment",
        entity_id=comment.id,
        ip_address=request.remote_addr,
        case_id=request.validated_data.get("case_id"),
        description=f"Added comment on {request.validated_data.get('case_id') and 'case' or request.validated_data.get('subject_id') and 'subject' or request.validated_data.get('client_id') and 'client' or 'entity'}",
    )
    db.session.commit()

    return jsonify(comment.to_dict()), 201


@cms_bp.route("/api/comments/<comment_id>", methods=["PUT"])
@login_required
@validate(UpdateCommentSchema)
def update_comment(comment_id: str) -> flask.Response:
    """Update a comment."""
    comment = db.session.get(Comment, comment_id) or abort(404)

    # Only author or admin can edit
    if comment.author_id != current_user.id and not current_user.is_admin:
        return api_error("Not authorized to edit this comment", 403)

    data = request.validated_data
    content_changed = False

    if "content" in data and data["content"] != comment.content:
        CommentEditHistory(
            comment_id=comment.id,
            previous_content=comment.content,
            new_content=data["content"],
            edited_by_id=current_user.id,
            edited_at=datetime.now(timezone.utc),
        )
        comment.content = data["content"]
        comment.edit_count = (comment.edit_count or 0) + 1
        comment.last_edited_by_id = current_user.id
        comment.last_edited_at = datetime.now(timezone.utc)
        content_changed = True

    if "is_pinned" in data:
        comment.is_pinned = data["is_pinned"]

    if "is_resolved" in data:
        comment.is_resolved = data["is_resolved"]

    comment.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    if content_changed:
        AuditLog.log(
            user_id=current_user.id,
            action="comment_edit",
            entity_type="comment",
            entity_id=comment.id,
            ip_address=request.remote_addr,
            case_id=comment.case_id,
            description=f"Edited comment (edit #{comment.edit_count})",
        )
        db.session.commit()

    return jsonify(comment.to_dict())


@cms_bp.route("/api/comments/<comment_id>", methods=["DELETE"])
@login_required
def delete_comment(comment_id: str) -> flask.Response:
    """Delete a comment."""
    comment = db.session.get(Comment, comment_id) or abort(404)

    # Only author or admin can delete
    if comment.author_id != current_user.id and not current_user.is_admin:
        return api_error("Not authorized to delete this comment", 403)

    comment.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="comment",
        entity_id=comment_id,
        ip_address=request.remote_addr,
        description="Deleted comment",
    )
    db.session.commit()

    return api_success({}, "Comment deleted")


@cms_bp.route("/api/comments/for-entity")
@login_required
def get_comments_for_entity() -> flask.Response:
    """Get all comments for a specific entity."""
    entity_type = request.args.get("type")  # case, subject, client, financial_record
    entity_id = request.args.get("id")

    query = Comment.query.filter_by(is_deleted=False)

    if entity_type == "case" and entity_id:
        query = query.filter_by(case_id=entity_id)
    elif entity_type == "subject" and entity_id:
        query = query.filter_by(subject_id=entity_id)
    elif entity_type == "client" and entity_id:
        query = query.filter_by(client_id=entity_id)
    elif entity_type == "financial_record" and entity_id:
        query = query.filter_by(financial_record_id=entity_id)
    else:
        return api_error("Invalid entity type or missing ID", 400)

    comments = query.order_by(Comment.is_pinned.desc(), Comment.created_at.desc()).all()

    return jsonify(
        {"comments": [c.to_dict() for c in comments], "count": len(comments)}
    )


@cms_bp.route("/api/comments/count")
@login_required
def get_comment_count() -> flask.Response:
    """Get comment count for a specific entity."""
    entity_type = request.args.get("type")
    entity_id = request.args.get("id")

    query = Comment.query.filter_by(is_deleted=False)

    if entity_type == "case" and entity_id:
        count = query.filter_by(case_id=entity_id).count()
    elif entity_type == "subject" and entity_id:
        count = query.filter_by(subject_id=entity_id).count()
    elif entity_type == "client" and entity_id:
        count = query.filter_by(client_id=entity_id).count()
    else:
        count = 0

    return jsonify({"count": count})
