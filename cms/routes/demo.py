import logging

from flask import render_template
from flask_login import login_required

from . import cms_bp

logger = logging.getLogger(__name__)


@cms_bp.route("/demo")
@login_required
def demo() -> str:
    """Design system demo page with all component types."""
    demo_data = {
        "page_title": "Design System Demo",
        "client": {
            "name": "De Vries Automotive B.V.",
            "is_company": True,
            "contact_person": "Jan de Vries",
            "is_active": True,
            "contract_number": "C-2024-0042",
            "vat_number": "NL861234567B01",
            "bank_account": "NL39INGB0001234567",
            "social_security_number": "••• ••• 789",
            "date_of_birth": "1985-03-14",
            "place_of_birth": "Amsterdam",
            "financial_notes": "Betalingstermijn 30 dagen. Laatste factuur verstuurd op 15-05-2026.",
            "contact_email": "info@devriesautomotive.nl",
            "contact_phone": "+31612345678",
        },
        "addresses": [
            {
                "id": "addr-1",
                "street": "Stationsweg",
                "number": "42",
                "zipcode": "1012AB",
                "town": "Amsterdam",
                "country": "Netherlands",
                "is_primary": True,
                "kadaster_verified": True,
            },
            {
                "id": "addr-2",
                "street": "Industrielaan",
                "number": "15",
                "zipcode": "2713CD",
                "town": "Zoetermeer",
                "country": "Netherlands",
                "is_primary": False,
                "kadaster_verified": False,
            },
        ],
        "cases": [
            {
                "id": "case-1",
                "case_number": "C-2026-001",
                "title": "Fraudeonderzoek De Vries Automotive",
                "status": "active",
                "priority": "high",
                "updated_at": "2026-05-28",
            },
            {
                "id": "case-2",
                "case_number": "C-2026-008",
                "title": "Bijrijderongeval A2 — getuigenverhoor",
                "status": "open",
                "priority": "critical",
                "updated_at": "2026-05-30",
            },
            {
                "id": "case-3",
                "case_number": "C-2026-015",
                "title": "Verzekeringsclaim 2026-015 Schade Expertise",
                "status": "active",
                "priority": "medium",
                "updated_at": "2026-05-25",
            },
            {
                "id": "case-4",
                "case_number": "C-2026-022",
                "title": "Digitale forensische analyse bedrijfsnetwerk",
                "status": "suspended",
                "priority": "low",
                "updated_at": "2026-05-20",
            },
            {
                "id": "case-5",
                "case_number": "C-2025-112",
                "title": "Personenonderzoek Vermissing",
                "status": "closed",
                "priority": "high",
                "updated_at": "2026-04-15",
            },
        ],
        "stats": {
            "open_cases": 3,
            "active_cases": 7,
            "suspended_cases": 1,
            "closed_cases": 24,
            "total_clients": 18,
            "total_subjects": 42,
            "total_findings": 156,
        },
        "health": {
            "database": "ok",
            "spiderfoot": "ok",
            "rdw": "ok",
            "kadaster": "ok",
            "hibp": "ok",
            "overheid": "no key configured",
            "brave": "ok",
        },
        "phones": ["+31612345678", "+31698765432"],
        "emails": ["info@devriesautomotive.nl", "jan@devriesautomotive.nl"],
        "reminders": [
            {
                "title": "Betalingstermijn verloopt",
                "date": "2026-06-02 12:00",
                "is_overdue": True,
            },
            {
                "title": "Getuigenverhoor plannen",
                "date": "2026-06-05 09:00",
                "is_overdue": False,
            },
        ],
        "findings": [
            {
                "id": "f-1",
                "title": "KvK-uittreksel — oprichtingsdatum 2018",
                "subject": "De Vries Automotive B.V.",
                "source_type": "manual",
                "author": "Lisa van Dijk",
                "created_at": "2026-05-28 14:30",
                "source_url": None,
            },
            {
                "id": "f-2",
                "title": "LinkedIn profiel Jan de Vries — management team",
                "subject": "Jan de Vries",
                "source_type": "osint",
                "author": "Piet Jansen",
                "created_at": "2026-05-27 09:15",
                "source_url": "https://linkedin.com/in/jandevries",
            },
            {
                "id": "f-3",
                "title": "Betalingsgeschiedenis ING rekening 2024-2026",
                "subject": "De Vries Automotive B.V.",
                "source_type": "document",
                "author": "Lisa van Dijk",
                "created_at": "2026-05-25 11:00",
                "source_url": None,
            },
            {
                "id": "f-4",
                "title": "Nieuwsartikel — inval FIOD 12 maart 2026",
                "subject": "De Vries Automotive B.V.",
                "source_type": "osint",
                "author": "Piet Jansen",
                "created_at": "2026-05-22 16:45",
                "source_url": "https://nieuws.nl/fiod-inval-devries",
            },
            {
                "id": "f-5",
                "title": "Telefoonnummer +31698765432 gelinkt aan verdachte transacties",
                "subject": "Jan de Vries",
                "source_type": "osint",
                "author": "Piet Jansen",
                "created_at": "2026-05-20 08:00",
                "source_url": None,
            },
        ],
    }
    return render_template("cms/demo.html", **demo_data)
