import logging
import os
from datetime import datetime, timezone

import flask
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..validation import (
    validate,
    CreateTemplateSchema,
    EditTemplateSchema,
    RenderPreviewSchema,
    GenerateReportSchema,
)
from ..models import db, Case, DocumentTemplate, Document, AuditLog
from ..auth import (
    roles_required,
    case_access_required,
    apply_tenant_filter,
    ensure_tenant_access,
)

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/templates")
@login_required
def list_templates() -> str:
    """List all document templates."""
    query = DocumentTemplate.query.filter_by(is_active=True)
    query = apply_tenant_filter(query, DocumentTemplate)
    templates = query.order_by(DocumentTemplate.name).all()
    return render_template("cms/templates/list.html", templates=templates)


@cms_bp.route("/templates/create", methods=["GET", "POST"])
@login_required
@roles_required("admin", "senior_investigator")
@validate(CreateTemplateSchema)
def create_template() -> flask.Response:
    """Create a new document template."""
    if request.method == "POST":
        data = request.validated_data

        template = DocumentTemplate(
            name=data["name"],
            description=data.get("description"),
            template_type=data.get("template_type", "report"),
            content=data["content"],
            category=data.get("category"),
            is_default=bool(data.get("is_default")),
            created_by=current_user.id,
        )

        db.session.add(template)

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="document_template",
            entity_id=template.id,
            ip_address=request.remote_addr,
            description=f"Created document template: {template.name}",
        )
        db.session.commit()

        if request.is_json:
            return jsonify(template.to_dict()), 201

        flash(f'Template "{template.name}" created.', "success")
        return redirect(url_for("cms.list_templates"))

    return render_template("cms/templates/create.html")


@cms_bp.route("/templates/<template_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "senior_investigator")
@validate(EditTemplateSchema)
def edit_template(template_id: str) -> flask.Response:
    """Edit a document template."""
    template = db.session.get(DocumentTemplate, template_id) or abort(404)
    ensure_tenant_access(template)

    if request.method == "POST":
        data = request.validated_data

        template.name = data["name"]
        template.description = data.get("description")
        template.template_type = data.get("template_type", "report")
        template.content = data["content"]
        template.category = data.get("category")
        template.is_default = bool(data.get("is_default"))
        template.updated_at = datetime.now(timezone.utc)

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="document_template",
            entity_id=template.id,
            ip_address=request.remote_addr,
            description=f"Updated document template: {template.name}",
        )
        db.session.commit()

        if request.is_json:
            return jsonify(template.to_dict())

        flash(f'Template "{template.name}" updated.', "success")
        return redirect(url_for("cms.list_templates"))

    return render_template("cms/templates/edit.html", template=template)


@cms_bp.route("/templates/<template_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_template(template_id: str) -> flask.Response:
    """Delete a document template."""
    template = db.session.get(DocumentTemplate, template_id) or abort(404)
    ensure_tenant_access(template)

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="document_template",
        entity_id=template.id,
        ip_address=request.remote_addr,
        description=f"Deleted document template: {template.name}",
    )

    db.session.delete(template)
    db.session.commit()

    flash("Template deleted.", "success")
    return redirect(url_for("cms.list_templates"))


@cms_bp.route("/templates/<template_id>/preview")
@login_required
def preview_template(template_id: str) -> flask.Response:
    """Preview a template with sample data."""
    template = db.session.get(DocumentTemplate, template_id) or abort(404)
    ensure_tenant_access(template)

    # Build sample context
    context = _build_report_context(None)
    rendered = template.render(context)

    return jsonify({"rendered": rendered})


