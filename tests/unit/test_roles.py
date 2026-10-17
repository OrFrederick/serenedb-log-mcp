"""The Role shaping (T7): the answer frame, the Manager's not-measured list and the row withholding.

The Role hook shapes what the asking Role receives, never what the Evidence Ledger stores, so the
same stored Evidence comes out differently for each Role (#4): the Engineer gets the raw Log
Entries, the Team Lead at most three samples, the Manager none, and all three keep the exact totals.
"""

from typing import Any

import anyio
import pytest
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from sample_tool import (
    SAMPLE_ROWS,
    SEARCH_ROWS,
    SEARCH_SUMMARY,
    SEARCH_TOTAL,
    register_sample_search,
    register_sample_tools,
)

from serenedb_log_mcp import roles, toolkit
from serenedb_log_mcp.evidence import DataScope, Evidence, Role
from serenedb_log_mcp.ledger import get_ledger
from serenedb_log_mcp.roles import (
    NOT_MEASURED,
    RAW_LINE_TOOLS,
    SECTIONS,
    TEAM_LEAD_SAMPLE_ROWS,
    answer_frame,
    is_raw_line,
    not_measured,
    shape_for_role,
)

CONTEXT_COLUMNS = ["entry_id", "ts", "offset_s", "node", "message", "anchor"]


def evidence(
    *,
    tool: str = "search_logs",
    columns: list[str] | None = None,
    rows: list[list[Any]] | None = None,
    total: int = 120,
    notes: list[str] | None = None,
    role: Role = Role.ENGINEER,
) -> Evidence:
    """A stored record the way toolkit._store builds it: the truncation note comes first."""
    rows = [[1, "a"], [2, "b"], [3, "c"], [4, "d"], [5, "e"]] if rows is None else rows
    truncated = len(rows) < total
    stored_notes = [f"Truncated: {len(rows)} of {total:,} matches returned."] if truncated else []
    return Evidence(
        evidence_id="E1",
        tool=tool,
        role=role,
        data_scope=DataScope(datasets=["bgl"]),
        total_matches=total,
        returned=len(rows),
        truncated=truncated,
        query_ms=1.0,
        columns=["entry_id", "message"] if columns is None else columns,
        rows=rows,
        summary={"entries_total": total, "distinct_sources": 3},
        notes=[*stored_notes, *(notes or ["The rows are examples."])],
    )


# The answer frame and not_measured ------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_every_role_gets_the_four_sections_in_order(role: Role) -> None:
    frame = answer_frame(role)

    assert SECTIONS == ("Observed Facts", "Hypotheses", "Unknowns", "Next Step")
    assert [line.split(":", 1)[0] for line in frame] == list(SECTIONS)


def test_the_next_step_differs_in_kind_between_the_roles() -> None:
    steps = {role: answer_frame(role)[-1] for role in Role}

    assert "a check" in steps[Role.ENGINEER]
    assert "an investigation priority" in steps[Role.TEAM_LEAD]
    assert "a prioritised action" in steps[Role.MANAGER]
    assert len(set(steps.values())) == 3


def test_the_frames_of_the_three_roles_differ() -> None:
    frames = [tuple(answer_frame(role)) for role in Role]

    assert len(set(frames)) == 3


def test_only_the_manager_gets_the_not_measured_list() -> None:
    assert not_measured(Role.ENGINEER) == []
    assert not_measured(Role.TEAM_LEAD) == []
    assert not_measured(Role.MANAGER) == list(NOT_MEASURED)


def test_the_not_measured_list_names_cost_downtime_customer_impact_sla_and_root_cause() -> None:
    # OrFrederick's #4 Research: not_measured: [cost, downtime, customer impact, SLA, root cause].
    heads = [item.split(":", 1)[0] for item in NOT_MEASURED]

    for term in ("cost", "downtime", "customer", "SLA", "root cause"):
        assert any(term in head for head in heads), term
    assert any("financial loss" in head for head in heads)  # the brief's own words


