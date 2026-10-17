"""Self-tests of the L0 contract checks: each one catches a deliberately broken input.

A contract test that silently stops catching its problem is worse than none, so every rule in
tests/support/tool_contract.py is fed a real tool from the listing (which passes) and a copy of it
broken in exactly the way the rule exists for (which must fail that rule), and the leak check of
tests/support/leak_guard.py is fed a label leak and the genuine BGL log text.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import anyio
import pytest
from contract_calls import derived_call, free_string_parameters, representative_calls
from contract_session import Outcome
from leak_guard import leaks
from mcp import Client
from mcp_types import Tool, ToolAnnotations
from tool_contract import CHECKS

from serenedb_log_mcp.evidence import DataScope, Evidence, Role
from serenedb_log_mcp.server import create_server


def real_tool(name: str) -> Tool:
    async def tools_list() -> list[Tool]:
        async with Client(create_server(), mode="legacy") as client:
            return (await client.list_tools()).tools

    return next(tool for tool in anyio.run(tools_list) if tool.name == name)


SEARCH_LOGS = real_tool("search_logs")
ROLE_BRIEF = real_tool("get_role_brief")


def broken(tool: Tool, change: Callable[[dict[str, Any]], None]) -> Tool:
    """A deep copy of `tool` (in its wire form) with `change` applied."""
    data = copy.deepcopy(tool.model_dump(by_alias=True, exclude_none=True))
    change(data)
    return Tool.model_validate(data)


def param(data: dict[str, Any], name: str) -> dict[str, Any]:
    properties: dict[str, Any] = data["inputSchema"]["properties"]
    return properties[name]


def drop_role_from_required(data: dict[str, Any]) -> None:
    data["inputSchema"]["required"].remove("role")


def narrow_the_role_enum(data: dict[str, Any]) -> None:
    data["inputSchema"]["$defs"]["Role"]["enum"] = ["engineer"]


def title_is_the_name(data: dict[str, Any]) -> None:
    data["title"] = data["name"]


def no_inputs_heading(data: dict[str, Any]) -> None:
    data["description"] = data["description"].replace("Inputs:", "Takes:")


def an_unnamed_parameter(data: dict[str, Any]) -> None:
    data["inputSchema"]["properties"]["extra"] = {"type": "boolean", "default": False, "description": "x"}


def an_unnamed_output_field(data: dict[str, Any]) -> None:
    data["outputSchema"]["properties"]["secret"] = {"type": "string"}


def a_parameter_without_description(data: dict[str, Any]) -> None:
    del param(data, "limit")["description"]


def not_idempotent(data: dict[str, Any]) -> None:
    data["annotations"]["idempotentHint"] = False


def no_always_load(data: dict[str, Any]) -> None:
    del data["_meta"]


def mode_is_free_text(data: dict[str, Any]) -> None:
    param(data, "mode").pop("enum")


def limit_without_maximum(data: dict[str, Any]) -> None:
    param(data, "limit").pop("maximum")


def query_without_max_length(data: dict[str, Any]) -> None:
    param(data, "query")["anyOf"][0].pop("maxLength")


def components_without_max_items(data: dict[str, Any]) -> None:
    param(data, "components")["anyOf"][0].pop("maxItems")


def limit_above_the_plan(data: dict[str, Any]) -> None:
    param(data, "limit")["maximum"] = 100
    param(data, "limit")["description"] = "How many (default 20, at most 100)."


def limit_not_stated(data: dict[str, Any]) -> None:
    data["description"] = data["description"].replace("at most 50", "a few")
    param(data, "limit")["description"] = "How many Log Entries to return."


def no_output_schema(data: dict[str, Any]) -> None:
    del data["outputSchema"]


def half_an_envelope(data: dict[str, Any]) -> None:
    data["outputSchema"]["properties"].pop("truncated")


@pytest.mark.parametrize(
    ("tool", "change", "rule"),
    [
        (SEARCH_LOGS, drop_role_from_required, "role"),
        (SEARCH_LOGS, narrow_the_role_enum, "role"),
        (SEARCH_LOGS, title_is_the_name, "title"),
        (SEARCH_LOGS, no_inputs_heading, "description"),
        (SEARCH_LOGS, an_unnamed_parameter, "description"),
        (ROLE_BRIEF, an_unnamed_output_field, "description"),
        (SEARCH_LOGS, a_parameter_without_description, "parameter_descriptions"),
        (SEARCH_LOGS, not_idempotent, "annotations"),
        (SEARCH_LOGS, no_always_load, "always_load"),
        (SEARCH_LOGS, mode_is_free_text, "closed_sets"),
        (SEARCH_LOGS, limit_without_maximum, "bounds"),
        (SEARCH_LOGS, query_without_max_length, "bounds"),
        (SEARCH_LOGS, components_without_max_items, "bounds"),
        (SEARCH_LOGS, limit_above_the_plan, "row_limits"),
        (SEARCH_LOGS, limit_not_stated, "row_limits"),
        (SEARCH_LOGS, no_output_schema, "output_schema"),
        (SEARCH_LOGS, half_an_envelope, "output_schema"),
    ],
    ids=lambda value: getattr(value, "__name__", None) or getattr(value, "name", None) or str(value),
)
def test_each_rule_catches_the_tool_broken_for_it(tool: Tool, change: Callable[..., None], rule: str) -> None:
    assert CHECKS[rule](tool) == [], f"the unbroken {tool.name} must pass {rule}"
    assert CHECKS[rule](broken(tool, change)) != [], f"{rule} did not catch {change.__name__}"


def writing(name: str, **hints: bool | None) -> Tool:
    return SEARCH_LOGS.model_copy(update={"name": name, "annotations": ToolAnnotations(**hints)})


def test_only_the_index_tools_may_write_and_they_must_say_so() -> None:
    explicit = {
        "read_only_hint": False,
        "destructive_hint": True,
        "idempotent_hint": False,
        "open_world_hint": False,
    }

    assert CHECKS["annotations"](writing("create_index", **explicit)) == []
    assert CHECKS["annotations"](writing("drop_index", **explicit)) == []
    assert CHECKS["annotations"](writing("count_by", **explicit)) != []
    assert CHECKS["annotations"](writing("create_index", read_only_hint=False)) != []


def test_free_string_parameters_are_the_non_enum_strings_and_string_lists() -> None:
    assert free_string_parameters(SEARCH_LOGS.input_schema) == [
        "query",
        "pattern_ids",
        "family_id",
        "time_from",
        "time_to",
        "components",
        "levels",
        "sources",
        "alert_categories",
    ]


def test_a_call_is_derived_from_the_schema_when_every_required_value_can_be() -> None:
    schema = {
        "properties": {
            "role": {"$ref": "#/$defs/Role"},
            "dataset": {"enum": ["bgl", "openstack"], "type": "string"},
            "entry_id": {"type": "integer", "minimum": 0},
            "strict": {"type": "boolean"},
            "trace_id": {"type": "string", "examples": ["abc123"]},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["role", "dataset", "entry_id", "strict", "trace_id"],
        "$defs": {"Role": {"enum": ["engineer", "team_lead", "manager"]}},
    }

    assert derived_call(schema) == {"dataset": "bgl", "entry_id": 0, "strict": False, "trace_id": "abc123"}
    del schema["properties"]["trace_id"]["examples"]
    assert derived_call(schema) is None
    assert representative_calls("unknown_tool", schema) is None


def evidence_outcome(columns: list[str], rows: list[list[Any]], notes: list[str]) -> Outcome:
    data = Evidence(
        evidence_id="E1",
        tool="sample",
        role=Role.ENGINEER,
        data_scope=DataScope(datasets=["bgl"]),
        total_matches=len(rows),
        returned=len(rows),
        truncated=False,
        query_ms=1.0,
        columns=columns,
        rows=rows,
        notes=notes,
    ).model_dump(mode="json")
    return Outcome("sample", {}, data, None, "")


def test_the_leak_check_catches_a_label_and_keeps_only_the_bgl_log_text() -> None:
    bgl_text = "mmcs_server exited abnormally due to signal: Aborted"

    assert leaks(evidence_outcome(["message"], [[bgl_text]], [])) == []
    assert (
        leaks(evidence_outcome(["template_text"], [["mmcs_server exited abnormally due to signal: <*>"]], []))
        == []
    )
    assert leaks(evidence_outcome(["source"], [["openstack_abnormal.log"]], [])) != []
    assert leaks(evidence_outcome(["message"], [["started"]], [f"see {bgl_text}"])) != []
    assert leaks(Outcome("report", {}, {"markdown": f"| {bgl_text} |"}, None, "")) == []
    assert leaks(Outcome("report", {}, {"markdown": "4 Abnormal instances"}, None, "")) != []
    assert leaks(Outcome("report", {}, None, "openstack_abnormal.log is missing", "")) != []
