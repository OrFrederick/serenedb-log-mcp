"""What the L0 contract suite (T15) knows about the tools that `tools/list` cannot say.

The contract tests iterate over whatever `tools/list` returns, so a tool that lands later is checked
without editing them. Only the rules and inputs that cannot be read from the listing live here:

- `WRITING_TOOLS`: the only tools that may be not read-only (plan section 2.3).
- `CLOSED_SET_PARAMETERS`: parameter names whose values form a closed set, so they must be enums.
- `ROW_LIMIT_PARAMETERS` and `PLAN_ROW_LIMITS`: the parameters that cap the returned rows, and the
  defaults and maximums plan section 2.3 fixes for them.
- `REPRESENTATIVE_CALLS`: arguments (without `role`) for calls on the Loghub 2k samples as
  `tests/db/conftest.py`'s `derived_sample` builds them. A tool that is not listed gets one call
  derived from its input schema (`derived_call`); if its required parameters cannot be derived, the
  pure contract test names the tool and asks for an entry here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SERVER_PREFIX = "mcp__serenedb-log__"
"""How Claude Code names our tools: `mcp__<server name in .mcp.json>__<tool>`."""

TOOL_NAME_LIMIT = 64
"""The Claude API accepts tool names of at most 64 characters (`^[a-zA-Z0-9_-]{1,64}$`)."""

CORE_TOOLS = frozenset(
    {
        "describe_corpus",
        "search_logs",
        "get_context",
        "count_by",
        "trend",
        "latency_stats",
        "get_evidence",
        "get_role_brief",
        "verify_answer",
    }
)
"""Tools merged before T15; tools/list must keep offering them."""

WRITING_TOOLS = frozenset({"create_index", "drop_index"})
"""The only tools that may set readOnlyHint=false (plan section 2.3, T19)."""

LEDGER_READERS = frozenset({"get_evidence", "verify_answer", "export_investigation"})
"""Tools that read the Evidence Ledger, so a session calls them after the Evidence tools."""

CLOSED_SET_PARAMETERS = frozenset(
    {
        "role",
        "dataset",
        "datasets",
        "group_by",
        "bucket",
        "mode",
        "sort",
        "scope",
        "metric",
        "merge_scope",
        "rank_by",
        "audience",
        "format",
    }
)
"""Parameter names whose values are a closed set; each must be an enum (or a list of enum values)."""

ROW_LIMIT_PARAMETERS = frozenset({"limit", "top", "before", "after"})
"""Integer parameters that cap how many rows (or series) a result returns."""

PLAN_ROW_LIMITS: Mapping[str, tuple[int, int]] = {
    "search_logs": (20, 50),
    "count_by": (25, 100),
    "get_context": (10, 50),
    "latency_stats": (25, 50),
    "list_incidents": (20, 100),
    "top_patterns": (20, 50),
}
"""(default, maximum) of each row-limit parameter of a tool, from plan section 2.3 "Limits"."""

RESULT_CHARS_LIMIT = 50_000
"""Claude Code spills a tool result above 50,000 characters to a file (plan section 2.3)."""

SAMPLE_DRAFT = """## Observed Facts
- The two Datasets hold 4,011 Log Entries [E1].

## Hypotheses
- None yet.

## Unknowns
- Cost, downtime and customer impact cannot be established from these logs.

