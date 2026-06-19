import csv
import io
import logging
from datetime import datetime

from flask_babel import gettext
from flask import Response, request, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from . import cms_bp
from ..models import db, Case, Subject, Client, Finding, Address, case_subjects
from ..auth import can_export, case_access_required, apply_tenant_filter
from ..rate_limiting import rate_limit, STRICT_RATE_LIMIT

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/cases/<case_id>/export")
@login_required
@case_access_required
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="export_case")
def export_case(case_id: str) -> str:
    """Export case data as CSV."""
    from ..tier_limits import check_feature

    if not check_feature("export"):
        return api_error(
            "Export is not available on your current plan. Upgrade to access this feature.",
            403,
        )
    case = db.session.get(Case, case_id) or abort(404)
    format_type = request.args.get("format", "csv")

    if format_type == "csv":
        return export_case_csv(case)
    else:
        return api_error("Unsupported format. Use csv.", 400)


def export_case_csv(case: Case) -> Response:
    """Generate CSV export of case data."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Case info
    writer.writerow([gettext("Case Report")])
    writer.writerow(["Case Number", case.case_number])
    writer.writerow(["Title", case.title])
    writer.writerow(["Client", case.client.name if case.client else "N/A"])
    writer.writerow(["Status", case.status])
    writer.writerow(["Priority", case.priority])
    writer.writerow(["Start Date", case.start_date])
    writer.writerow(["Target End Date", case.target_end_date or "N/A"])
    writer.writerow(["Actual End Date", case.actual_end_date or "N/A"])
    writer.writerow(["Description", case.description or "N/A"])
    writer.writerow(["Case Type", case.case_type or "N/A"])
    writer.writerow(["Jurisdiction", case.jurisdiction or "N/A"])
    writer.writerow([])

    # Subjects
    writer.writerow(["Subjects"])
    writer.writerow(["Name", "Type", "Risk Score", "Email", "Phone", "Address"])
    for subject in case.subjects.all():
        subject.decrypt_identifiers()
        writer.writerow(
            [
                subject.name,
                subject.subject_type,
                subject.risk_score,
                subject.email or "N/A",
                subject.phone or "N/A",
                subject.address or "N/A",
            ]
        )
    writer.writerow([])

    # Findings
    writer.writerow(["Findings"])
    writer.writerow(["Title", "Type", "Reliability", "Created", "Content"])
    for finding in case.findings.filter_by(is_deleted=False).all():
        writer.writerow(
            [
                finding.title,
                finding.finding_type or "N/A",
                finding.reliability_score or "N/A",
                finding.created_at.strftime("%Y-%m-%d %H:%M"),
                (finding.content or "")[:200],
            ]
        )
    writer.writerow([])

    # Financial Records
    writer.writerow(["Financial Records"])
    writer.writerow(["Date", "Amount", "Type", "Counterparty", "Description"])
    for record in case.financial_records.filter_by(is_deleted=False).all():
        writer.writerow(
            [
                record.transaction_date.strftime("%Y-%m-%d"),
                record.amount,
                record.transaction_type,
                record.counterparty_name or "N/A",
                (record.description or "")[:200],
            ]
        )

    output.seek(0)
    filename = f"case_{case.case_number.replace('-', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@cms_bp.route("/subjects/export")
@login_required
@can_export
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="export_subjects")
def export_subjects() -> str:
    """Export all subjects as CSV."""
    from ..tier_limits import check_feature

    if not check_feature("export"):
        return api_error(
            "Export is not available on your current plan. Upgrade to access this feature.",
            403,
        )
    format_type = request.args.get("format", "csv")

    if format_type == "csv":
        return export_subjects_csv()
    else:
        return api_error("Unsupported format. Use csv.", 400)


def export_subjects_csv() -> Response:
    """Generate CSV export of all subjects."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "Name",
            "Type",
            "Risk Score",
            "Email",
            "Phone",
            "Address (old)",
            "Street",
            "Number",
            "Zipcode",
            "Town",
            "Country",
            "Address Kadaster Verified",
            "Notes",
            "Created",
        ]
    )

    subject_addresses: dict = {}
    for addr in (
        apply_tenant_filter(
            Address.query.filter(Address.subject_id.isnot(None)),
            Address,
        )
        .order_by(Address.is_primary.desc())
        .yield_per(500)
    ):
        subject_addresses.setdefault(addr.subject_id, []).append(addr)

    q = apply_tenant_filter(
        Subject.query.filter_by(is_deleted=False), Subject
    ).order_by(Subject.name)
    for subject in q.yield_per(200):
        subject.decrypt_identifiers()
        addrs = subject_addresses.get(subject.id, [])
        primary_addr = next((a for a in addrs if a.is_primary), None)
        if primary_addr:
            primary_addr.decrypt_fields()

        writer.writerow(
            [
                subject.name,
                subject.subject_type,
                subject.risk_score,
                subject.email or "",
                subject.phone or "",
                subject.address or "",
                primary_addr.street or "" if primary_addr else "",
                primary_addr.number or "" if primary_addr else "",
                primary_addr.zipcode or "" if primary_addr else "",
                primary_addr.town or "" if primary_addr else "",
                primary_addr.country or "" if primary_addr else "",
                "Yes" if primary_addr and primary_addr.kadaster_verified else "No",
                (subject.notes or "")[:200],
                subject.created_at.strftime("%Y-%m-%d"),
            ]
        )

    output.seek(0)
    filename = f"subjects_export_{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@cms_bp.route("/clients/export")
