from cms.services.customer_registration_mail import analyze_raw_email


def test_mail_analysis_reports_sender_authentication_and_completeness():
    raw = b"""From: Aanvrager <person@example.com>\nTo: intake@iveras.com\nDate: Mon, 27 Oct 2026 10:00:00 +0000\nSubject: Onderzoeksvraag\nMessage-ID: <request@example.com>\nAuthentication-Results: mx.example; spf=pass dkim=pass dmarc=pass\nReceived: from mail.example (mail.example [203.0.113.7])\n\nGeachte heer, graag ontvang ik informatie.\n"""

    report = analyze_raw_email(raw)

    assert report["sender"]["addresses"][0]["address"] == "person@example.com"
    assert report["authentication"]["spf"] == "pass"
    assert report["authentication"]["dkim"] == "pass"
    assert report["authentication"]["dmarc"] == "pass"
    assert all(report["completeness"].values())
    assert report["message_sha256"]


def test_mail_analysis_flags_reply_to_mismatch_and_missing_headers():
    raw = b"""From: Aanvrager <person@example.com>\nReply-To: andere@unrelated.example\nSubject: Vraag\n\nKunt u mij helpen?\n"""

    report = analyze_raw_email(raw)

    assert report["completeness"]["date"] is False
    assert report["completeness"]["received_chain"] is False
    assert any("Reply-To" in warning for warning in report["warnings"])
