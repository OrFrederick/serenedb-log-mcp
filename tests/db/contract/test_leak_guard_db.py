"""Ground-truth leak guard (T15): no tool result says "abnormal" (tests/support/leak_guard.py).

`derived_sample` (tests/db/conftest.py) relabels the last 600 OpenStack sample lines as coming from
`openstack_abnormal.log`, so a tool that exposed the file name, or anything derived from it, would
say "abnormal" here. Every tool in tools/list is called with its representative inputs
(tests/support/contract_calls.py) for every Role, every enum value of every Evidence tool is swept
once, and every stored record is then fetched again by the Team Lead and the Manager.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from contract_calls import LEDGER_READERS, REPRESENTATIVE_CALLS, WRITING_TOOLS, is_enum, representative_calls
from contract_session import Outcome, listing, run_session
from leak_guard import CELL_EXEMPTION, EVIDENCE_FIELDS, leaks
from mcp_types import Tool
from tool_contract import ROLES

RELABELLED_OPENSTACK_FROM = 1401
"""derived_sample labels OpenStack sample lines 1401 to 2000 as openstack_abnormal.log."""


def called(tools: dict[str, Tool]) -> list[str]:
    """Every tool except a writing tool without representative calls; the ledger readers last, so
    the Evidence they read exists."""
    names = [name for name in tools if name not in WRITING_TOOLS or name in REPRESENTATIVE_CALLS]
    return sorted(names, key=lambda name: (name in LEDGER_READERS, name))


def enum_sweep(tool: Tool, base: dict[str, Any]) -> list[dict[str, Any]]:
    """`base` once with every other value of every enum parameter except role."""
    schema = tool.input_schema
    defs = schema.get("$defs", {})
    calls = []
    for parameter, spec in schema.get("properties", {}).items():
        if parameter == "role" or not is_enum(spec, defs):
            continue
        for option in spec.get("anyOf", [spec]):
            calls += [
                {**base, parameter: value} for value in option.get("enum", []) if base.get(parameter) != value
            ]
    return calls


def plan(tools: dict[str, Tool]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for name in called(tools):
        tool = tools[name]
        representative = representative_calls(name, tool.input_schema) or []
        calls += [(name, {**arguments, "role": role}) for arguments in representative for role in ROLES]
        if representative and set((tool.output_schema or {}).get("properties", {})) == EVIDENCE_FIELDS:
            calls += [(name, {**swept, "role": "engineer"}) for swept in enum_sweep(tool, representative[0])]
    return calls


def evidence(outcomes: list[Outcome]) -> list[dict[str, Any]]:
    return [o.data for o in outcomes if o.data is not None and set(o.data) == EVIDENCE_FIELDS]


def shows_relabelled_openstack_lines(record: dict[str, Any]) -> bool:
    if record["data_scope"]["datasets"] != ["openstack"] or "entry_id" not in record["columns"]:
        return False
    index = record["columns"].index("entry_id")
    return any(row[index] >= RELABELLED_OPENSTACK_FROM for row in record["rows"])


@pytest.mark.usefixtures("sample")
def test_no_tool_result_contains_the_word_abnormal() -> None:
    tools = listing()
    _, outcomes = run_session(plan(tools))
    stored = [record["evidence_id"] for record in evidence(outcomes)]
    _, fetched = run_session(
        [
            ("get_evidence", {"role": role, "evidence_id": id_})
            for id_ in stored
            for role in ("team_lead", "manager")
        ]
    )
    outcomes += fetched

    found = {outcome.label: hits for outcome in outcomes if (hits := leaks(outcome))}
    assert found == {}, f"'abnormal' reached a client: {json.dumps(found, indent=1)[:4000]}"
    # Every tool answered at least one representative call, so the guard is not vacuous.
    answered = {outcome.tool for outcome in outcomes if outcome.data is not None}
    silent = [name for name in called(tools) if name not in answered]
    assert silent == [], f"no representative call succeeded for {silent}; fix REPRESENTATIVE_CALLS"
    assert all(o.error is None for o in fetched), [o.error for o in fetched if o.error][:3]
    # The exemption was exercised, and so were the relabelled OpenStack lines.
    messages = [
        str(cell)
        for record in evidence(outcomes)
        if "message" in record["columns"]
        for cell in (row[record["columns"].index("message")] for row in record["rows"])
    ]
    assert any(CELL_EXEMPTION.search(message) for message in messages)
    assert any(shows_relabelled_openstack_lines(record) for record in evidence(outcomes))
