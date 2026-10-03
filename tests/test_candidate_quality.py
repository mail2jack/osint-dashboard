from types import SimpleNamespace

from cms.services.candidate_quality import (
    assess_finding,
    canonicalize_url,
    platform_for_url,
)


def test_canonicalize_url_removes_tracking_and_normalizes_host():
    assert canonicalize_url(
        "HTTPS://WWW.X.COM/nesss34/?utm_source=test&ref=search"
    ) == "https://x.com/nesss34"


def test_platform_detection_requires_exact_host():
    assert platform_for_url("https://x.com/nesss34") == "x"
    assert platform_for_url("https://itemfix.com/x.com/nesss34") is None


def test_specific_confirmed_profile_scores_above_generic_false_positive():
    confirmed = assess_finding(
        SimpleNamespace(
            title="Account found on YouTube",
            content="Status: confirmed",
            detail="",
            source_url="https://www.youtube.com/@patricia.van.sandwijk",
        )
    )
    false_positive = assess_finding(
        SimpleNamespace(
            title="8 sites respond but account not confirmed",
            content="The page contained no confirmation of the account. These are likely false positives.",
            detail="",
            source_url="https://example.com/search?q=patricia",
        )
    )
    assert confirmed["confidence"] == "high"
    assert confirmed["score"] > false_positive["score"]
    assert false_positive["warnings"]
