"""The Role hook: shape one Evidence record for the Role that asks for it (#4, plan section 2.3).

`toolkit` calls `shape_for_role` on every Evidence result, after the record was stored, and
`get_evidence` calls it on every record it fetches, with the Role asking now. The Evidence Ledger
always keeps the full record, so Evidence handed from the Engineer to the Manager by its id is
shaped for the Manager when the Manager fetches it.

What each Role receives:
- every Role: the `answer_frame` of its answer (frames.py);
- Manager: the `not_measured` list, and no raw Log Entries. A raw-line result keeps
  `total_matches`, `summary` (the facets), the SQL and the Data Scope, and a note points to the
  aggregation tools; aggregation rows are kept, they are measured counts;
- Team Lead: at most `TEAM_LEAD_SAMPLE_ROWS` raw Log Entries, labelled as a sample (both #4
  Research comments: a Team Lead answer shows at most a few sample lines per problem, never a
  frequency read from rows);
- Engineer: every row.

A row count lowered here makes the result Truncated, and the record's own truncation note is
replaced, because it counted rows this Role does not get.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.evidence import Evidence
from serenedb_log_mcp.roles.frames import answer_frame, not_measured

RAW_LINE_TOOLS: frozenset[str] = frozenset({"search_logs", "get_context"})
"""Tools whose rows are raw Log Entries. A tool not listed here still counts as raw lines when its
columns include `message` (see `is_raw_line`), so a later tool that returns Log Entry text is
covered without being named."""

RAW_LINE_COLUMN = "message"
ANCHOR_COLUMN = "anchor"
TEAM_LEAD_SAMPLE_ROWS = 3

MANAGER_WITHHELD_NOTE = (
    "Rows withheld for role 'manager': they are raw Log Entries, and a Manager answer uses measured "
    "frequency and affected Components only. total_matches ({total:,}) and summary are exact over "
    "every match. For how often and where, use count_by (group_by component, team, alert_category or "
    "dataset) and trend; to rank problems by Incidents and Spread, use top_patterns or list_incidents."
)
TEAM_LEAD_SAMPLE_NOTE = (
    "Showing {shown} sample Log Entries of {total:,} for role 'team_lead': a sample, not a frequency. "
    "total_matches and summary describe every match, not only these rows; use count_by and trend for "
    "how often, where and since when."
)


def is_raw_line(evidence: Evidence) -> bool:
    """True when the rows of `evidence` are raw Log Entries rather than calculated values."""
    return evidence.tool in RAW_LINE_TOOLS or RAW_LINE_COLUMN in evidence.columns


def shape_for_role(evidence: Evidence, role: Role) -> Evidence:
    """`evidence` as `role` receives it. The given record is never changed."""
    update: dict[str, Any] = {"answer_frame": answer_frame(role), "not_measured": not_measured(role)}
    if is_raw_line(evidence) and evidence.rows:
        if role is Role.MANAGER:
            update |= _replace_rows(evidence, [], MANAGER_WITHHELD_NOTE.format(total=evidence.total_matches))
        elif role is Role.TEAM_LEAD and evidence.returned > TEAM_LEAD_SAMPLE_ROWS:
            sample = _sample(evidence, TEAM_LEAD_SAMPLE_ROWS)
            note = TEAM_LEAD_SAMPLE_NOTE.format(shown=len(sample), total=evidence.total_matches)
            update |= _replace_rows(evidence, sample, note)
    return evidence.model_copy(update=update)


def _replace_rows(evidence: Evidence, rows: list[list[Any]], note: str) -> dict[str, Any]:
    """The fields that change when a Role gets `rows` instead of the stored rows.

    `toolkit._store` puts its truncation note first whenever the stored record is Truncated; that
    note counts the stored rows, so it is dropped and this Role's note leads instead.
    """
    kept = evidence.notes[1:] if evidence.truncated else evidence.notes
    return {
        "rows": rows,
        "returned": len(rows),
        "truncated": len(rows) < evidence.total_matches,
        "notes": [note, *kept],
    }


def _sample(evidence: Evidence, size: int) -> list[list[Any]]:
    """`size` rows: the first ones, or for a context the anchor with its nearest neighbours."""
    rows: Sequence[list[Any]] = evidence.rows
    start = 0
    if ANCHOR_COLUMN in evidence.columns:
        flag = evidence.columns.index(ANCHOR_COLUMN)
        anchor = next((i for i, row in enumerate(rows) if row[flag] is True), 0)
        start = max(0, min(anchor - (size - 1) // 2, len(rows) - size))
    return [list(row) for row in rows[start : start + size]]