@cms_bp.route("/cases/<case_id>/generate-report", methods=["GET", "POST"])
@login_required
@case_access_required
@validate(GenerateReportSchema)
def generate_case_report(case_id: str) -> flask.Response:
    """Generate a report from a template for a specific case."""
    case = db.session.get(Case, case_id) or abort(404)
    ensure_tenant_access(case)

    tmpl_query = DocumentTemplate.query.filter_by(is_active=True)
    tmpl_query = apply_tenant_filter(tmpl_query, DocumentTemplate)
    templates = tmpl_query.order_by(DocumentTemplate.name).all()

    if request.method == "POST":
        vd = request.validated_data
        template_id = vd.get("template_id")
        if not template_id:
            if request.is_json:
                return jsonify(
                    {
                        "error": "Validation failed",
                        "details": [
                            {"field": "template_id", "message": "Template is required"}
                        ],
                    }
                ), 400
            flash("Please select a template.", "danger")
            return render_template(
                "cms/reports/generate.html", case=case, templates=templates
            )

        custom_fields = {
            "conclusion": vd.get("conclusion", ""),
            "recommendation": vd.get("recommendation", ""),
            "classification": vd.get("classification", "Confidential"),
        }

        template = db.session.get(DocumentTemplate, template_id) or abort(404)
        ensure_tenant_access(template)

        # Build context from case
        context = _build_report_context(case)
        context.update(custom_fields)
        context["user"] = current_user

        rendered = template.render(context)

        # Save as document
        doc = Document(
            case_id=case.id,
            filename=f"report_{case.case_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            original_filename=f"{template.name}_{case.case_number}.txt",
            mime_type="text/plain",
            file_size=len(rendered.encode("utf-8")),
            document_type="report",
            description=f"Generated from template: {template.name}",
            classification="confidential",
            uploaded_by=current_user.id,
        )
        db.session.add(doc)
        db.session.flush()

        # Save the report content
        doc_path = f"static/uploads/{doc.filename}"
        os.makedirs(os.path.dirname(doc_path), exist_ok=True)
        with open(doc_path, "w") as f:
            f.write(rendered)

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="document",
            entity_id=doc.id,
            ip_address=request.remote_addr,
            case_id=case.id,
            description=f"Generated report: {template.name}",
        )
        db.session.commit()

        flash("Report generated and saved.", "success")
        return redirect(url_for("cms.view_case", case_id=case.id))

    return render_template(
        "cms/templates/generate_report.html", case=case, templates=templates
    )


def _build_report_context(case: Case) -> dict:
    """Build context dictionary for template rendering."""
    context = {
        "case": None,
        "client": None,
        "subjects": [],
        "findings": [],
        "financials": {"summary": {}, "by_type": {}},
        "user": current_user,
        "now": datetime.now(timezone.utc),
    }

    if case:
        case.decrypt_all() if hasattr(case, "decrypt_all") else None

        context["case"] = {
            "case_number": case.case_number,
            "title": case.title,
            "description": case.description,
            "case_type": case.case_type,
            "priority": case.priority,
            "status": case.status,
            "start_date": case.start_date,
            "target_end_date": case.target_end_date,
            "client": {"name": case.client.name} if case.client else None,
        }

        context["subjects"] = []
        for subject in case.subjects.all():
            subject.decrypt_identifiers()
            context["subjects"].append(
                {
                    "name": subject.name,
                    "subject_type": subject.subject_type,
                    "risk_score": subject.risk_score,
                    "address": subject.address,
                    "email": subject.email,
                    "phone": subject.phone,
                }
            )

        context["findings"] = []
        for finding in case.findings.filter_by(is_deleted=False).all():
            context["findings"].append(
                {
                    "title": finding.title,
                    "description": finding.content,  # Finding uses 'content' not 'description'
                    "finding_type": finding.finding_type,
                    # Map confidence_level to severity
                    "severity": finding.confidence_level or "medium",
                    "status": "active",
                }
            )

        # Financial summary
        fin_records = case.financial_records.filter_by(is_deleted=False).all()
        total = sum(r.amount for r in fin_records)
        by_type = {}
        for r in fin_records:
            if r.transaction_type not in by_type:
                by_type[r.transaction_type] = {"count": 0, "total": 0}
            by_type[r.transaction_type]["count"] += 1
            by_type[r.transaction_type]["total"] += float(r.amount)

        context["financials"] = {
            "summary": {"total_records": len(fin_records), "total_amount": total},
            "by_type": by_type,
        }

    return context


@cms_bp.route("/templates/api/all")
@login_required
def get_all_templates() -> flask.Response:
    """Get all templates as JSON."""
    query = DocumentTemplate.query.filter_by(is_active=True)
    query = apply_tenant_filter(query, DocumentTemplate)
    templates = query.order_by(DocumentTemplate.name).all()
    return jsonify({"templates": [t.to_dict() for t in templates]})


@cms_bp.route("/templates/api/render-preview", methods=["POST"])
@login_required
@validate(RenderPreviewSchema)
def render_template_preview() -> flask.Response:
    """Render a template preview with case data."""
    data = request.validated_data

    template_id = data.get("template_id")
    case_id = data.get("case_id")

    template = db.session.get(DocumentTemplate, template_id)
    if not template:
        return api_error("Template not found", 404)
    ensure_tenant_access(template)

    case = db.session.get(Case, case_id) if case_id else None
    if case:
        ensure_tenant_access(case)

    context = _build_report_context(case)
    context.update(
        {
            "conclusion": data.get("conclusion", ""),
            "recommendation": data.get("recommendation", ""),
            "classification": data.get("classification", "Confidential"),
        }
    )
    context["user"] = current_user

    rendered = template.render(context)

    return jsonify({"rendered": rendered})
