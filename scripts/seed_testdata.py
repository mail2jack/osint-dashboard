"""Seed production database with 100 realistic Dutch fictitious cases.

Each case has 2-5 subjects with full profiles: identifiers, facts,
addresses, contacts, social accounts, research actions, and findings.

Usage (on VPS):
    cd /opt/osint-dashboard
    sudo -u osint ./venv/bin/python scripts/seed_testdata.py
"""

import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "/opt/osint-dashboard")

from app import app  # noqa: E402
from cms.models import (  # noqa: E402
    Address,
    Case,
    Client,
    Contact,
    FeatureFlag,
    Finding,
    ResearchAction,
    SocialAccount,
    Subject,
    SubjectFact,
    SubjectIdentifier,
    db,
)
from cms.encryption_utils import encryptor  # noqa: E402

TENANT_ID = "3a169c92-04a2-48f9-be1b-1fcf930c0f0f"
ADMIN_USER_ID = None

# ── Dutch fictitious data pools ──────────────────────────────────────────────

COMPANY_NAMES = [
    "De Gouden Leeuw BV", "Noordelijke Shipping NV", "Randstad Logistiek BV",
    "Zuidwest Transport Groep", "Hollandse Energie Coöperatie",
    "Rotterdamse Haven Services", "Brabantse Machinebouw BV",
    "Twentse Textiel Handel", "Friesland Zuivel Export",
    "Limburgse Bouwmaterialen NV", "Drentse Agrarische Diensten",
    "Overijssel ICT Solutions", "Gelderland Groep BV",
    "Utrechtse Vastgoed Maatschappij", "Noord-Holland Security BV",
    "Zuid-Holland Maritiem Services", "Zeeland Offshore BV",
    "Flevoland Smart Solutions", "Groninger Energie Unie",
    "Amsterdam Financieel Advies", "Den Haag Juridisch Centrum",
    "Eindhoven Technologie Park", "Tilburg Commercieel BV",
    "Nijmegen Gezondheidszorg", "Maastricht Internationaal",
]

FIRST_MALE = [
    "Jan", "Piet", "Kees", "Willem", "Henk", "Marco", "Peter", "David",
    "Thomas", "Kevin", "Bas", "Rick", "Sven", "Daan", "Finn", "Luuk",
    "Bram", "Sem", "Mats", "Niels", "Jasper", "Ruben", "Tim", "Gijs",
    "Stijn", "Floris", "Max", "Tijn", "Levi", "Lucas", "Jayden", "Adam",
    "Benjamin", "James", "Alexander", "Joep", "Tygo", "Boaz", "Olivier",
    "Dex", "Julian", "Hugo", "Cas", "Silas", "Noud", "James", "Sam",
]

FIRST_FEMALE = [
    "Anna", "Emma", "Sophie", "Julia", "Mila", "Lisa", "Eva", "Noor",
    "Lotte", "Fenna", "Yara", "Evi", "Sara", "Nova", "Maud", "Lina",
    "Saar", "Fien", "Lieve", "Roos", "Jasmijn", "Isa", "Tess", "Yente",
    "Lara", "Nora", "Olivia", "Amber", "Quinty", "Britt", "Sanne",
    "Iris", "Chiara", "Fleur", "Marloes", "Anouk", "Lianne", "Nicole",
    "Laura", "Claire", "Floor", "Isabelle", "Demi", "Nina", "Romée",
]

ACHTERNAMEN = [
    "Bakker", "Jansen", "de Vries", "van Dijk", "Bos", "Visser",
    "Smit", "Mulder", "Brouwer", "Groot", "Hendriks", "Willems",
    "de Boer", "Dijkstra", "van Leeuwen", "Meijer", "van den Berg",
    "Peters", "van Veen", "Kok", "van Dam", "Hermans", "de Groot",
    "Schouten", "Jansen", "Mol", "Blaauw", "Kuiper", "Postma",
    "Vos", "Wijkstra", "Dekker", "Bosman", "Prijs", "Stoker",
]

