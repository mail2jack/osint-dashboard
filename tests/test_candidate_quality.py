from types import SimpleNamespace

from cms.services.candidate_quality import (
    assess_finding,
    canonicalize_url,
    finding_sort_key,
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


def test_subject_signals_raise_score_without_using_email_domain():
    subject = SimpleNamespace(
        name="Patricia van Sandwijk",
        voornamen="Patricia",
        achternaam="van Sandwijk",
        tussenvoegsels="van",
        email="patricia.van.sandwijk@gmail.com",
        phone="+31612345678",
        social_media_ids={"linkedin": {"username": "patricia-van-sandwijk"}},
        workflow_social_accounts=[],
        social_accounts=[],
        contacts=[],
    )
    finding = SimpleNamespace(
        title="Account found on LinkedIn",
        content="The profile patricia-van-sandwijk is active.",
        detail="",
        source_url="https://www.linkedin.com/in/patricia-van-sandwijk/",
        subject=subject,
    )
    result = assess_finding(finding)
    assert result["identity_score"] >= 20
    assert "bekende gebruikersnaam in de bron" in result["identity_matches"]


def test_subject_email_domain_alone_does_not_match():
    subject = SimpleNamespace(
        name="Jan Jansen",
        voornamen="Jan",
        achternaam="Jansen",
        tussenvoegsels="",
        email="jjansen@yahoo.fr",
        phone="",
        social_media_ids={},
        workflow_social_accounts=[],
        social_accounts=[],
        contacts=[],
    )
    finding = SimpleNamespace(
        title="Search result",
        content="Yahoo.fr provides account recovery information.",
        detail="",
        source_url="https://www.google.com/search?q=yahoo.fr",
        subject=subject,
    )
    result = assess_finding(finding)
    assert "exact e-mailadres in de bron" not in result["identity_matches"]
    assert "lokale deel van e-mailadres in de bron" not in result["identity_matches"]


def test_finding_sort_key_supports_quality_relevance_and_source():
    older = SimpleNamespace(
        created_at=SimpleNamespace(isoformat=lambda: "2026-10-01T10:00:00"),
        source_type="web",
        source_url="https://example.com/older",
        candidate_quality={
            "score": 70,
            "identity_score": 10,
            "platform": "Example",
        },
    )
    stronger_identity = SimpleNamespace(
        created_at=SimpleNamespace(isoformat=lambda: "2026-10-01T11:00:00"),
        source_type="social",
        source_url="https://x.com/name",
        candidate_quality={
            "score": 80,
            "identity_score": 40,
            "platform": "X",
        },
    )

    assert finding_sort_key(stronger_identity, "quality") < finding_sort_key(older, "quality")
    assert finding_sort_key(stronger_identity, "relevance") < finding_sort_key(older, "relevance")
    assert finding_sort_key(older, "source") < finding_sort_key(stronger_identity, "source")
