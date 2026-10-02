"""Helpers for presenting findings in official reports."""

from __future__ import annotations

import re


_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


def finding_payload_key(finding, *, include_subject: bool = False):
    """Return a stable identity for one evidential finding payload."""
    key = (
        finding.title or "",
        finding.content or "",
        finding.detail or "",
        finding.source_url or "",
        finding.source_type or "",
    )
    if include_subject:
        return (getattr(finding, "subject_id", None),) + key
    return key


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
        key = finding_payload_key(finding)
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


def normalize_report_markdown(markdown_text: str | None) -> str:
    """Repair compact pipe tables emitted as one physical Markdown line.

    Some generated reports contain a complete table (header, separator and
    rows) on one line.  Markdown table parsers need one physical line per row,
    so split only the unambiguous table shape and leave all other text intact.
    """
    if not markdown_text:
        return markdown_text or ""

    normalized = []
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            normalized.append(line)
            continue

        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        # A compact table commonly has an empty cell between each concatenated
        # row (``... | |---| ... | |Row...``).  Those separators are not data
        # cells and can be removed once a table separator is present.
        compact_cells = [cell for cell in cells if cell]
        separator_start = next(
            (
                index
                for index, cell in enumerate(compact_cells)
                if _TABLE_SEPARATOR.fullmatch(cell)
            ),
            None,
        )
        if separator_start is None or separator_start == 0:
            normalized.append(line)
            continue

        column_count = 0
        while (
            separator_start + column_count < len(compact_cells)
            and _TABLE_SEPARATOR.fullmatch(
                compact_cells[separator_start + column_count]
            )
        ):
            column_count += 1

        if separator_start != column_count:
            normalized.append(line)
            continue

        remaining = compact_cells[2 * column_count :]
        if not remaining or len(remaining) % column_count:
            normalized.append(line)
            continue

        rows = [
            compact_cells[:column_count],
            compact_cells[column_count : 2 * column_count],
        ]
        rows.extend(
            remaining[offset : offset + column_count]
            for offset in range(0, len(remaining), column_count)
        )
        normalized.extend(
            "| " + " | ".join(row) + " |" for row in rows
        )

    return "\n".join(normalized)
