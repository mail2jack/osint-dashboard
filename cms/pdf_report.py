import os
import uuid
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors


def _add_watermark(canvas, doc):
    """Draw a subtle watermark on every page with viewer info."""
    canvas.saveState()
    canvas.setFillColor(colors.Color(0.7, 0.7, 0.7, alpha=0.3))
    canvas.setFont("Helvetica", 8)
    canvas.rotate(45)
    for y in range(-200, 1000, 200):
        for x in range(-200, 1000, 300):
            canvas.drawString(x, y, doc.watermark_text or "OSINT Dashboard")
    canvas.restoreState()


def generate_results_pdf(data, search_type, query, watermark_text=None):
    os.makedirs("reports", exist_ok=True)
    filename = f"reports/{search_type}_{query}_{uuid.uuid4().hex[:8]}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=letter)
    doc.watermark_text = watermark_text or "OSINT Dashboard"

    story = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"], fontSize=18, spaceAfter=20
    )
    heading_style = ParagraphStyle(
        "Heading", parent=styles["Heading2"], fontSize=14, spaceAfter=10
    )
    normal_style = styles["Normal"]

    search_title = {
        "email": f"Email OSINT Report: {query}",
        "username": f"Username OSINT Report: {query}",
        "social": f"Social Media Report: {query}",
        "ip": f"IP Lookup Report: {query}",
        "domain": f"Domain Lookup Report: {query}",
        "person": f"People Search Report: {query}",
    }.get(search_type, f"OSINT Report: {query}")

    story.append(Paragraph(search_title, title_style))
    story.append(
        Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_style
        )
    )
    story.append(Spacer(1, 20))

    if search_type == "email" or search_type == "username":
        found = data.get("findings", data.get("account_checks", []))
        found_accounts = [f for f in found if f.get("exists") == True]
        story.append(Paragraph(f"Found {len(found_accounts)} accounts", heading_style))
        story.append(Spacer(1, 10))
        if found_accounts:
            table_data = [["Platform", "URL"]]
            for f in found_accounts[:200]:
                url = f.get("url", f.get("profile_url", "N/A"))
                platform = f.get("site", f.get("platform", "Unknown"))
                table_data.append([platform, url])
            table = Table(table_data, colWidths=[2 * inch, 4 * inch])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.darkcyan),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                        ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                        ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
                        ("FONTSIZE", (0, 1), (-1, 1), 8),
                        ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ]
                )
            )
            story.append(table)

    elif search_type == "social":
        found = data.get("found", [])
        story.append(
            Paragraph(f"Found {len(found)} social media accounts", heading_style)
        )
        story.append(Spacer(1, 10))
        if found:
            table_data = [["Platform", "URL", "Status"]]
            for f in found[:200]:
                url = f.get("url", "N/A")
                platform = f.get("platform", "Unknown")
                status = f.get("status", "found")
                table_data.append([platform, url, status])
            table = Table(table_data, colWidths=[1.5 * inch, 3.5 * inch, 1 * inch])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.darkcyan),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                        ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                        ("FONTSIZE", (0, 1), (-1, 1), 8),
                        ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ]
                )
            )
            story.append(table)

    elif search_type == "ip":
        story.append(Paragraph("IP Information", heading_style))
        story.append(Spacer(1, 10))
        info_items = [
            ("IP Address", data.get("ip", "N/A")),
            ("Country", data.get("country", "N/A")),
            ("City", data.get("city", "N/A")),
            ("ISP", data.get("isp", "N/A")),
            ("ASN", data.get("asn", "N/A")),
            ("Hostname", data.get("hostname", "N/A")),
        ]
        for label, value in info_items:
            if value:
                story.append(Paragraph(f"<b>{label}:</b> {value}", normal_style))

    elif search_type == "domain":
        story.append(Paragraph("Domain Information", heading_style))
        story.append(Spacer(1, 10))
        if data.get("registrar"):
            story.append(
                Paragraph(f"<b>Registrar:</b> {data.get('registrar')}", normal_style)
            )
        if data.get("creation_date"):
            story.append(
                Paragraph(f"<b>Created:</b> {data.get('creation_date')}", normal_style)
            )
        if data.get("expiration_date"):
            story.append(
                Paragraph(
                    f"<b>Expires:</b> {data.get('expiration_date')}", normal_style
                )
            )
        if data.get("nameservers"):
            story.append(
                Paragraph(
                    f"<b>Name Servers:</b> {', '.join(data.get('nameservers', []))}",
                    normal_style,
                )
            )

    elif search_type == "person":
        if data.get("results"):
            story.append(Paragraph("Search Results", heading_style))
            for engine, results in data.get("results", {}).items():
                if results and results.get("results"):
                    story.append(Spacer(1, 10))
                    story.append(Paragraph(f"<b>{engine}:</b>", normal_style))
                    for r in results.get("results", [])[:10]:
                        story.append(
                            Paragraph(
                                f"- {r.get('title', 'N/A')}: {r.get('url', 'N/A')}",
                                normal_style,
                            )
                        )

    story.append(Spacer(1, 30))
    story.append(Paragraph("<i>Generated by OSINT Dashboard</i>", normal_style))

    doc.build(story, onFirstPage=_add_watermark, onLaterPages=_add_watermark)
    return filename


__all__ = ["generate_results_pdf"]
