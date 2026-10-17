"""The L0 contract rules (T15) as pure checks over one `tools/list` entry.

Each check takes an `mcp_types.Tool`, as a client receives it, and returns the problems it found as
sentences (an empty list when the tool keeps the rule). tests/contract/test_tool_contract.py runs
every check on every tool the server lists; tests/contract/test_contract_checks.py feeds them
deliberately broken tools, so a check that stops catching its problem fails too.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from contract_calls import (
    CLOSED_SET_PARAMETERS,
    PLAN_ROW_LIMITS,
    ROW_LIMIT_PARAMETERS,
    WRITING_TOOLS,
    branches,
    free_string_parameters,
    is_enum,
    is_evidence_schema,
)
from mcp_types import Tool

from serenedb_log_mcp.evidence import Evidence

ROLES = ["engineer", "team_lead", "manager"]
EVIDENCE_FIELDS = set(Evidence.model_fields)
PURPOSE_MIN_CHARS = 60
"""The purpose before "Inputs:" must say more than a name does."""

READ_ONLY_HINTS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

Check = Callable[[Tool], list[str]]


def _parameters(tool: Tool) -> dict[str, Any]:
    properties: dict[str, Any] = tool.input_schema.get("properties", {})
    return properties


def _named(word: str, text: str) -> bool:
    """True when `word` appears in `text` as a whole identifier."""
    return re.search(rf"(?<![\w.]){re.escape(word)}(?!\w)", text) is not None


def description_sections(description: str) -> tuple[str, str, str] | None:
    """The description split into purpose, inputs and outputs; None without both headings in order."""
    inputs = re.search(r"^Inputs\b[^:\n]*:", description, re.MULTILINE)
    outputs = re.search(r"^Outputs\b[^:\n]*:", description, re.MULTILINE)
    if inputs is None or outputs is None or inputs.start() > outputs.start():
        return None
    return (
        description[: inputs.start()],
        description[inputs.end() : outputs.start()],
        description[outputs.end() :],
    )


def role_problems(tool: Tool) -> list[str]:
    """`role` is required and is the three-Role enum, with a description (#4)."""
    schema = tool.input_schema
    role = _parameters(tool).get("role")
    if role is None:
        return ["has no role parameter"]
    problems = []
    if "role" not in schema.get("required", []):
        problems.append("role is not required")
    if not str(role.get("description", "")).strip():
        problems.append("role has no description")
    target = role
    if "$ref" in role:
        target = schema.get("$defs", {}).get(role["$ref"].rsplit("/", 1)[-1], {})
    if target.get("enum") != ROLES:
        problems.append(f"role is not the enum {ROLES}")
    return problems


def title_problems(tool: Tool) -> list[str]:
    """A title in words, other than the name."""
    title = tool.title or ""
    if not title.strip():
        return ["has no title"]
    if title == tool.name or " " not in title:
        return [f"the title {title!r} is an identifier, not a label"]
    return []


def description_problems(tool: Tool) -> list[str]:
    """The description states the purpose, names every input and summarises the outputs.

    The model never sees the output schema (plan section 2.6), so the text must say what comes
    back: for an Evidence tool what its rows and summary hold, for any other tool every field.
    """
    parts = description_sections(tool.description or "")
    if parts is None:
        return ["the description has no 'Inputs:' line followed by an 'Outputs:' line"]
    purpose, inputs, outputs = parts
    problems = []
    if len(purpose.strip()) < PURPOSE_MIN_CHARS:
        problems.append("the purpose before 'Inputs:' is missing or too short")
    unnamed = [name for name in _parameters(tool) if not _named(name, inputs)]
    if unnamed:
        problems.append(f"the Inputs section does not name {unnamed}")
    output_schema = tool.output_schema or {}
    if is_evidence_schema(output_schema, EVIDENCE_FIELDS):
        if not (_named("rows", outputs) or _named("row", outputs)):
            problems.append("the Outputs section does not say what the rows hold")
        if "summary" not in outputs:
            problems.append("the Outputs section does not say what the summary holds")
    else:
        missing = [field for field in output_schema.get("properties", {}) if not _named(field, outputs)]
        if missing:
            problems.append(f"the Outputs section does not name the output fields {missing}")
    return problems


def parameter_description_problems(tool: Tool) -> list[str]:
    """Every parameter has a description the model can read."""
    missing = [
        name for name, schema in _parameters(tool).items() if not str(schema.get("description", "")).strip()
    ]
    return [f"parameters without a description: {missing}"] if missing else []


def annotation_problems(tool: Tool) -> list[str]:
    """Read-only hints on every tool; only create_index and drop_index may differ (plan 2.3)."""
    annotations = tool.annotations
    if annotations is None:
        return ["has no annotations"]
    hints = {
        "readOnlyHint": annotations.read_only_hint,
        "destructiveHint": annotations.destructive_hint,
        "idempotentHint": annotations.idempotent_hint,
        "openWorldHint": annotations.open_world_hint,
    }
    if tool.name in WRITING_TOOLS:
        unset = [hint for hint, value in hints.items() if value is None]
        return [f"a writing tool must set every hint explicitly; unset: {unset}"] if unset else []
    if hints != READ_ONLY_HINTS:
        return [f"annotations {hints}; only {sorted(WRITING_TOOLS)} may differ from {READ_ONLY_HINTS}"]
    return []


def always_load_problems(tool: Tool) -> list[str]:
    """`_meta["anthropic/alwaysLoad"]` is true; without it Claude Code defers the tool to ToolSearch."""
    meta = tool.meta or {}
    return [] if meta.get("anthropic/alwaysLoad") is True else ["_meta['anthropic/alwaysLoad'] is not true"]


def closed_set_problems(tool: Tool) -> list[str]:
    """Closed-set parameters (CLOSED_SET_PARAMETERS) are enums."""
    defs = tool.input_schema.get("$defs", {})
    loose = [
        name
        for name, schema in _parameters(tool).items()
        if name in CLOSED_SET_PARAMETERS and not is_enum(schema, defs)
    ]
    return [f"closed-set parameters that are not enums: {loose}"] if loose else []


def bound_problems(tool: Tool) -> list[str]:
    """Every input is bounded: numbers have a minimum (row limits a maximum too), lists maxItems,
    free text maxLength."""
    unbounded = []
    for name, schema in _parameters(tool).items():
        for option in branches(schema):
            kind = option.get("type")
            if kind in {"integer", "number"}:
                if "minimum" not in option and "exclusiveMinimum" not in option:
                    unbounded.append(f"{name} (no minimum)")
                if name in ROW_LIMIT_PARAMETERS and "maximum" not in option:
                    unbounded.append(f"{name} (a row limit without a maximum)")
            elif kind == "array" and "maxItems" not in option:
                unbounded.append(f"{name} (no maxItems)")
    for name in free_string_parameters(tool.input_schema):
        for option in branches(_parameters(tool)[name]):
            if option.get("type") == "string" and "maxLength" not in option:
                unbounded.append(f"{name} (no maxLength)")
    return [f"inputs without declared bounds: {unbounded}"] if unbounded else []


def row_limit_problems(tool: Tool) -> list[str]:
    """Row limits match plan section 2.3 and the description states the default and the maximum."""
    limits: dict[str, Mapping[str, Any]] = {
        name: {**branches(schema)[0], "default": schema.get("default")}
        for name, schema in _parameters(tool).items()
        if name in ROW_LIMIT_PARAMETERS
    }
    planned = PLAN_ROW_LIMITS.get(tool.name)
    if planned is not None and not limits:
        return [
            f"plan 2.3 sets a row limit {planned}, but there is no {sorted(ROW_LIMIT_PARAMETERS)} parameter"
        ]
    problems = []
    for name, schema in limits.items():
        default, maximum = schema["default"], schema.get("maximum")
        if planned is not None and (default, maximum) != planned:
            problems.append(f"{name} has default {default} and maximum {maximum}; plan 2.3 says {planned}")
        text = f"{tool.description or ''}\n{_parameters(tool)[name].get('description', '')}"
        if f"at most {maximum}" not in text:
            problems.append(f"{name}: the maximum {maximum} is not stated as 'at most {maximum}'")
        if default != maximum and f"default {default}" not in text:
            problems.append(f"{name}: the default {default} is not stated as 'default {default}'")
    return problems


def output_schema_problems(tool: Tool) -> list[str]:
    """An object output schema; a tool that returns Evidence publishes exactly the envelope."""
    schema = tool.output_schema
    if schema is None:
        return ["has no output schema"]
    if schema.get("type") != "object" or not schema.get("properties"):
        return ["the output schema is not an object with properties"]
    if "evidence_id" in schema["properties"] and not is_evidence_schema(schema, EVIDENCE_FIELDS):
        return ["it returns an evidence_id, so its output schema must be exactly the Evidence envelope"]
    return []


CHECKS: dict[str, Check] = {
    "role": role_problems,
    "title": title_problems,
    "description": description_problems,
    "parameter_descriptions": parameter_description_problems,
    "annotations": annotation_problems,
    "always_load": always_load_problems,
    "closed_sets": closed_set_problems,
    "bounds": bound_problems,
    "row_limits": row_limit_problems,
    "output_schema": output_schema_problems,
}
"""Every rule of the tool contract, by name."""