@login_required
@can_export
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="export_clients")
def export_clients() -> str:
    """Export all clients as CSV."""
    from ..tier_limits import check_feature

    if not check_feature("export"):
        return api_error(
            "Export is not available on your current plan. Upgrade to access this feature.",
            403,
        )
    format_type = request.args.get("format", "csv")

    if format_type == "csv":
        return export_clients_csv()
    else:
        return api_error("Unsupported format. Use csv.", 400)


def export_clients_csv() -> Response:
    """Generate CSV export of all clients."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "Name",
            "Type",
            "Contact Person",
            "Email",
            "Phone",
            "Contract Number",
            "Active",
            "Created",
        ]
    )

    client_count = apply_tenant_filter(
        Client.query.filter_by(is_deleted=False), Client
    ).count()
    if client_count > 5000:
        logger.warning(
            "Large client export (%d records) triggered by %s",
            client_count,
            current_user.username,
        )
    for client in (
        apply_tenant_filter(Client.query.filter_by(is_deleted=False), Client)
        .order_by(Client.name)
        .yield_per(200)
    ):
        client.decrypt_naw()
        writer.writerow(
            [
                client.name,
                "Company" if client.is_company else "Individual",
                client.contact_person or "",
                client.contact_email or "",
                client.contact_phone or "",
                client.contract_number or "",
                "Yes" if client.is_active else "No",
                client.created_at.strftime("%Y-%m-%d"),
            ]
        )

    output.seek(0)
    filename = f"clients_export_{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@cms_bp.route("/cases/export")
@login_required
@can_export
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="export_cases")
def export_cases() -> str:
    """Export all cases as CSV."""
    from ..tier_limits import check_feature

    if not check_feature("export"):
        return api_error(
            "Export is not available on your current plan. Upgrade to access this feature.",
            403,
        )
    format_type = request.args.get("format", "csv")

    if format_type == "csv":
        return export_cases_csv()
    else:
        return api_error("Unsupported format. Use csv.", 400)


def export_cases_csv() -> Response:
    """Generate CSV export of all cases."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "Case Number",
            "Title",
            "Client",
            "Status",
            "Priority",
            "Start Date",
            "End Date",
            "Type",
            "Subjects Count",
            "Findings Count",
        ]
    )

    case_count = apply_tenant_filter(
        Case.query.filter_by(is_deleted=False), Case
    ).count()
    if case_count > 5000:
        logger.warning(
            "Large cases export (%d records) triggered by %s",
            case_count,
            current_user.username,
        )

    subject_counts = dict(
        db.session.query(
            case_subjects.c.case_id, func.count(case_subjects.c.subject_id)
        )
        .group_by(case_subjects.c.case_id)
        .all()
    )
    finding_counts = dict(
        apply_tenant_filter(
            db.session.query(Finding.case_id, func.count(Finding.id))
            .filter(Finding.is_deleted == False)
            .group_by(Finding.case_id),
            Finding,
        ).all()
    )

    for case in (
        apply_tenant_filter(Case.query.filter_by(is_deleted=False), Case)
        .order_by(Case.case_number)
        .yield_per(200)
    ):
        writer.writerow(
            [
                case.case_number,
                case.title,
                case.client.name if case.client else "N/A",
                case.status,
                case.priority,
                case.start_date.strftime("%Y-%m-%d"),
                case.actual_end_date.strftime("%Y-%m-%d")
                if case.actual_end_date
                else "",
                case.case_type or "",
                subject_counts.get(case.id, 0),
                finding_counts.get(case.id, 0),
            ]
        )

    output.seek(0)
    filename = f"cases_export_{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
