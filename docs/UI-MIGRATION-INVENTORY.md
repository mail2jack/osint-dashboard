# UI-migratie: scherminventaris en uitvoeringsplan

Status: fase 1 — inventarisatie en harmonisatieplan

Dit document is de vaste checklist voor de omslag naar de nieuwe Iveras-stijl. Een scherm wordt pas als afgerond gemarkeerd wanneer de route, autorisatie, lege/fout/laadstatus, responsive weergave, auditimpact en relevante tests zijn gecontroleerd.

## Ontwerpprincipes

- Eén herkenbare Iveras-basis: contextbalk, paginatitel, status, primaire actie, breadcrumbs en consistente kaarten.
- Eén schermtaal per doelgroep: CMS, klantportal, publieke website, authenticatie en foutpagina's delen tokens, maar niet automatisch dezelfde navigatie.
- Intern en klantzichtbaar blijven expliciet gescheiden.
- Relaties zijn klikbaar: zaak ↔ onderzoek ↔ subject ↔ klant ↔ actie ↔ finding ↔ rapport.
- Statussen, prioriteiten, validatie en lege staten gebruiken vaste componenten.
- Destructieve acties krijgen bevestiging, auditregistratie en een herstelpad waar dat kan.
- Legacy-routes blijven alleen bestaan zolang ze bewust doorverwijzen of aantoonbaar nodig zijn.

## Schermcatalogus

### A. CMS-werkruimte — hoogste prioriteit

| Onderdeel | Routes/templates | Nieuwe stijl | Extra controle |
|---|---|---|---|
| Dashboard en werkqueue | `/cms/dashboard`, `cms/dashboard.html` | Werkgericht dashboard uit de dashboard-demo | rolgebaseerde kaarten, lege queue, klikroutes |
| Zaken | `/cms/workflow/*`, `cms/workflow/workflow_dashboard.html`, `workflow_case_detail.html`, `workflow_case_edit.html`, `workflow_case_new.html` | contextbalk, status/priority chips, relationele secties | tenantfilter, overgangsaudit, legacy-link |
| Onderzoeken | `workflow_case_investigations.html`, `workflow_investigation_detail.html`, `workflow_investigation_edit.html` | actie- en voortgangsoverzicht | behandelaar, bronnen, audit en foutstatus |
| Onderzoeksacties | `_investigations_section.html`, workflow detail | actiekaart met uitvoerder, bron, resultaat en status | intern blijven, bronverwijzing, tijdlijn |
| Bevindingen | `findings_dashboard.html`, `workflow_findings.html`, `_finding_item*.html` | verificatie-, bron- en vrijgavecomponenten | interne finding nooit per ongeluk vrijgeven |
| Rapporten en vrijgave | `workflow_case_report_hub.html`, `workflow_pv.html`, `workflow_pv_edit.html`, `cases/report.html`, `cases/report_pdf.html` | rapportmodule uit de UI-review-demo | release-gates, klantpreview, downloadrechten |
| Berichten | `workflow_case_messages.html`, portal message templates | chat/tijdlijn met quote en unread | mailnotificatie, audit, portal-isolatie |
| Documenten | portal/CMS documentweergaven en downloadroutes | documentkaart met zichtbaarheid/status | MIME, downloadrechten, audit |
| Findings/portal dashboard | findings- en portal-dashboard routes | vaste metrics, filters, trenduitleg | usage meting, tenantafscherming |

### B. Relaties en kerngegevens

| Onderdeel | Routes/templates | Nieuwe stijl | Extra controle |
|---|---|---|---|
| Subjecten/personen | `/cms/subjects/*`, `subjects/create.html`, `edit.html`, `view.html`, `profile.html` | uniforme persoonskaart uit subject-demo | meervoudige adressen/contacten, primair, foto, Keno, audit |
| Klanten | `/cms/clients/*`, `clients/create.html`, `edit.html`, `view.html` | client-demo naar productiepatroon | particulier/organisatie, portal access, tenantrelatie |
| Portal-aanvragen | `/cms/portal-requests`, `portal_requests.html` | triagekaart met drie scenario's | eenmalig onderzoek, tenant, andere vraag |
| Klantregistraties | `customer_registrations.html` | registratiekaart met spam/archiefpad | geen antwoord bij spam, soft-delete, audit |
| Gebruikers | `users/*.html` | profiel-, rol- en statuskaarten | tenantrol, 2FA, portal-only gebruiker |
| Herinneringen | `reminders/*.html` | tijdlijn en actiekaart | eigenaar, snooze, audit |

### C. Instellingen, beveiliging en platform

