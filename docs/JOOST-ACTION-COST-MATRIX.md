# Joost action cost matrix

Status: inventory plus first metering layer, 4 October 2026

This document separates three things that must not be conflated in pricing:

1. **Local processing**: database work, normalization, deduplication, scoring,
   relationship extraction and report formatting.
2. **External access**: an API, search engine, browser or self-hosted service is
   contacted.
3. **Billable usage**: the provider or Joost infrastructure can incur a
   variable cost.

The current workflow code does not yet charge credits for every external call.
This matrix is therefore the basis for the later metering implementation, not
an assertion that every action already has a working price gate.

## Current action inventory

| Action | Current implementation | External usage | Initial cost class | Initial credits | Consent policy |
|---|---|---|---|---:|---|
| Email check | Email account checks, optional HIBP, PGP and exact Brave context search | Email-check service, HIBP, keys.openpgp.org, Brave | mixed | 1–3 | Free path may run; paid provider calls require approval or tenant budget |
| Phone check | Number parsing/enrichment plus WhatsApp and Telegram checks | WhatsApp/Telegram service or configured provider | mixed | 1–3 | Free/local checks may run; provider lookup requires approval or budget |
| Address research | PDOK/BAG lookup | PDOK location service | open | 1 | Included in standard plan |
| KvK research | Overheid.io with openkvk fallback | Overheid.io or openkvk.nl | mixed | 1–2 | Included if covered by plan; provider call is metered |
| Vehicle check | RDW open-data lookup | RDW open data | open | 1 | Included in standard plan |
| Vessel check | Vessel service with optional commercial sources | Public and optional commercial maritime sources | mixed | 1–3 | Commercial source requires approval or budget |
| Social media scan | Default major-platform username scan; expanded exotic scope | Public HTTP checks, potentially many sites | open / expanded | 1 / 5 | Default scan included; exotic scan requires explicit approval |
| Facebook research | RapidAPI connector | RapidAPI provider | paid | 3 | Explicit approval and paid-channel flag |
| Instagram research | RapidAPI connector | RapidAPI provider | paid | 3 | Explicit approval and paid-channel flag |
| TikTok research | RapidAPI connector | RapidAPI provider | paid | 3 | Explicit approval and paid-channel flag |
| LinkedIn research | RapidAPI connector | RapidAPI provider | paid | 8 | Explicit approval and paid-channel flag |
| X/Twitter research | RapidAPI connector | RapidAPI provider | paid | 8 | Explicit approval and paid-channel flag |
| OSINT deep search | Brave search, with fallback dork links | Brave or fallback search | mixed | 2–5 | Included while free allowance remains; otherwise approval |
| Google Dork search | Brave first, DuckDuckGo fallback | Brave or DuckDuckGo | mixed | 1 per query | Included within normal allowance |
| Browser search | Creates manual Google/Bing/DuckDuckGo links; no automation | Investigator's browser only | local | 0 | No provider charge; normal use policy applies |
| Financial research | Public-source research | Public websites and optional APIs | mixed | 1–3 | Provider calls require approval or budget |
| Subdomain scan | Certificate Transparency services | crt.sh and Cert Spotter | open | 1 | Included in standard plan |
| Photo analysis | EXIF locally; optional Picarta AI geolocation | Optional Picarta API | mixed | 0 / 5 | EXIF is included; AI geolocation requires approval or budget |
| Manual entry | No external calls | None | local | 0 | Included |

## Cost-driving rules

### Included by default

- Local processing and storage within the tenant allowance.
- Public/open-data lookups with predictable and low provider cost.
- Browser-search proposals, because Joost only records links and the
  investigator performs the search manually.
- Default major-platform username scans within a fair-use limit.

### Explicit approval required

- RapidAPI social-platform connectors.
- Exotic/expanded platform searches.
- AI geolocation and any paid AI operation.
- Search runs that exceed the tenant's monthly free allowance.
- Large batches, repeated scans and monitoring jobs.

## Metering design and current implementation

The action registry now carries structured metadata rather than only a display
label:

```python
{
    "cost_class": "open|local|mixed|paid",
    "credit_units": 1,
    "requires_explicit_consent": False,
    "providers": ["brave"],
    "estimated_cost_eur": 0.005,
}
```

The local implementation now records, tenant-scoped and in the same database
transaction where possible:

- research actions started and completed;
- estimated research credit units and paid-action starts;
- Findings Dashboard views;
- customer portal dashboard views;
- portal requests submitted, reviewed, declined and converted.

These events create an immutable `AuditLog` entry as well as a daily counter in
`UsageRecord`. The analytics page exposes the counters without storing the
underlying research value or portal question in usage metrics.

Before production billing or hard limits, Joost should still:

1. calculate the estimated credit usage;
2. show the provider(s), estimated cost and privacy note;
3. check tenant allowance and any explicit approval;
4. reserve usage atomically;
5. release or reconcile the reservation after completion;
6. store the actual provider calls and usage in the audit trail.

## Pricing assumptions used

- Brave currently advertises $5 per 1,000 Search requests and $5 monthly free
  credits. Exact plan terms can change and must be refreshed before launch.
- OpenRouter usage is model/token dependent; the model price and any platform
  fee must be treated as variable.
- HIBP email/breach access is subscription-dependent; the free Pwned Passwords
  API is a different service and must not be treated as free email-breach
  searching.
- RapidAPI social connectors currently expose provider-specific credit limits
  in Joost; the actual provider invoice must be used for final prices.

## Important limitation

The initial credits above are product estimates, not provider invoices. Before
publishing prices, Joost should record a representative sample of real runs:
provider calls, response counts, tokens, elapsed time, storage delta and
whether a fallback was used. The final credit conversion should be based on
those measurements plus a safety margin.
