"""`toolkit.evidence_tool`: the envelope, the ledger write, the Role hook, the pin and ToolError."""

import json
import logging
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult
from sample_tool import SAMPLE_ROWS, SAMPLE_SQL, FakeQueryResult, register_sample_tools, sample_outcome

from serenedb_log_mcp.config import load_settings, set_settings
from serenedb_log_mcp.evidence import DataScope, Evidence, Role, RoleParam
from serenedb_log_mcp.ledger import Ledger, get_ledger
from serenedb_log_mcp.toolkit import (
    ALWAYS_LOAD_META,
    RETRY_HINT,
    ToolContractError,
    ToolOutcome,
    evidence_tool,
    set_role_hook,
)


@pytest.fixture
def server() -> MCPServer:
    """A scratch server with the sample Evidence tool registered."""
    server = MCPServer(name="toolkit-test")
    register_sample_tools(server)
    return server


def sample_count(server: MCPServer, **arguments: Any) -> CallToolResult:
    """Call the sample tool the way the SDK does, and return its result."""
    result = anyio.run(lambda: server.call_tool("sample_count", {"role": "team_lead", **arguments}))
    assert isinstance(result, CallToolResult)
    return result


def envelope(result: CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None
    return result.structured_content


def test_a_tool_without_a_role_parameter_is_rejected() -> None:
    server = MCPServer(name="t")

    with pytest.raises(ToolContractError, match="role"):

        @evidence_tool(server, title="No Role")
        def no_role(query: str) -> ToolOutcome:
            """Missing the Role."""
            return sample_outcome()


def test_a_tool_whose_role_is_not_the_role_enum_is_rejected() -> None:
    server = MCPServer(name="t")

    with pytest.raises(ToolContractError, match="Role"):

        @evidence_tool(server, title="Wrong Role type")
        def plain_role(role: str) -> ToolOutcome:
            """Role is not the enum."""
            return sample_outcome()


def test_the_tool_is_registered_with_a_title_read_only_hints_and_always_load(
    server: MCPServer,
) -> None:
    tools = {tool.name: tool for tool in anyio.run(server.list_tools)}

    tool = tools["sample_count"]
    assert tool.title == "Sample: count Log Entries by alert category"
    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False
    assert tool.description is not None
    assert "Outputs:" in tool.description
    assert tool.input_schema["required"] == ["role"]


def test_the_envelope_carries_the_query_the_scope_and_the_counts(server: MCPServer) -> None:
    data = envelope(sample_count(server))

    assert data["evidence_id"] == "E1"
    assert data["tool"] == "sample_count"
    assert data["role"] == "team_lead"
    assert data["sql"] == [SAMPLE_SQL]
    assert data["params"] == [[25]]
    assert data["data_scope"]["datasets"] == ["bgl"]
    assert data["data_scope"]["filters"]["group_by"] == "alert_tag"
    assert data["total_matches"] == len(SAMPLE_ROWS)
    assert data["returned"] == len(SAMPLE_ROWS)
    assert data["truncated"] is False
    assert data["query_ms"] == 12.3
    assert data["columns"] == ["alert_tag", "entries"]
    assert data["rows"] == SAMPLE_ROWS
    assert data["summary"]["entries_total"] == 265_876


def test_the_envelope_records_the_arguments_the_tool_was_called_with(server: MCPServer) -> None:
    # T13 renders the tool calls with their parameters, and a replay needs things like the sort
    # mode, so the wrapper records the bound arguments instead of each tool copying them itself.
    # The SDK validates the input against the schema first, so a default the caller left out is
    # bound too and the record is the call as it ran.
    data = envelope(sample_count(server, groups=2))

    assert data["arguments"] == {"groups": 2, "fail": False}
    assert get_ledger().get("E1")["arguments"] == {"groups": 2, "fail": False}


def test_the_recorded_arguments_never_include_the_role(server: MCPServer) -> None:
    # The Role is already a field of its own, and it selects the connection, so it is not a filter.
    data = envelope(sample_count(server, role="manager", groups=1))

    assert data["role"] == "manager"
    assert "role" not in data["arguments"]


def test_only_the_tools_own_parameters_can_reach_the_recorded_arguments() -> None:
    # The guard against a secret landing in the Evidence: the keys can only be parameters the tool
    # declares, which are search terms, Dataset names, time ranges and limits. Credentials live in
    # DATABASE_URL and DATABASE_URL_<ROLE> in the environment and are never tool parameters.
    server = MCPServer(name="t")

    @evidence_tool(server, title="Many parameters")
    def parameterised(role: RoleParam, query: str = "", limit: int = 20) -> ToolOutcome:
        """Take two parameters. Inputs: role, query, limit. Outputs: the envelope."""
        return sample_outcome()

    result = anyio.run(
        lambda: server.call_tool("parameterised", {"role": "engineer", "query": "ciod failed"})
    )
    assert isinstance(result, CallToolResult)
    recorded = envelope(result)["arguments"]

    assert recorded == {"query": "ciod failed", "limit": 20}
    assert set(recorded) <= {"query", "limit"}


def test_an_evidence_record_written_before_arguments_existed_still_validates() -> None:
    stored = {
        "evidence_id": "E1",
        "tool": "count_by",
        "role": "team_lead",
        "data_scope": {"datasets": ["bgl"]},
        "total_matches": 1,
        "returned": 1,
        "truncated": False,
        "query_ms": 1.0,
    }

    assert Evidence.model_validate(stored).arguments == {}


def test_one_text_block_mirrors_the_structured_content(server: MCPServer) -> None:
    result = sample_count(server)

    assert len(result.content) == 1
    block = result.content[0]
    assert block.type == "text"
    assert json.loads(block.text) == envelope(result)


def test_every_call_is_appended_to_the_evidence_ledger(server: MCPServer) -> None:
    sample_count(server)
    sample_count(server)

    records = get_ledger().all()
    assert [record["evidence_id"] for record in records] == ["E1", "E2"]
    # The #3 pilot bug: the ledger record must hold the finished envelope, summary included.
    assert records[0]["summary"]["groups_total"] == 3
    assert records[0]["summary"]["entries_total"] == 265_876
    assert records[0]["rows"] == SAMPLE_ROWS
    assert Evidence.model_validate(records[0]).tool == "sample_count"


def test_the_ledger_file_is_shared_with_later_sessions(server: MCPServer, ledger_path: Path) -> None:
    sample_count(server)

    assert Ledger(ledger_path).get("E1") is not None


def test_the_role_hook_shapes_the_answer_but_never_the_stored_evidence(server: MCPServer) -> None:
    def withhold_rows(evidence: Evidence, role: Role) -> Evidence:
        assert role is Role.TEAM_LEAD
        return evidence.model_copy(update={"rows": [], "not_measured": ["financial loss"]})

    set_role_hook(withhold_rows)
    data = envelope(sample_count(server))

    assert data["rows"] == []
    assert data["not_measured"] == ["financial loss"]
    assert data["returned"] == len(SAMPLE_ROWS)  # the count stays honest
    assert get_ledger().get("E1")["rows"] == SAMPLE_ROWS


def test_a_role_hook_that_breaks_the_contract_is_an_actionable_error(server: MCPServer) -> None:
    # model_copy(update=...) skips validation, so a T7 hook that lowers `returned` and leaves
    # `truncated` alone used to fail the SDK's own output validation as a bare UnexpectedToolError
    # with no text at all ("Error executing tool sample_count").
    def drop_the_count(evidence: Evidence, role: Role) -> Evidence:
        return evidence.model_copy(update={"returned": 0})

    set_role_hook(drop_the_count)

    with pytest.raises(ToolError) as raised:
        sample_count(server)

    message = str(raised.value)
    assert "Role shaping" in message
    assert "truncated" in message
    assert "returned=0" in message
    assert "bug in this server" in message


def test_a_role_hook_that_keeps_the_contract_is_passed_through(server: MCPServer) -> None:
    def withhold_rows(evidence: Evidence, role: Role) -> Evidence:
        return evidence.model_copy(update={"rows": [], "returned": 0, "truncated": True})

    set_role_hook(withhold_rows)
    data = envelope(sample_count(server))

    assert data["rows"] == []
    assert data["returned"] == 0
    assert data["truncated"] is True
    # The stored record is untouched, as always.
    assert get_ledger().get("E1")["returned"] == len(SAMPLE_ROWS)


def test_a_role_other_than_the_pin_is_rejected_before_the_query_runs(server: MCPServer) -> None:
    set_settings(load_settings({}, role="manager"))

    with pytest.raises(ToolError, match="--role") as raised:
        sample_count(server, role="engineer")

    assert "manager" in str(raised.value)
    assert get_ledger().all() == []


def test_the_pinned_role_is_accepted(server: MCPServer) -> None:
    set_settings(load_settings({}, role="manager"))

    assert envelope(sample_count(server, role="manager"))["role"] == "manager"


def test_an_unknown_role_names_the_three_roles(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="team_lead") as raised:
        sample_count(server, role="ceo")

    assert "ceo" in str(raised.value)


def test_an_anticipated_failure_keeps_its_own_message(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="Widen the time range"):
        sample_count(server, fail=True)

    assert get_ledger().all() == []


def test_any_other_exception_becomes_a_tool_error_with_a_next_step() -> None:
    server = MCPServer(name="t")

    @evidence_tool(server, title="Crashing tool")
    def crashing(role: RoleParam) -> ToolOutcome:
        """Crash. Inputs: role. Outputs: nothing."""
        raise RuntimeError("psycopg lost the connection")

    with pytest.raises(ToolError) as raised:
        anyio.run(lambda: server.call_tool("crashing", {"role": "engineer"}))

    message = str(raised.value)
    assert "psycopg lost the connection" in message
    assert "Retry" in message


def test_an_unexpected_crash_is_logged_with_its_traceback(caplog: pytest.LogCaptureFixture) -> None:
    # The SDK logs a ToolError with logger.info and no traceback, so wrapping an unexpected
    # exception removes the only stack trace there is. stderr is safe in stdio mode.
    server = MCPServer(name="t")

    @evidence_tool(server, title="Crashing tool")
    def key_error(role: RoleParam) -> ToolOutcome:
        """Crash on a missing summary key. Inputs: role. Outputs: the envelope."""
        raise KeyError("entries_total")

    with (
        caplog.at_level(logging.ERROR, logger="serenedb_log_mcp.toolkit"),
        pytest.raises(ToolError),
    ):
        anyio.run(lambda: server.call_tool("key_error", {"role": "engineer"}))

    records = [record for record in caplog.records if record.name == "serenedb_log_mcp.toolkit"]
    assert len(records) == 1
    assert "key_error" in records[0].getMessage()
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is KeyError
    assert "KeyError" in caplog.text
    assert "Traceback" in caplog.text


def test_a_validation_error_names_the_field_and_the_reason() -> None:
    # str(ValidationError) starts with "2 validation errors for DataScope.", so keeping only the
    # first line dropped everything actionable.
    server = MCPServer(name="t")

    @evidence_tool(server, title="Bad Data Scope")
    def bad_scope(role: RoleParam) -> ToolOutcome:
        """Build an invalid Data Scope. Inputs: role. Outputs: the envelope."""
        return ToolOutcome(
            result=FakeQueryResult(),
            scope=DataScope(datasets="bgl", time_from=7),  # type: ignore[arg-type]
            columns=["alert_tag", "entries"],
        )

    with pytest.raises(ToolError) as raised:
        anyio.run(lambda: server.call_tool("bad_scope", {"role": "engineer"}))

    message = str(raised.value)
    assert "DataScope" in message
    assert "datasets" in message
    assert "time_from" in message
    assert "valid string" in message or "valid list" in message


def test_a_broken_contract_is_reported_as_a_server_bug_without_a_retry() -> None:
    # A ToolContractError is deterministic: the same call fails the same way, so "Retry the call
    # once" is the wrong advice.
    server = MCPServer(name="t")

    @evidence_tool(server, title="Mislabelled rows")
    def mislabelled_rows(role: RoleParam) -> ToolOutcome:
        """Return rows that do not match the columns. Inputs: role. Outputs: the envelope."""
        return ToolOutcome(
            result=FakeQueryResult(total=1, rows=[["KERNEL", 1, "extra"]]),
            scope=DataScope(datasets=["bgl"]),
            columns=["component", "entries"],
        )

    with pytest.raises(ToolError) as raised:
        anyio.run(lambda: server.call_tool("mislabelled_rows", {"role": "engineer"}))

    message = str(raised.value)
    assert "columns" in message
    assert "bug in this server" in message
    assert "calling it again will not help" in message
    assert RETRY_HINT not in message
    assert "Retry the call" not in message


def test_rows_wider_than_their_columns_are_rejected() -> None:
    server = MCPServer(name="t")

    @evidence_tool(server, title="Mislabelled rows")
    def mislabelled(role: RoleParam) -> ToolOutcome:
        """Return rows that do not match the columns. Inputs: role. Outputs: the envelope."""
        return ToolOutcome(
            result=FakeQueryResult(total=1, rows=[["KERNEL", 1, "extra"]]),
            scope=DataScope(datasets=["bgl"]),
            columns=["component", "entries"],
        )

    with pytest.raises(ToolError, match="columns"):
        anyio.run(lambda: server.call_tool("mislabelled", {"role": "engineer"}))


def test_a_truncated_result_says_so_in_the_notes(server: MCPServer) -> None:
    data = envelope(sample_count(server, groups=1))

    assert data["truncated"] is True
    assert data["returned"] == 1
    assert data["total_matches"] == 3
    assert any("1 of 3" in note for note in data["notes"])
    assert any("count_by" in note for note in data["notes"])


def test_the_stored_sql_is_the_statement_that_ran() -> None:
    # Collapsing whitespace changed the statement: a "--" comment swallowed the rest of the line,
    # and a literal with two spaces was rewritten. T13's replay re-runs exactly these strings.
    server = MCPServer(name="t")
    commented = "SELECT count(*)\n  FROM bgl_entry -- index scan, see #2\n WHERE component = %s"
    with_literal = "SELECT 'two  spaces' AS marker"

    @evidence_tool(server, title="Commented SQL")
    def commenting(role: RoleParam) -> ToolOutcome:
        """Run SQL with a comment and a literal. Inputs: role. Outputs: the envelope."""
        return ToolOutcome(
            result=FakeQueryResult(
                total=1,
                rows=[["KERNEL", 1]],
                sql=[commented, with_literal],
                params=[["KERNEL"], []],
            ),
            scope=DataScope(datasets=["bgl"]),
            columns=["alert_tag", "entries"],
        )

    result = anyio.run(lambda: server.call_tool("commenting", {"role": "engineer"}))
    assert isinstance(result, CallToolResult)
    stored = envelope(result)["sql"]

    assert stored == [commented, with_literal]
    # The WHERE clause stays on its own line, so the comment cannot comment it out.
    assert "-- index scan, see #2\n" in stored[0]
    assert "'two  spaces'" in stored[1]
    assert get_ledger().get("E1")["sql"] == [commented, with_literal]


def test_an_indented_sql_template_is_dedented_and_stripped() -> None:
    # A triple-quoted template carries the module's indentation; dedent removes it without
    # touching the line structure or anything inside a literal.
    server = MCPServer(name="t")
    template = """
        SELECT alert_tag, count(*)
          FROM bgl
         GROUP BY 1
    """

    @evidence_tool(server, title="Indented SQL")
    def indented(role: RoleParam) -> ToolOutcome:
        """Run an indented template. Inputs: role. Outputs: the envelope."""
        return ToolOutcome(
            result=FakeQueryResult(total=1, rows=[["KERNEL", 1]], sql=[template], params=[[]]),
            scope=DataScope(datasets=["bgl"]),
            columns=["alert_tag", "entries"],
        )

    result = anyio.run(lambda: server.call_tool("indented", {"role": "engineer"}))
    assert isinstance(result, CallToolResult)

    assert envelope(result)["sql"] == ["SELECT alert_tag, count(*)\n  FROM bgl\n GROUP BY 1"]


def test_an_aggregation_tool_replaces_the_truncation_note_with_its_own() -> None:
    # A top-N aggregation returns exact counts plus an (other) row, so the default note ("a sample,
    # not a frequency; use count_by") states the opposite of the truth and points count_by at itself.
    server = MCPServer(name="t")
    own_note = "Showing the top 3 of 31,468 groups; the (other) row holds the rest, so the counts are exact."

    @evidence_tool(server, title="Counting groups")
    def grouping(role: RoleParam) -> ToolOutcome:
        """Count groups. Inputs: role. Outputs: the envelope."""
        return ToolOutcome(
            result=FakeQueryResult(total=348_460, rows=[list(row) for row in SAMPLE_ROWS]),
            scope=DataScope(datasets=["bgl"]),
            columns=["alert_tag", "entries"],
            total_matches=31_468,
            truncation_note=own_note,
        )

    result = anyio.run(lambda: server.call_tool("grouping", {"role": "team_lead"}))
    assert isinstance(result, CallToolResult)
    notes = envelope(result)["notes"]

    assert notes[0] == own_note
    assert not any("sample" in note for note in notes)
    assert not any("use count_by" in note for note in notes)


def test_a_truncation_note_is_dropped_when_nothing_was_truncated(server: MCPServer) -> None:
    # The note describes truncation, so a complete result must not carry it.
    scratch = MCPServer(name="t")

    @evidence_tool(scratch, title="Complete result")
    def complete(role: RoleParam) -> ToolOutcome:
        """Return everything. Inputs: role. Outputs: the envelope."""
        return ToolOutcome(
            result=FakeQueryResult(),
            scope=DataScope(datasets=["bgl"]),
            columns=["alert_tag", "entries"],
            truncation_note="Showing the top 3 of 3 groups.",
        )

    result = anyio.run(lambda: scratch.call_tool("complete", {"role": "team_lead"}))
    assert isinstance(result, CallToolResult)

    assert envelope(result)["truncated"] is False
    assert envelope(result)["notes"] == []


def test_notes_from_the_database_layer_are_carried_through() -> None:
    server = MCPServer(name="t")
    note = "SereneDB restarted; the statement was retried once."

    @evidence_tool(server, title="Retried tool")
    def retried(role: RoleParam) -> ToolOutcome:
        """A query that was retried. Inputs: role. Outputs: the envelope."""
        return sample_outcome(notes=[note])

    data = anyio.run(lambda: server.call_tool("retried", {"role": "engineer"}))
    assert isinstance(data, CallToolResult)

    assert note in envelope(data)["notes"]


def test_the_role_reaches_the_tool_body_as_the_enum() -> None:
    server = MCPServer(name="t")
    seen: list[Role] = []

    @evidence_tool(server, title="Role observer")
    def observer(role: RoleParam) -> ToolOutcome:
        """Record the Role. Inputs: role. Outputs: the envelope."""
        seen.append(role)
        return sample_outcome()

    anyio.run(lambda: server.call_tool("observer", {"role": "manager"}))

    assert seen == [Role.MANAGER]


def test_a_tool_without_a_docstring_is_rejected() -> None:
    server = MCPServer(name="t")

    with pytest.raises(ToolContractError, match="docstring"):

        @evidence_tool(server, title="No description")
        def undocumented(role: RoleParam) -> ToolOutcome:
            return sample_outcome()


def test_a_tool_may_count_a_different_unit_than_the_database_layer() -> None:
    # count_by counts groups, not Log Entries, and carries the entry count in the summary.
    server = MCPServer(name="t")

    @evidence_tool(server, title="Counting groups")
    def counting(role: RoleParam) -> ToolOutcome:
        """Count groups. Inputs: role. Outputs: the envelope."""
        outcome = sample_outcome()
        return ToolOutcome(
            result=FakeQueryResult(total=348_460, rows=[list(row) for row in SAMPLE_ROWS]),
            scope=outcome.scope,
            columns=outcome.columns,
            summary={"entries_total": 348_460, "groups_total": 31_468},
            total_matches=31_468,
        )

    result = anyio.run(lambda: server.call_tool("counting", {"role": "team_lead"}))
    assert isinstance(result, CallToolResult)
    data = envelope(result)

    assert data["total_matches"] == 31_468
    assert data["returned"] == 3
    assert data["truncated"] is True
    assert data["summary"]["entries_total"] == 348_460