| Onderdeel | Routes/templates | Nieuwe stijl | Extra controle |
|---|---|---|---|
| Instellingen | `settings/index.html` en `settings/*.html` | instellingen-demo met groepen en zoekfunctie | tenant vs platform, unsaved changes |
| OPSEC/Tor | `opsec_dashboard.html` | duidelijke veiligheidsstatus en checks | geen gevoelige waarden in UI/log |
| Audit en loginhistorie | `audit/log.html`, `settings/read_audit.html`, `settings/login_history.html` | filterbare audit-tijdlijn | actor, timestamp, entity, wijziging, tenant |
| Sessies en API keys | `sessions.html`, `settings/api_keys.html` | beveiligingskaarten | intrekken, rotatie, laatste gebruik |
| DPA/privacy/datalekken | `settings/dpa.html`, `privacy.html`, `settings/breaches.html`, `avg_request.html` | compliance-overzicht | bewaartermijn, rechten, audit |
| Analytics | `analytics.html`, `analytics_super.html`, `settings/statistics.html` | dashboard-demo met duidelijke definities | tenantaggregatie, geen cross-tenant data |
| Super Admin | tenant-, platform-, feature-, rate-limit- en announcementschermen | aparte beheercontext | sterkere waarschuwingen en bevoegdheden |
| Integraties | SpiderFoot, Brave, search, Telegram, AI en API-documentatie | status/actiekaarten | secrets maskeren, testactie auditen |
| Facturatie | `invoicing/*.html` | financiële kaarten en duidelijke status | verzenden, betalen, annuleren, PDF, audit |

### D. Klantportal en publieke website

| Onderdeel | Templates/routes | Nieuwe stijl | Extra controle |
|---|---|---|---|
| Publieke website | `public/homepage.html`, `contact.html`, `legal.html` | publieke variant van design tokens | SEO, juridische teksten, formulieren |
| Registratie en aanvraag | `portal_register.html`, `portal_request.html`, `portal_request_message.html` | duidelijke scenario-keuze | spam, validatie, geen automatische tenant zonder goedkeuring |
| Portal dashboard | `portal_dashboard.html`, `portal.html` | klantgerichte opdrachten/meldingen | alleen eigen organisatie/relatie |
| Portal profiel | `portal_profile.html` | complete klantenkaart na eerste login | verplichte velden, 2FA, uitnodiging/reset |
| Portal zaak/rapport | `portal_report.html`, `portal_documents.html`, `portal_case_messages.html` | vrijgegeven informatie centraal | interne findings/notes altijd uitgesloten |
| Portal factuur | `portal_invoice.html` | financiële statuskaart | alleen eigen facturen |
| Niet beschikbaar/fouten | `portal_unavailable.html`, CMS 400/403/404/405/429/500 | consistente herstelactie | geen stack traces of gegevenslek |

### E. Authenticatie en onboarding

| Onderdeel | Templates | Nieuwe stijl | Extra controle |
|---|---|---|---|
| Login en wachtwoord | `login.html`, `forgot_password.html`, `set_password.html`, `accept_invite.html` | compacte beveiligde flow | juiste redirect, geen admin-autologin |
| 2FA | `2fa/*.html` | stapindicator en herstelpad | localhost-uitzondering blijft lokaal |
| Setup wizard | `setup_wizard.html`, `signup.html` | begeleide eerste inrichting | skip-route naar dashboard, tenantkeuze |

## Verbeteringen die tijdens de uitvoering worden meegenomen

1. Gedeelde design tokens en componenten voor kaarten, badges, formulieren, tabbladen, alerts, breadcrumbs en tijdlijnen.
2. Een vaste contextbalk met titel, relatie, status, primaire actie en terug-link.
3. Eén terminologielijst voor Nederlands/Engels, inclusief zaak, onderzoek, finding, rapport, klant en subject.
4. Eén validatiepatroon: inline bij verlaten van het veld, blokkeren van vervolgstappen bij fouten en samenvatting vóór opslaan.
5. Eén patroon voor loading, empty, error en success states.
6. Eén patroon voor primair kiezen bij meerdere adressen/contactgegevens.
7. Eén patroon voor zichtbaarheid: intern, klantzichtbaar, vrijgegeven en gearchiveerd.
8. Consistente auditinformatie bij aanmaken, wijzigen, vrijgeven, archiveren, spam en verwijderen.
9. Waarschuwing voor niet-opgeslagen wijzigingen en veilige navigatie terug naar de bronzaak.
10. Toegankelijkheid: labels, focus, toetsenbordbediening, contrast en foutmeldingen.
11. Responsive gedrag voor formulieren, tabellen, rapporten en dashboardkaarten.
12. Routecontrole: links openen de actuele route; oude routes krijgen een bewuste redirect of worden verwijderd na gebruiksanalyse.

## Uitvoeringsvolgorde

- [x] Fase 1 — bestaande demo's en scherminventaris vastgelegd.
- [ ] Fase 2 — gedeelde CMS-componenten en design tokens extraheren.
- [ ] Fase 3 — dashboard, zaken, onderzoeken, acties, findings en rapportmodule.
- [ ] Fase 4 — subjecten, klanten, aanvragen, gebruikers en herinneringen.
- [ ] Fase 5 — instellingen, OPSEC, audit, analytics, facturatie en Super Admin.
- [ ] Fase 6 — portal, publieke website, onboarding, authenticatie en foutpagina's.
- [ ] Fase 7 — route-, rechten-, audit-, responsive- en regressietest.
- [ ] Fase 8 — lokale review, commit/CI en pas daarna expliciete productie-release.

## Definition of done

Een fase is klaar wanneer alle schermen in die fase dezelfde componenttaal gebruiken, geen bekende oude route meer onbedoeld opent, autorisatie en tenantafscherming zijn gecontroleerd, audit-events aanwezig zijn waar nodig, en de relevante tests groen zijn. Productie wordt pas aangepast nadat de lokale fase expliciet is beoordeeld.