## Next Step
- Check the KERNEL Log Entries first.
"""

REPRESENTATIVE_CALLS: Mapping[str, list[dict[str, Any]]] = {
    "describe_corpus": [{}, {"dataset": "openstack"}],
    "search_logs": [
        {"dataset": "bgl", "query": "error"},
        {"dataset": "openstack", "query": "instance"},
        # The one MASABNORM Log Entry in the samples says "exited abnormally" (tests/db/conftest.py).
        {"dataset": "bgl", "alert_categories": ["MASABNORM"]},
    ],
    "get_context": [
        {"dataset": "bgl", "entry_id": 900_010},
        {"dataset": "openstack", "entry_id": 1500, "scope": "window", "window_s": 600},
    ],
    "count_by": [
        {"group_by": "dataset"},
        {"group_by": "component"},
        {"group_by": "node", "dataset": "bgl"},
        {"group_by": "level", "dataset": "openstack", "query": "instance"},
    ],
    "trend": [
        {"dataset": "openstack", "bucket": "minute"},
        {"dataset": "bgl", "bucket": "week", "group_by": "alert_category"},
    ],
    "latency_stats": [
        {},
        {"metric": "spawn"},
        {"metric": "build", "limit": 5},
        {"group_by": "bucket", "bucket": "minute"},
    ],
    "get_evidence": [{"evidence_id": "E1"}],
    "get_role_brief": [{}],
    "verify_answer": [{"draft": SAMPLE_DRAFT}],
    "search_patterns": [{"dataset": "bgl", "query": "error"}, {"dataset": "openstack", "query": "instance"}],
    # MASABNORM is an alert category of the samples (see search_logs above).
    "find_precursors": [{"dataset": "bgl", "target_pattern": "MASABNORM", "min_support": 1}],
    "export_investigation": [
        {
            "title": "Contract check",
            "answers": [{"role": "engineer", "question": "What is there?", "answer": SAMPLE_DRAFT}],
        }
    ],
}


def branches(schema: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The non-null alternatives of a parameter schema (`anyOf: [X, null]` gives [X])."""
    options = schema.get("anyOf", [schema])
    return [option for option in options if option.get("type") != "null"]


def is_enum(schema: Mapping[str, Any], defs: Mapping[str, Any]) -> bool:
    """True when every non-null alternative is an enum, a `$ref` to one, or a list of them."""
    for option in branches(schema):
        if "$ref" in option:
            option = defs[option["$ref"].rsplit("/", 1)[-1]]
        if option.get("type") == "array":
            option = option.get("items", {})
            if "$ref" in option:
                option = defs[option["$ref"].rsplit("/", 1)[-1]]
        if "enum" not in option and "const" not in option:
            return False
    return True


def free_string_parameters(input_schema: Mapping[str, Any]) -> list[str]:
    """Parameters that take free text: strings or lists of strings that are not enums."""
    defs = input_schema.get("$defs", {})
    names = []
    for name, schema in input_schema.get("properties", {}).items():
        if is_enum(schema, defs):
            continue
        for option in branches(schema):
            item = option.get("items", {}) if option.get("type") == "array" else option
            if item.get("type") == "string":
                names.append(name)
                break
    return names


def derived_call(input_schema: Mapping[str, Any]) -> dict[str, Any] | None:
    """Arguments for the required parameters, read from the schema; None if one cannot be derived.

    Enums take their first value, numbers their default or minimum, booleans false, and strings
    their first `examples` entry. A required free-text parameter without examples cannot be guessed.
    """
    defs = input_schema.get("$defs", {})
    arguments: dict[str, Any] = {}
    for name in input_schema.get("required", []):
        if name == "role":
            continue
        schema = input_schema["properties"][name]
        if "default" in schema:
            arguments[name] = schema["default"]
            continue
        option = branches(schema)[0]
        if "$ref" in option:
            option = defs[option["$ref"].rsplit("/", 1)[-1]]
        if "enum" in option:
            arguments[name] = option["enum"][0]
        elif option.get("type") in {"integer", "number"}:
            arguments[name] = option.get("minimum", 0)
        elif option.get("type") == "boolean":
            arguments[name] = False
        elif schema.get("examples") or option.get("examples"):
            arguments[name] = (schema.get("examples") or option["examples"])[0]
        else:
            return None
    return arguments


def representative_calls(name: str, input_schema: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """The calls (without role) the db contract tests make for tool `name`; None if unknown."""
    if name in REPRESENTATIVE_CALLS:
        return [dict(call) for call in REPRESENTATIVE_CALLS[name]]
    derived = derived_call(input_schema)
    return None if derived is None else [derived]


def is_evidence_schema(output_schema: Mapping[str, Any] | None, evidence_fields: set[str]) -> bool:
    """True when a tool publishes the Evidence envelope as its output schema."""
    return output_schema is not None and set(output_schema.get("properties", {})) == evidence_fields