TUSSENVOEGSELS = ["de", "van", "van der", "van den", "van de", "van het",
                  "den", "der", "het", "ten", "ter", "te", "op", "aan", ""]

STREETS = [
    "Hoofdstraat", "Dorpsweg", "Kerklaan", "Molenweg", "Stationsplein",
    "Willem-Alexanderstraat", "Prinsesseweg", "Oranjelaan",
    "Beatrixstraat", "Amsterdamseweg", "Rotterdamseweg", "Utrechtseweg",
    "Haagseweg", "Bredaseweg", "Eindhovenseweg", "Maastrichterlaan",
    "Groningerweg", "Zwolseweg", "Apeldoornseweg", "Arnhemseweg",
    "Nijmeegseweg", "Delftseweg", "Leidseweg", "Haarlemmerweg",
    "Bergenseweg", "Noordwijkseweg", "Kerkweg", "Raadhuisstraat",
    "Brink", "Dorpsstraat", "Heereweg", "Langestraat", "Schoolstraat",
]

CITIES = [
    "Amsterdam", "Rotterdam", "Den Haag", "Utrecht", "Eindhoven",
    "Groningen", "Tilburg", "Almere", "Breda", "Nijmegen",
    "Enschede", "Haarlem", "Arnhem", "Zaanstad", "Amersfoort",
    "Apeldoorn", "'s-Hertogenbosch", "Hoofddorp", "Maastricht", "Leiden",
    "Dordrecht", "Zoetermeer", "Zwolle", "Deventer", "Delft",
]


def _pc():
    return f"{random.randint(1000, 9999)}{chr(random.randint(65, 83))}{chr(random.randint(65, 83))}"


def _enc(text):
    if not text:
        return text
    return encryptor.encrypt(text)


def _uuid():
    return str(uuid.uuid4())


def _date(start_y=2020, end_y=2026):
    s = datetime(start_y, 1, 1, tzinfo=UTC)
    e = datetime(end_y, 12, 31, tzinfo=UTC)
    return s + timedelta(days=random.randint(0, (e - s).days))


def _phone():
    return f"06{random.randint(10000000, 99999999)}"


def _email(first, last):
    d = random.choice(["gmail.com", "outlook.com", "hotmail.com",
                       "protonmail.com", "ziggo.nl", "kpn.nl"])
    return f"{first.lower()}.{last.lower().replace(' ', '')}@{d}"


def _iban():
    return f"NL{random.randint(10,99)}RABO{random.randint(100000000,999999999)}{random.randint(1000,9999)}"


def _amount():
    return f"{random.randint(50, 50000):,.2f}".replace(",", ".")


def _person_name():
    gender = random.choice(["man", "vrouw"])
    first = random.choice(FIRST_MALE if gender == "man" else FIRST_FEMALE)
    tv = random.choice(TUSSENVOEGSELS)
    last = random.choice(ACHTERNAMEN)
    full = f"{first} {tv} {last}".replace("  ", " ").strip()
    return gender, first, tv, last, full


def _company_name():
    return random.choice(COMPANY_NAMES)


# ── Seed functions ───────────────────────────────────────────────────────────

def seed_clients():
    clients = []
    for name in COMPANY_NAMES[:15]:
        gender, first, tv, last, full = _person_name()
        c = Client(
            id=_uuid(), tenant_id=TENANT_ID,
            name=name, is_company=True,
            contact_person=_enc(f"{first} {tv} {last}".replace("  ", " ").strip()),
            contact_email=_enc(_email("contact", name.split()[0])),
            contact_phone=_enc(_phone()),
            address_street=_enc(random.choice(STREETS)),
            address_number=_enc(str(random.randint(1, 200))),
            address_city=_enc(random.choice(CITIES)),
            address_postal=_enc(_pc()),
            address_country=_enc("Nederland"),
            contract_number=f"CTR-{random.randint(2023,2026)}-{random.randint(100,999)}",
            is_active=True, created_by=ADMIN_USER_ID,
        )
        db.session.add(c)
        clients.append(c)
    db.session.flush()
    print(f"  + {len(clients)} clients")
    return clients


