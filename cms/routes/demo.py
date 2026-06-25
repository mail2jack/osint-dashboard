import logging
from datetime import datetime, timedelta

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


@cms_bp.route("/demo/case/kees")
@login_required
def demo_case_kees() -> str:
    """Demo case detail page for Kees."""
    return render_template("cms/demo_case.html", **_build_kees_case())


@cms_bp.route("/demo/workflow/personenonderzoek")
@login_required
def demo_workflow():
    """Interactive demo: full person investigation workflow."""
    now = datetime.now()
    return render_template("cms/demo_workflow.html", now=now)


def _build_kees_case() -> dict:
    """Build realistic demo data for 'Kees' case."""
    now = datetime.now()
    return {
        "case": {
            "case_number": "C-2026-042",
            "title": "Kees — Fraudeonderzoek & Identiteitsdiefstal",
            "status": "active",
            "priority": "critical",
            "client_name": "Schadeverzekering Nederland N.V.",
            "client_id": "client-svn",
            "lead_investigator": "Lisa van Dijk",
            "assigned_to": "Piet Jansen",
            "created_at": (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M"),
            "updated_at": now.strftime("%Y-%m-%d %H:%M"),
            "due_date": (now + timedelta(days=16)).strftime("%Y-%m-%d"),
            "description": (
                "Verdachte transacties en identiteitsdiefstal gemeld door "
                "Schadeverzekering Nederland N.V. Betrokkene 'Kees' heeft "
                "zich voorgedaan als meerdere personen om verzekeringsuitkeringen "
                "te frauderen. Mogelijk link met eerder gesloten zaak C-2025-089 "
                "(Marijkefraude)."
            ),
            "criminal_code": "Art. 326 Sr (Oplichting), Art. 225 Sr (Valsheid in geschrifte)",
            "tags": ["fraude", "identiteitsdiefstal", "verzekering", "urgent"],
            "closure_reason": "",
            "reopened_reason": "",
            "total_financial": 184_500.00,
            "total_transactions": 23,
        },
        "subjects": [
            {
                "id": "subj-kees",
                "name": "Kees de Vries",
                "subject_type": "person",
                "risk_score": 92,
                "identification_number": "BSN •••• 382",
                "social_accounts": [
                    {"platform": "LinkedIn", "url": "https://linkedin.com/in/keesdv"},
                    {
                        "platform": "Facebook",
                        "url": "https://facebook.com/kees.devries.92",
                    },
                    {"platform": "Instagram", "url": "https://instagram.com/keesdv"},
                ],
                "addresses": [
                    "Damrak 45, 1012LL Amsterdam",
                    "Jonkerlaan 12, 3701TC Zeist",
                ],
                "phone": "+31638472910",
                "email": "kees.devries@protonmail.com",
            },
            {
                "id": "subj-bv",
                "name": "Kees Holding B.V.",
                "subject_type": "company",
                "risk_score": 78,
                "identification_number": "KvK 87654321",
                "social_accounts": [],
                "addresses": ["Jonkerlaan 12, 3701TC Zeist"],
                "phone": "",
                "email": "info@keesholding.nl",
            },
            {
                "id": "subj-mede",
                "name": "Mevrouw J. de Vries-Wit",
                "subject_type": "person",
                "risk_score": 45,
                "identification_number": "BSN •••• 671",
                "social_accounts": [],
                "addresses": ["Damrak 45, 1012LL Amsterdam"],
                "phone": "+31611223344",
                "email": "",
            },
        ],
        "findings": [
            {
                "title": "ING-rekeningafschriften — onverklaarbare stortingen €48.000",
                "subject": "Kees de Vries",
                "source_type": "document",
                "author": "Lisa van Dijk",
                "created_at": (now - timedelta(days=12)).strftime("%Y-%m-%d %H:%M"),
                "source_url": None,
            },
            {
                "title": "LinkedIn profiel toont fictieve werkgever 'FinCorp International'",
                "subject": "Kees de Vries",
                "source_type": "osint",
                "author": "Piet Jansen",
                "created_at": (now - timedelta(days=11)).strftime("%Y-%m-%d %H:%M"),
                "source_url": "https://linkedin.com/in/keesdv",
            },
            {
                "title": "KvK-uittreksel — Kees Holding B.V. opgericht 2 maanden geleden",
                "subject": "Kees Holding B.V.",
                "source_type": "manual",
                "author": "Lisa van Dijk",
                "created_at": (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
                "source_url": None,
            },
            {
                "title": "Verzekeringsclaim 2026-031 — valse overlijdensakte bijgevoegd",
                "subject": "Kees de Vries",
                "source_type": "document",
                "author": "Piet Jansen",
                "created_at": (now - timedelta(days=9)).strftime("%Y-%m-%d %H:%M"),
                "source_url": None,
            },
            {
                "title": "Telefoonnummer gekoppeld aan 3 verschillende identiteiten bij 2 verzekeraars",
                "subject": "Kees de Vries",
                "source_type": "osint",
                "author": "Piet Jansen",
                "created_at": (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M"),
                "source_url": None,
            },
            {
                "title": "Holding heeft geen enkele bedrijfsactiviteit — lege BV",
                "subject": "Kees Holding B.V.",
                "source_type": "manual",
                "author": "Lisa van Dijk",
                "created_at": (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M"),
                "source_url": None,
            },
            {
                "title": "Mevrouw J. de Vries-Wit staat als uittrekseladres ingeschreven — medeplichtigheid?",
                "subject": "Mevrouw J. de Vries-Wit",
                "source_type": "osint",
                "author": "Piet Jansen",
                "created_at": (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M"),
                "source_url": None,
            },
            {
                "title": "Kees Holding B.V. heeft €120.000 ontvangen van niet-gelieerde rekening",
                "subject": "Kees Holding B.V.",
                "source_type": "document",
                "author": "Lisa van Dijk",
                "created_at": (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M"),
                "source_url": None,
            },
        ],
        "documents": [
            {
                "name": "ING_rekening_2026_01.pdf",
                "type": "bank_afschrift",
                "size": "1.2 MB",
                "uploaded_by": "Lisa van Dijk",
                "created_at": (now - timedelta(days=12)).strftime("%Y-%m-%d"),
                "classification": "vertrouwelijk",
            },
            {
                "name": "claim_2026_031_overlijdensakte.pdf",
                "type": "claim_document",
                "size": "0.8 MB",
                "uploaded_by": "Piet Jansen",
                "created_at": (now - timedelta(days=9)).strftime("%Y-%m-%d"),
                "classification": "geheim",
            },
            {
                "name": "kvk_keesholding.pdf",
                "type": "kvk_uittreksel",
                "size": "0.3 MB",
                "uploaded_by": "Lisa van Dijk",
                "created_at": (now - timedelta(days=10)).strftime("%Y-%m-%d"),
                "classification": "intern",
            },
            {
                "name": "transactie_overzicht_holding.xlsx",
                "type": "financieel",
                "size": "0.5 MB",
                "uploaded_by": "Lisa van Dijk",
                "created_at": (now - timedelta(days=3)).strftime("%Y-%m-%d"),
                "classification": "vertrouwelijk",
            },
            {
                "name": "facebook_profiel_kees.pdf",
                "type": "osint_export",
                "size": "2.1 MB",
                "uploaded_by": "Piet Jansen",
                "created_at": (now - timedelta(days=7)).strftime("%Y-%m-%d"),
                "classification": "intern",
            },
        ],
        "financials": {
            "total_amount": 184_500.00,
            "transaction_count": 23,
            "verified_count": 12,
            "pending_count": 11,
            "latest": [
                {
                    "date": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
                    "description": "Storting Kees Holding B.V.",
                    "amount": 48000.00,
                    "type": "credit",
                    "verified": False,
                },
                {
                    "date": (now - timedelta(days=3)).strftime("%Y-%m-%d"),
                    "description": "Opname contant — geldautomaat Zeist",
                    "amount": 5000.00,
                    "type": "debit",
                    "verified": True,
                },
                {
                    "date": (now - timedelta(days=5)).strftime("%Y-%m-%d"),
                    "description": "Overschrijving naar rekening NL39RABO...",
                    "amount": 12000.00,
                    "type": "debit",
                    "verified": True,
                },
                {
                    "date": (now - timedelta(days=7)).strftime("%Y-%m-%d"),
                    "description": "Storting onbekende bron",
                    "amount": 7500.00,
                    "type": "credit",
                    "verified": False,
                },
                {
                    "date": (now - timedelta(days=10)).strftime("%Y-%m-%d"),
                    "description": "Verzekeringsuitkering claim 2026-031",
                    "amount": 25000.00,
                    "type": "credit",
                    "verified": True,
                },
            ],
        },
        "reminders": [
            {
                "title": "ING-rekening opvragen bij rechtbank",
                "date": (now + timedelta(days=1)).strftime("%Y-%m-%d 09:00"),
                "is_overdue": False,
            },
            {
                "title": "Getuigenverhoor mevrouw J. de Vries-Wit plannen",
                "date": (now + timedelta(days=3)).strftime("%Y-%m-%d 14:00"),
                "is_overdue": False,
            },
            {
                "title": "KvK-uittreksel Kees Holding controleren",
                "date": (now - timedelta(days=2)).strftime("%Y-%m-%d 12:00"),
                "is_overdue": True,
            },
            {
                "title": "Voortgangsrapportage indienen bij opdrachtgever",
                "date": (now + timedelta(days=14)).strftime("%Y-%m-%d 17:00"),
                "is_overdue": False,
            },
        ],
        "comments": [
            {
                "author": "Lisa van Dijk",
                "content": "Heb zojuist de ING-rekeningafschriften ontvangen. Bevestigen ons vermoeden van onverklaarbare stortingen. Ga door met brononderzoek.",
                "created_at": (now - timedelta(days=12)).strftime("%Y-%m-%d %H:%M"),
                "is_pinned": True,
            },
            {
                "author": "Piet Jansen",
                "content": "LinkedIn profiel toont 'FinCorp International' als werkgever — dit bedrijf blijkt niet te bestaan bij KvK. Kees gebruikt dus een fictieve werkgever.",
                "created_at": (now - timedelta(days=11)).strftime("%Y-%m-%d %H:%M"),
                "is_pinned": False,
            },
            {
                "author": "Lisa van Dijk",
                "content": "Overlijdensakte blijkt vals — document is onderzocht door forensisch team. Papier en inkt komen niet overeen met echt overlijdensbewijs. Zaak wordt voorgelegd aan OM.",
                "created_at": (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M"),
                "is_pinned": True,
            },
            {
                "author": "Piet Jansen",
                "content": "Mevrouw J. de Vries-Wit is de echtgenote. Zij staat ingeschreven op hetzelfde adres. Moet worden gehoord over haar betrokkenheid.",
                "created_at": (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M"),
                "is_pinned": False,
            },
        ],
        "child_cases": [
            {
                "case_number": "C-2025-089",
                "title": "Marijkefraude — identiteitsdiefstal",
                "status": "closed",
            },
        ],
    }
