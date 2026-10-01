"""Register all action handlers with the registry."""

from cms.workflow.actions.registry import ACTION_REGISTRY, register_action
from cms.workflow.actions.email_action import _email_check
from cms.workflow.actions.phone_action import _phone_check
from cms.workflow.actions.address_action import _address_check
from cms.workflow.actions.social_action import _social_scan
from cms.workflow.actions.company_action import _kvk_check
from cms.workflow.actions.vehicle_action import _rdw_check, _vessel_check
from cms.workflow.actions.osint_action import (
    _osint_deep_search,
    _google_dork_search,
    _browser_search,
)
from cms.workflow.actions.platform_action import (
    _facebook_check,
    _instagram_check,
    _tiktok_check,
    _linkedin_check,
    _twitter_check,
)
from cms.workflow.actions.other_action import (
    _financial_check,
    _subdomain_check,
    _photo_analysis,
)

register_action(
    "email",
    "Email check",
    "📧",
    _email_check,
    "Searches the email address in public sources (HIBP, PGP keyservers, SpiderFoot) and checks whether it "
    "appears in data breaches, has a PGP key, is linked to social media, "
    "or leaves other online traces (web context).",
    category="open",
)
register_action(
    "phone",
    "Phone check",
    "📞",
    _phone_check,
    "Searches public sources for the phone number: links to social media, business registrations, and any signals from data breaches.",
)
register_action(
    "address",
    "Address research",
    "🏠",
    _address_check,
    "Searches the address in public sources (Kadaster, Overheid.io) to find resident history, property information, and related addresses.",
)
register_action(
    "social",
    "Social media scan",
    "🌐",
    _social_scan,
    "Scans multiple social media platforms based on name or username and collects public profiles, posts, and network connections.",
)
register_action(
    "facebook",
    "Facebook research",
    "📘",
    _facebook_check,
    "Searches for public Facebook profiles, pages, and posts by name. Returns profile photo, bio, and public interactions.",
    cost_label="~€0.25 / lookup",
)
register_action(
    "instagram",
    "Instagram research",
    "📸",
    _instagram_check,
    "Searches Instagram for public profiles and posts. Finds username, profile photo, biography, and recent posts.",
    cost_label="~€0.25 / lookup",
)
register_action(
    "tiktok",
    "TikTok research",
    "🎵",
    _tiktok_check,
    "Searches for public TikTok profiles and content. Returns username, avatar, bio, and video data. (50 credits)",
    cost_label="~€0.25 / lookup",
)
register_action(
    "linkedin",
    "LinkedIn research",
    "💼",
    _linkedin_check,
    "Searches for public LinkedIn profiles by name. Finds work experience, education, location, and network size. (50 credits)",
    cost_label="~€1.00 / lookup",
)
register_action(
    "twitter",
    "Twitter research",
    "🐦",
    _twitter_check,
    "Searches X/Twitter for public profiles and posts. Returns username, bio, followers, and recent tweets. (1000 credits)",
    cost_label="~€1.00 / lookup",
)
register_action(
    "kvk",
    "KvK research",
    "🏢",
    _kvk_check,
    "Consults the Chamber of Commerce (KvK) for company data: legal form, registered address, directors, and annual figures.",
)
register_action(
    "rdw",
    "Vehicle check (RDW)",
    "🚗",
    _rdw_check,
    "Retrieves vehicle information from the RDW database: license plate, make/type, APK history, technical specifications, and registration data.",
)
register_action(
    "vessel",
    "Vessel check",
    "🚢",
    _vessel_check,
    "Searches maritime data sources (VesselFinder, MarinePlan, KVNR, Binnenvaart.eu, Equasis) "
    "by IMO number, MMSI, ENI, or vessel name. Finds position, technical specifications, flag, and year built.",
)
register_action(
    "osint",
    "OSINT Deep Search",
    "🌍",
    _osint_deep_search,
    "Performs in-depth open-source research via Brave Search and SpiderFoot. Searches the entire web for traces of the person or entity.",
)
register_action(
    "financial",
    "Financial research",
    "💰",
    _financial_check,
    "Searches public sources for financial data: business registers, insolvencies, UBO registers, and any negative financial signals.",
)
register_action(
    "subdomain",
    "Subdomain scan",
    "🌐",
    _subdomain_check,
    "Searches Certificate Transparency logs (crt.sh) for all subdomains of a domain. "
    "Finds internal hostnames, development environments, and hidden infrastructure.",
)

register_action(
    "google_dork",
    "Google Dork Search",
    "🔎",
    _google_dork_search,
    "Execute advanced search queries using Google dork syntax. "
    "Pick from 150+ pre-built dorks across categories (personal, email, phone, company, "
    "technical, financial, social, government, vehicle, etc.) or build your own.",
)

register_action(
    "browser_search",
    "Browser search",
    "🔗",
    _browser_search,
    "Composes an open-in-browser search query (Google, Bing, DuckDuckGo) as a proposal "
    "the investigator starts manually. No silent browser automation and no bulk queries — "
    "the composed links are recorded and opened in your own browser.",
)

register_action(
    "manual_entry",
    "Manual entry",
    "📝",
    lambda action: [],
    "Manually created findings by the researcher.",
)

register_action(
    "photo_analysis",
    "Photo Analysis",
    "📷",
    _photo_analysis,
    "Analyze photo EXIF metadata: GPS coordinates, camera info, date/time, "
    "privacy/OPSEC risks, and generate reverse image search links. "
    "Includes AI geolocation fallback when no GPS data is found.",
)

# Channel categories (ADR-0001 PR5/D1.6): paid channels require explicit
# opt-in behind a per-tenant FeatureFlag (paid_channels, off by default) and
# are never proposed by default; local/open actions are the default research
# path and ready as proposals.
_PAID = {"facebook", "instagram", "tiktok", "linkedin", "twitter"}
_LOCAL = {"photo_analysis", "manual_entry"}
for _action_type in ACTION_REGISTRY:
    if _action_type in _PAID:
        ACTION_REGISTRY[_action_type]["category"] = "paid"
    elif _action_type in _LOCAL:
        ACTION_REGISTRY[_action_type]["category"] = "local"
