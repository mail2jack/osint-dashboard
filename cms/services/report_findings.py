"""Helpers for presenting findings in official reports."""

from __future__ import annotations


def deduplicate_report_findings(findings):
    """Return visible findings once per identical evidential payload.

    Re-running a research action can create a second database row for the same
    source and text.  Those rows remain separate for auditability and workflow
    review, but an official report should not print the same evidence twice.
    Keep the first row's chronological position and prefer a duplicate that
    carries analyst comments or more screenshot evidence.
    """
    result = []
    positions = {}
    for finding in findings:
        key = (
            finding.title or "",
            finding.content or "",
            finding.detail or "",
            finding.source_url or "",
            finding.source_type or "",
        )
        existing_position = positions.get(key)
        if existing_position is None:
            positions[key] = len(result)
            result.append(finding)
            continue

        existing = result[existing_position]
        existing_score = (
            bool(existing.comment),
            len(existing.finding_screenshots or []),
        )
        finding_score = (
            bool(finding.comment),
            len(finding.finding_screenshots or []),
        )
        if finding_score > existing_score:
            result[existing_position] = finding
    return result