def test_the_unknowns_section_of_the_manager_points_at_not_measured() -> None:
    unknowns = answer_frame(Role.MANAGER)[2]

    assert "not_measured" in unknowns
    assert "always" in unknowns


# Which Evidence is raw lines ------------------------------------------------------------------------


def test_search_logs_and_get_context_return_raw_lines() -> None:
    assert {"search_logs", "get_context"} <= RAW_LINE_TOOLS
    assert is_raw_line(evidence(tool="search_logs", columns=["entry_id", "ts"], rows=[[1, "x"]]))


def test_any_evidence_with_a_message_column_is_raw_lines() -> None:
    # A later tool that returns Log Entry text is covered without being listed by name.
    assert is_raw_line(evidence(tool="get_trace", columns=["entry_id", "message"]))


def test_aggregation_evidence_is_not_raw_lines() -> None:
    counted = evidence(
        tool="count_by", columns=["dataset", "alert_category", "count"], rows=[["bgl", "X", 3]]
    )

    assert not is_raw_line(counted)


# The shaping ----------------------------------------------------------------------------------------


def test_the_engineer_keeps_every_row_and_gets_the_frame() -> None:
    stored = evidence()

    shaped = shape_for_role(stored, Role.ENGINEER)

    assert shaped.rows == stored.rows
    assert shaped.notes == stored.notes
    assert shaped.answer_frame == answer_frame(Role.ENGINEER)
    assert shaped.not_measured == []


def test_the_manager_gets_no_raw_rows_but_keeps_the_totals_and_facets() -> None:
    stored = evidence()

    shaped = shape_for_role(stored, Role.MANAGER)

    assert shaped.rows == []
    assert shaped.returned == 0
    assert shaped.truncated is True
    assert shaped.total_matches == stored.total_matches == 120
    assert shaped.summary == stored.summary
    assert shaped.data_scope == stored.data_scope
    assert shaped.sql == stored.sql
    assert shaped.not_measured == list(NOT_MEASURED)
    assert shaped.answer_frame == answer_frame(Role.MANAGER)
    Evidence.model_validate(shaped.model_dump())  # still a valid envelope


def test_the_manager_note_points_to_the_aggregation_tools_and_replaces_the_truncation_note() -> None:
    shaped = shape_for_role(evidence(), Role.MANAGER)

    first = shaped.notes[0]
    assert "withheld" in first
    assert "'manager'" in first
    for tool in ("count_by", "top_patterns", "list_incidents"):
        assert tool in first
    assert "120" in first  # the exact total is restated
    # The stored truncation note counted rows this Role does not get, so it is gone.
    assert not any(note.startswith("Truncated: 5 of") for note in shaped.notes)
    assert "The rows are examples." in shaped.notes


def test_the_manager_keeps_aggregation_rows() -> None:
    counted = evidence(
        tool="count_by", columns=["alert_category", "count"], rows=[["KERNDTLB", 152_734]], total=1
    )

    shaped = shape_for_role(counted, Role.MANAGER)

    assert shaped.rows == [["KERNDTLB", 152_734]]
    assert shaped.notes == counted.notes
    assert shaped.not_measured == list(NOT_MEASURED)


def test_the_manager_with_no_matches_keeps_the_no_match_hint() -> None:
    empty = evidence(rows=[], total=0, notes=["No Log Entries matched in bgl. To find some: ..."])

    shaped = shape_for_role(empty, Role.MANAGER)

    assert shaped.rows == []
    assert shaped.notes == empty.notes


def test_the_team_lead_gets_at_most_three_sample_rows_labelled_as_a_sample() -> None:
    stored = evidence()

    shaped = shape_for_role(stored, Role.TEAM_LEAD)

    assert TEAM_LEAD_SAMPLE_ROWS == 3
    assert shaped.rows == stored.rows[:3]
    assert shaped.returned == 3
    assert shaped.total_matches == 120
    assert "sample, not a frequency" in shaped.notes[0]
    assert "'team_lead'" in shaped.notes[0]
    assert not any(note.startswith("Truncated: 5 of") for note in shaped.notes)
    assert shaped.not_measured == []


