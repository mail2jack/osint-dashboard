"""Pure policy definitions for tenant feature flags."""

FEATURE_FLAG_NAMES = {
    "export": "📤 Export (CSV/PDF)",
    "ai": "🤖 AI Summarization",
    "spiderfoot": "🕷️ SpiderFoot OSINT",
    "api_keys": "🔑 API Key Access",
    "paid_channels": "💰 Paid Channels",
    "subject_first_investigations": "👤 Subject-First Investigations",
    "investigation_workspace": "🔬 Investigation Workspace",
    "investigator_primary_navigation": "🧭 Investigator Primary Navigation",
    "finding_screenshot_capture": "📷 Finding Screenshot Capture",
    "local_browser_screenshot_capture": "🧩 Local Browser Screenshot Capture",
}

FEATURE_FLAG_ORDER = tuple(FEATURE_FLAG_NAMES)

OFF_BY_DEFAULT = frozenset(
    {
        "paid_channels",
        "subject_first_investigations",
        "investigation_workspace",
        "investigator_primary_navigation",
        "finding_screenshot_capture",
        "local_browser_screenshot_capture",
    }
)


def validate_flag_name(flag_name: str) -> None:
    if flag_name not in FEATURE_FLAG_NAMES:
        raise ValueError(f"Unknown flag: {flag_name}")


def tier_default(flag_name: str, tenant_tier: str) -> bool:
    """Return the effective value when no tenant override exists."""
    validate_flag_name(flag_name)
    if flag_name in OFF_BY_DEFAULT:
        return False
    if flag_name == "export":
        return tenant_tier in ("starter", "professional", "enterprise")
    return tenant_tier in ("professional", "enterprise")
