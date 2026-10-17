"""Truncation honesty under random limits (T15): whatever the limit, the Evidence says what it left out.

For every Evidence tool in tools/list and each of its representative calls
(tests/support/contract_calls.py), the row-limit parameters (limit, top, before, after) take their
minimum, their maximum and `RANDOM_DRAWS` seeded random values within the declared bounds. Every
result, as the Engineer receives it and as the Team Lead and the Manager receive it again through
get_evidence, must:

- count its rows: `returned == len(rows)` and `truncated == (returned < total_matches)`;
- say so: a truncated result's first note names `total_matches` (the Role shaping relies on it);
- stay within its limit (`ROW_BOUNDS`), validate against the published output schema, and stay under
  Claude Code's 50,000-character spill threshold;

and `total_matches` must not depend on the limit, because it is the exact count either way.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest
from contract_calls import (
    PLAN_ROW_LIMITS,
    REPRESENTATIVE_CALLS,
    RESULT_CHARS_LIMIT,
    ROW_LIMIT_PARAMETERS,
    WRITING_TOOLS,
    branches,
    representative_calls,
)
from contract_session import listing, run_session
from mcp_types import Tool
from tool_contract import EVIDENCE_FIELDS

OTHER = "(other)"
"""count_by's row for the groups beyond top."""
RANDOM_DRAWS = 4


def evidence_tools() -> list[str]:
    tools = listing()
    return sorted(
        name
        for name, tool in tools.items()
        if set((tool.output_schema or {}).get("properties", {})) == EVIDENCE_FIELDS
        and name != "get_evidence"
        and (name not in WRITING_TOOLS or name in REPRESENTATIVE_CALLS)
    )


def limit_settings(tool: Tool) -> list[dict[str, int]]:
    """The row-limit values to try: all at their minimum, all at their maximum, and random draws."""
    bounds = {
        name: (option["minimum"], option["maximum"])
        for name, schema in tool.input_schema.get("properties", {}).items()
        if name in ROW_LIMIT_PARAMETERS
        for option in branches(schema)[:1]
    }
    if not bounds:
        return [{}]
    draw = random.Random(f"T15 truncation {tool.name}")
    settings = [{n: low for n, (low, _) in bounds.items()}, {n: high for n, (_, high) in bounds.items()}]
    settings += [
        {n: draw.randint(low, high) for n, (low, high) in bounds.items()} for _ in range(RANDOM_DRAWS)
    ]
    return settings


def named_groups(record: dict[str, Any]) -> int:
    group_by = record["arguments"].get("group_by")
    if group_by == "dataset" or group_by not in record["columns"]:
        return record["returned"]
    index = record["columns"].index(group_by)
    return sum(row[index] != OTHER for row in record["rows"])


def count_by_bound(record: dict[str, Any]) -> list[str]:
    """top groups, plus per Dataset one row for the rest: an (other) row, or the hidden group itself
    when it is the only one (count_by decision, docs/overnight-decisions.md 02:20 UTC)."""
    named, top = named_groups(record), record["arguments"]["top"]
    allowed = top + len(record["data_scope"]["datasets"])
    problems = [f"{record['returned']} rows for top={top}"] if record["returned"] > allowed else []
    if record["role"] != "manager" and record["truncated"] != (named < record["total_matches"]):
        problems.append(
            f"truncated={record['truncated']} with {named} of {record['total_matches']} groups named"
        )
    return problems


def trend_bound(record: dict[str, Any]) -> list[str]:
    series, top = len(record["columns"]) - 1, record["arguments"]["top"]
    return [f"{series} series for top={top}"] if series > top else []


def get_context_bound(record: dict[str, Any]) -> list[str]:
    before, after = record["arguments"]["before"], record["arguments"]["after"]
    summary = record["summary"]
    problems = []
    if record["returned"] > before + after + 1:
        problems.append(f"{record['returned']} rows for before={before}, after={after}")
    if (summary.get("before_returned") or 0) > before or (summary.get("after_returned") or 0) > after:
        problems.append(f"before_returned/after_returned exceed {before}/{after}")
    return problems


def limit_bound(record: dict[str, Any]) -> list[str]:
    limit = record["arguments"].get("limit")
    return (
        [f"{record['returned']} rows for limit={limit}"]
        if limit is not None and record["returned"] > limit
        else []
    )


ROW_BOUNDS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "count_by": count_by_bound,
    "trend": trend_bound,
    "get_context": get_context_bound,
}
"""How a tool's row-limit parameters bound its rows; any other tool: returned <= limit."""


def problems(record: dict[str, Any], output_schema: dict[str, Any], text: str) -> list[str]:
    found = []
    if record["returned"] != len(record["rows"]):
        found.append(f"returned={record['returned']} but {len(record['rows'])} rows")
    if record["truncated"] != (record["returned"] < record["total_matches"]):
        found.append(
            f"truncated={record['truncated']} with {record['returned']} of {record['total_matches']}"
        )
    if record["truncated"] and not (record["notes"] and f"{record['total_matches']:,}" in record["notes"][0]):
        found.append(f"truncated, but the first note does not name {record['total_matches']:,}")
    found += ROW_BOUNDS.get(record["tool"], limit_bound)(record)
    try:
        jsonschema.validate(record, output_schema)
    except jsonschema.ValidationError as exc:
        found.append(f"does not validate against the output schema: {exc.message}")
    if len(text) > RESULT_CHARS_LIMIT:
        found.append(f"{len(text):,} characters, above Claude Code's {RESULT_CHARS_LIMIT:,}")
    return found


@pytest.mark.usefixtures("sample")
@pytest.mark.parametrize("name", evidence_tools())
def test_truncated_tells_the_truth_under_random_limits(name: str) -> None:
    tool = listing()[name]
    calls = [
        (index, {**base, **limits, "role": "engineer"})
        for index, base in enumerate(representative_calls(name, tool.input_schema) or [])
        for limits in limit_settings(tool)
    ]
    _, outcomes = run_session([(name, arguments) for _, arguments in calls])
    failed = [o.label + ": " + (o.error or "")[:300] for o in outcomes if o.error is not None]
    assert failed == [], f"representative calls must succeed: {failed}"
    ids = [o.data["evidence_id"] for o in outcomes if o.data is not None]
    _, fetched = run_session(
        [
            ("get_evidence", {"role": role, "evidence_id": id_})
            for id_ in ids
            for role in ("team_lead", "manager")
        ]
    )

    found: dict[str, list[str]] = {}
    for outcome in [*outcomes, *fetched]:
        assert outcome.data is not None, outcome.error
        if hits := problems(outcome.data, tool.output_schema or {}, outcome.text):
            found[outcome.label] = hits
    assert found == {}, json.dumps(found, indent=1)[:4000]
    # The exact count does not depend on the limit.
    totals: dict[int, set[int]] = {}
    for (index, _), outcome in zip(calls, outcomes, strict=True):
        assert outcome.data is not None
        totals.setdefault(index, set()).add(outcome.data["total_matches"])
    assert all(len(seen) == 1 for seen in totals.values()), totals
    # Not vacuous: at their minimum, the plan's row limits truncate some representative call.
    if name in PLAN_ROW_LIMITS:
        assert any(o.data is not None and o.data["truncated"] for o in outcomes), f"{name} never truncated"
