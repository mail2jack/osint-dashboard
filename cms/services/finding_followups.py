"""Deterministic extraction of safe follow-up candidates from findings."""

import re
from urllib.parse import urlparse


_EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?![\w.-])")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s()./-]{6,}\d)(?!\d)")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_ACCOUNT_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "tiktok.com": "tiktok",
    "youtube.com": "browser_search",
    "reddit.com": "browser_search",
}


def _host_platform(url):
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    for domain, action_type in _ACCOUNT_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return action_type
    return None


def _clean_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if value.strip().startswith("+"):
        normalized = "+" + digits
    else:
        normalized = digits
    return normalized if 8 <= len(digits) <= 15 else None


def extract_finding_followups(findings, subjects):
    """Extract source-bound emails, phones, account URLs and relations.

    This function does not call external services and does not infer a fact.
    It only returns values literally present in the supplied findings.
    """
    emails = {}
    phones = {}
    accounts = {}
    relations = {}
    subject_pairs = [
        (left, right)
        for index, left in enumerate(subjects)
        for right in subjects[index + 1 :]
        if left.name and right.name
    ]

    for finding in findings:
        text = "\n".join(
            str(value or "")
            for value in (finding.title, finding.content, finding.detail, finding.source_url)
        )
        for value in _EMAIL_RE.findall(text):
            emails.setdefault(value.casefold(), {"value": value, "finding_ids": []})
            emails[value.casefold()]["finding_ids"].append(finding.id)
        for raw in _PHONE_RE.findall(text):
            value = _clean_phone(raw)
            if not value:
                continue
            phones.setdefault(value, {"value": value, "finding_ids": []})
            phones[value]["finding_ids"].append(finding.id)
        for raw_url in _URL_RE.findall(text):
            url = raw_url.rstrip(".,);]}")
            action_type = _host_platform(url)
            if not action_type:
                continue
            key = url.casefold()
            accounts.setdefault(
                key,
                {"url": url, "platform": action_type, "finding_ids": []},
            )["finding_ids"].append(finding.id)
        lowered = text.casefold()
        for left, right in subject_pairs:
            if left.name.casefold() in lowered and right.name.casefold() in lowered:
                key = (left.id, right.id)
                relations.setdefault(
                    key,
                    {
                        "subject_ids": [left.id, right.id],
                        "subject_names": [left.name, right.name],
                        "finding_ids": [],
                    },
                )["finding_ids"].append(finding.id)

    return {
        "emails": list(emails.values()),
        "phones": list(phones.values()),
        "accounts": list(accounts.values()),
        "relations": list(relations.values()),
    }
