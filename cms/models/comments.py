from datetime import datetime, timezone
import uuid

from ..models import db


class Comment(db.Model):
    """
    Comment model for notes/discussions on any entity.

    Can be linked to: case, subject, client, or financial_record
    """

    __tablename__ = "comments"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )

    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), index=True)
    subject_id = db.Column(db.String(36), db.ForeignKey("subjects.id"), index=True)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), index=True)
    financial_record_id = db.Column(
        db.String(36), db.ForeignKey("financial_records.id"), index=True
    )

    content = db.Column(db.Text, nullable=False)

    comment_type = db.Column(db.String(20), default="note")
    is_pinned = db.Column(db.Boolean, default=False)
    is_resolved = db.Column(db.Boolean, default=False)

    author_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    author = db.relationship("User", foreign_keys=[author_id], backref="comments")

    edit_count = db.Column(db.Integer, default=0)
    last_edited_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    last_edited_by = db.relationship(
        "User", foreign_keys=[last_edited_by_id], backref="edited_comments"
    )
    last_edited_at = db.Column(db.DateTime)
    edit_history = db.relationship(
        "CommentEditHistory",
        backref="comment",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "client_id": self.client_id,
            "financial_record_id": self.financial_record_id,
            "content": self.content,
            "comment_type": self.comment_type,
            "is_pinned": self.is_pinned,
            "is_resolved": self.is_resolved,
            "author_id": self.author_id,
            "author_name": self.author.full_name if self.author else "Unknown",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "edit_count": self.edit_count,
            "last_edited_by_id": self.last_edited_by_id,
            "last_edited_by_name": self.last_edited_by.full_name
            if self.last_edited_by
            else None,
            "last_edited_at": self.last_edited_at.isoformat()
            if self.last_edited_at
            else None,
            "edit_history": [
                h.to_dict()
                for h in self.edit_history.order_by(CommentEditHistory.edited_at.desc())
                .limit(10)
                .all()
            ],
        }


class CommentEditHistory(db.Model):
    """
    Audit trail for comment edits.
    Stores each version of a comment when it is edited.
    """

    __tablename__ = "comment_edit_history"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )

    comment_id = db.Column(
        db.String(36), db.ForeignKey("comments.id"), nullable=False, index=True
    )

    previous_content = db.Column(db.Text, nullable=False)
    new_content = db.Column(db.Text, nullable=False)

    edited_by_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    edited_by = db.relationship("User", backref="comment_edits")

    edited_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "comment_id": self.comment_id,
            "previous_content": self.previous_content,
            "new_content": self.new_content,
            "edited_by_id": self.edited_by_id,
            "edited_by_name": self.edited_by.full_name if self.edited_by else "Unknown",
            "edited_at": self.edited_at.isoformat() if self.edited_at else None,
        }