def _seed_person_subject(case_id):
    gender, first, tv, last, full = _person_name()

    subject = Subject(
        id=_uuid(), tenant_id=TENANT_ID, name=full,
        subject_type="person",  # name is NOT encrypted, plain text
        achternaam=last, voornamen=first, tussenvoegsels=tv if tv else None,
        geslacht=gender,
        date_of_birth=_enc(_date(1955, 2005).strftime("%Y-%m-%d")),
        place_of_birth=_enc(random.choice(CITIES)),
        nationality=_enc("Nederlands"),
        phone=_enc(_phone()), email=_enc(_email(first, last)),
        street=_enc(random.choice(STREETS)),
        house_number=_enc(str(random.randint(1, 300))),
        postal_code=_enc(_pc()), city=_enc(random.choice(CITIES)),
        created_by=ADMIN_USER_ID,
    )
    db.session.add(subject)

    # Identifiers (use set_value for fingerprint generation)
    for id_type, val in [
        ("email", _email(first, last)),
        ("phone", _phone()),
        ("bsn", f"{random.randint(100000000, 999999999)}"),
    ]:
        si = SubjectIdentifier(
            id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
            identifier_type=id_type, status="verified", created_by=ADMIN_USER_ID,
        )
        si.set_value(val)
        db.session.add(si)

    if random.random() > 0.5:
        si = SubjectIdentifier(
            id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
            identifier_type="iban", status="candidate", created_by=ADMIN_USER_ID,
        )
        si.set_value(_iban())
        db.session.add(si)

    # Address
    addr = Address(
        id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
        street=_enc(random.choice(STREETS)),
        number=_enc(str(random.randint(1, 300))),
        zipcode=_enc(_pc()), town=_enc(random.choice(CITIES)),
        country=_enc("Nederland"), is_primary=True,
        source="seed_data", status="verified",
    )
    db.session.add(addr)

    # Contacts
    db.session.add(Contact(
        id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
        contact_type="email", value=_enc(_email(first, last)),
        is_primary=True, source="seed_data", status="verified",
    ))
    db.session.add(Contact(
        id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
        contact_type="phone", value=_enc(_phone()),
        is_primary=False, source="seed_data", status="verified",
    ))

    # Social accounts
    if random.random() > 0.3:
        platform = random.choice(["linkedin", "twitter", "facebook",
                                  "instagram", "tiktok"])
        db.session.add(SocialAccount(
            id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
            platform=platform,
            username=f"{first.lower()}.{last.lower()}_{random.randint(10,99)}",
            url=f"https://{platform}.com/{first.lower()}{random.randint(10,99)}",
            source="seed_data", status="candidate",
        ))

    # Facts (use set_value for proper encryption)
    for _ in range(random.randint(2, 5)):
        sf = SubjectFact(
            id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
            fact_key=random.choice([
                "geboortedatum", "nationaliteit", "beroep", "werkgever",
                "inkomen_schatting", "burgerlijke_staats", "opleiding",
                "strafblad", "faillissement", "vastgoedbezit",
            ]),
            status="verified", source="seed_data",
            created_by=ADMIN_USER_ID,
        )
        sf.set_value(random.choice([
            "Bevestigd", "Niet bevestigd", "Onbekend",
            f"Waarde: {random.randint(1, 100)}",
            random.choice(CITIES), f"EUR {_amount()}",
        ]))
        db.session.add(sf)

    return subject