def test_the_team_lead_cap_marks_an_untruncated_result_truncated() -> None:
    stored = evidence(total=5)
    assert stored.truncated is False

    shaped = shape_for_role(stored, Role.TEAM_LEAD)

    assert shaped.returned == 3
    assert shaped.truncated is True
    assert "sample, not a frequency" in shaped.notes[0]
    assert shaped.notes[1:] == stored.notes


def test_the_team_lead_keeps_a_small_result_whole() -> None:
    stored = evidence(rows=[[1, "a"], [2, "b"]], total=2)

    shaped = shape_for_role(stored, Role.TEAM_LEAD)

    assert shaped.rows == stored.rows
    assert shaped.notes == stored.notes


def test_the_team_lead_sample_of_a_context_keeps_the_anchor_in_the_middle() -> None:
    rows = [[i, f"t{i}", float(i - 5), "n", f"m{i}", i == 5] for i in range(10)]
    context = evidence(tool="get_context", columns=CONTEXT_COLUMNS, rows=rows, total=40)

    shaped = shape_for_role(context, Role.TEAM_LEAD)

    assert [row[0] for row in shaped.rows] == [4, 5, 6]


def test_the_team_lead_sample_of_a_context_whose_anchor_is_first() -> None:
    rows = [[i, f"t{i}", float(i), "n", f"m{i}", i == 0] for i in range(6)]
    context = evidence(tool="get_context", columns=CONTEXT_COLUMNS, rows=rows, total=6)

    shaped = shape_for_role(context, Role.TEAM_LEAD)

    assert [row[0] for row in shaped.rows] == [0, 1, 2]


def test_shaping_never_changes_the_record_it_was_given() -> None:
    stored = evidence()
    before = stored.model_dump()

    for role in Role:
        shape_for_role(stored, role)

    assert stored.model_dump() == before


# Through the real tool wrapper -----------------------------------------------------------------------


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> MCPServer:
    """A scratch server with both sample tools, and the product's Role hook installed."""
    monkeypatch.setattr(toolkit, "_role_hook", roles.shape_for_role)
    server = MCPServer(name="roles-test")
    register_sample_tools(server)
    register_sample_search(server)
    return server


def call(server: MCPServer, name: str, **arguments: Any) -> dict[str, Any]:
    result = anyio.run(lambda: server.call_tool(name, arguments))
    assert isinstance(result, CallToolResult)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def test_the_role_shaping_is_the_default_role_hook() -> None:
    # toolkit's T3 identity hook is replaced by the Role shaping (T7); the unit-test conftest only
    # installs the identity hook for isolation, so read the module default from a fresh reset.
    toolkit.reset_role_hook()

    assert toolkit.get_role_hook() is roles.shape_for_role


def test_the_manager_reads_no_rows_and_the_ledger_keeps_them(server: MCPServer) -> None:
    data = call(server, "sample_search", role="manager")

    assert data["rows"] == []
    assert data["total_matches"] == SEARCH_TOTAL
    assert data["summary"] == SEARCH_SUMMARY
    assert data["not_measured"] == list(NOT_MEASURED)
    assert "withheld" in data["notes"][0]
    assert not any("Truncated: 5 of" in note for note in data["notes"])
    assert get_ledger().get("E1")["rows"] == SEARCH_ROWS


def test_the_same_call_differs_in_substance_for_each_role(server: MCPServer) -> None:
    results = {role: call(server, "sample_search", role=role.value) for role in Role}

    assert [len(results[role]["rows"]) for role in Role] == [5, 3, 0]
    assert {results[role]["total_matches"] for role in Role} == {SEARCH_TOTAL}
    assert len({tuple(results[role]["answer_frame"]) for role in Role}) == 3


def test_every_evidence_tool_carries_the_answer_frame(server: MCPServer) -> None:
    data = call(server, "sample_count", role="team_lead")

    assert data["answer_frame"] == answer_frame(Role.TEAM_LEAD)
    assert data["rows"] == SAMPLE_ROWS
    assert data["not_measured"] == []