def _seed_company_subject(case_id):
    name = _company_name()
    kvk = str(random.randint(10000000, 99999999))

    subject = Subject(
        id=_uuid(), tenant_id=TENANT_ID, name=name,
        subject_type="company", registration_number=kvk,
        created_by=ADMIN_USER_ID,
    )
    db.session.add(subject)

    si = SubjectIdentifier(
        id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
        identifier_type="kvk_number", status="verified", created_by=ADMIN_USER_ID,
    )
    si.set_value(kvk)
    db.session.add(si)

    addr = Address(
        id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
        street=_enc(random.choice(STREETS)),
        number=_enc(str(random.randint(1, 300))),
        zipcode=_enc(_pc()), town=_enc(random.choice(CITIES)),
        country=_enc("Nederland"), is_primary=True,
        source="seed_data", status="verified",
    )
    db.session.add(addr)

    db.session.add(Contact(
        id=_uuid(), subject_id=subject.id, tenant_id=TENANT_ID,
        contact_type="email",
        value=_enc(f"info@{name.lower().replace(' ', '').replace('.', '')}.nl"),
        is_primary=True, source="seed_data", status="verified",
    ))

    return subject


def seed_all():
    global ADMIN_USER_ID
    with app.app_context():
        # Bypass Row-Level Security for bulk seed inserts
        db.session.execute(db.text("SET app.bypass_rls = 'true'"))

        from cms.models import User
        admin = User.query.filter_by(role="admin").first()
        ADMIN_USER_ID = admin.id
        print(f"Admin: {admin.username} ({admin.id})")
        print(f"Tenant: {TENANT_ID}\n")

        # Feature flag
        flag = FeatureFlag.query.filter_by(
            tenant_id=TENANT_ID, flag_name="subject_first_investigations",
        ).first()
        if not flag:
            db.session.add(FeatureFlag(
                tenant_id=TENANT_ID,
                flag_name="subject_first_investigations", enabled=True,
            ))
        elif not flag.enabled:
            flag.enabled = True

        # Clients
        print("Seeding clients...")
        clients = seed_clients()

        # Cases + Subjects
        print("Seeding cases + subjects...")
        cases_data = []
        all_subjects = []
        for idx in range(1, 101):
            client = random.choice(clients)
            status = random.choice(["open", "in_progress", "closed", "pending"])
            priority = random.choice(["low", "medium", "high", "urgent"])
            start = _date(2023, 2026)
            end = _date(2026, 2026) if status == "closed" else None

            reason = random.choice([
                "vermoeden van fraude", "identiteitsfraude", "oplichting",
                "bedrijfsspionage", "contractbreuk", "verzuimonderzoek",
                "achtergrondcheck", "due diligence", "incassodossier",
            ])
            context = random.choice(["privé", "zakelijk", "familie", "arbeid"])

            case = Case(
                id=_uuid(), tenant_id=TENANT_ID,
                case_number=f"{random.choice([2023,2024,2025,2026])}-{idx:05d}",
                client_id=client.id,
                title=f"Onderzoek nr. {idx:05d} — {reason}",
                description=f"Onderzoek ingesteld naar aanleiding van een melding. "
                            f"Aard: {reason}. Context: {context}.",
                priority=priority, status=status,
                case_type=random.choice(["civil", "criminal", "family",
                                         "corporate", "fraud"]),
                jurisdiction=random.choice([
                    "Noord-Holland", "Zuid-Holland", "Utrecht",
                    "Noord-Brabant", "Gelderland",
                ]),
                start_date=start.date(),
                target_end_date=end.date() if end else None,
                actual_end_date=end.date() if status == "closed" else None,
                closure_reason="Onderzoek afgerond" if status == "closed" else None,
                created_by=ADMIN_USER_ID,
            )
            db.session.add(case)
            db.session.flush()

            # 2-5 subjects
            subjects = []
            for _ in range(random.randint(2, 5)):
                if random.random() > 0.15:
                    s = _seed_person_subject(case.id)
                else:
                    s = _seed_company_subject(case.id)
                db.session.flush()
                subjects.append(s)
                all_subjects.append(s)

                db.session.execute(
                    db.text(
                        "INSERT INTO case_subjects (case_id, subject_id) "
                        "VALUES (:c, :s) ON CONFLICT DO NOTHING"
                    ),
                    {"c": case.id, "s": s.id},
                )

            # Update title with first subject name
            first_name = subjects[0].name if subjects else "Subject"
            case.title = f"Onderzoek nr. {idx:05d} — {first_name} — {reason}"

            cases_data.append((case, subjects))

            if idx % 25 == 0:
                db.session.flush()
                print(f"  ... {idx}/100 cases")

        db.session.flush()
        print(f"  + {len(cases_data)} cases")
        print(f"  + {len(all_subjects)} subjects")

        # Research actions
        print("Seeding research actions...")
        actions = []
        for case, subjects in cases_data:
            for _ in range(random.randint(1, 4)):
                subject = random.choice(subjects)
                atype = random.choice([
                    "email", "phone", "social_media", "osint",
                    "document", "surveillance", "interview",
                    "database_check", "financial",
                ])
                label_map = {
                    "email": "Email onderzoek", "phone": "Telefoon trace",
                    "social_media": "Social media analyse",
                    "osint": "OSINT scan", "document": "Document verificatie",
                    "surveillance": "Observatie", "interview": "Interview",
                    "database_check": "Database check",
                    "financial": "Financieel onderzoek",
                }
                status = random.choice([
                    "completed", "pending", "in_progress", "cancelled",
                ])
                started = _date(2023, 2026)
                ra = ResearchAction(
                    id=_uuid(), case_id=case.id, subject_id=subject.id,
                    tenant_id=TENANT_ID, action_type=atype,
                    label=label_map.get(atype, atype),
                    status=status, target_kind="subject",
                    created_by=ADMIN_USER_ID,
                    created_at=started,
                    started_at=started if status != "pending" else None,
                    completed_at=started + timedelta(days=random.randint(1, 14))
                    if status == "completed" else None,
                )
                db.session.add(ra)
                actions.append(ra)

        db.session.flush()
        print(f"  + {len(actions)} research actions")

        # Findings
        print("Seeding findings...")
        findings = []
        finding_templates = [
            "Emailadres gekoppeld aan social media account.",
            "Telefoonnummer geassocieerd met meerdere profielen.",
            "Adres bevestigd via gemeentelijke basisregistratie.",
            "KVK-nummer gelinkt aan onderzoekspersoon.",
            "Social media activiteit gedetecteerd.",
            "Financiële transactie gedetecteerd.",
            "Achtergrondcheck: geen strafblad gevonden.",
            "Verbondenheid met bedrijf bevestigd via openbare registers.",
            "Adreswijziging gedetecteerd.",
            "Nieuw telefoonnummer gedetecteerd.",
            "Profielfoto gewijzigd op social media.",
            "Verhuizing bevestigd via Volksregister.",
            "Zakelijke relatie aangetroffen in Handelsregister.",
            "Online profiel gevonden met gebruikersnaam.",
        ]
        for case, subjects in cases_data:
            for _ in range(random.randint(1, 5)):
                subject = random.choice(subjects)
                status = random.choice(["candidate", "verified", "rejected"])
                content = random.choice(finding_templates)

                f = Finding(
                    id=_uuid(), case_id=case.id, subject_id=subject.id,
                    tenant_id=TENANT_ID,
                    title=f"Bevinding: {random.choice(['Email','Telefoon','Adres','Social','Financieel','Identiteit'])}",
                    content=content,
                    source_type=random.choice(["osint", "manual", "database", "interview"]),
                    status=status,
                    verified=(status == "verified"),
                    verified_by=ADMIN_USER_ID if status == "verified" else None,
                    verified_at=_date(2024, 2026) if status == "verified" else None,
                    created_by=ADMIN_USER_ID,
                    created_at=_date(2023, 2026),
                )
                db.session.add(f)
                findings.append(f)

        db.session.flush()
        print(f"  + {len(findings)} findings")

        db.session.commit()
        print("\n=== DONE ===")
        print(f"  Clients:          {len(clients)}")
        print(f"  Cases:            {len(cases_data)}")
        print(f"  Subjects:         {len(all_subjects)}")
        print(f"  Research actions: {len(actions)}")
        print(f"  Findings:         {len(findings)}")


if __name__ == "__main__":
    seed_all()
