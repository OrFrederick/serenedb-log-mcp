"""`create_index` and `drop_index` as MCP tools (T19): registration, inputs and the Evidence shape.

The database work is replaced by fakes of `index_admin`; tests/db/test_agent_index_db.py runs it for
real.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult

from serenedb_log_mcp.corpus.agent_indexes import AgentIndex
from serenedb_log_mcp.corpus.facts import DatasetFacts
from serenedb_log_mcp.db import QueryResult
from serenedb_log_mcp.index_admin import IndexReport
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META
from serenedb_log_mcp.tools import agent_index

FIRST, LAST = datetime(2005, 6, 3, 15, 42, 50, 363779), datetime(2006, 1, 4, 8, 0, 5, 233639)
START, END = datetime(2005, 8, 22), datetime(2005, 9, 19)
WAVE = AgentIndex("slm", "agent_idx_bgl_0a1b2c3d", "bgl", START, END, 310_868)
STATEMENTS = QueryResult(
    0, [], False, 512.5, ["SELECT 1", 'CREATE INDEX "agent_idx_bgl_0a1b2c3d" …'], [[], []]
)


def tools() -> dict[str, Any]:
    return {tool.name: tool for tool in anyio.run(create_server().list_tools)}


def call(tool: str, **arguments: Any) -> CallToolResult:
    result = anyio.run(lambda: create_server().call_tool(tool, arguments))
    assert isinstance(result, CallToolResult)
    return result


@pytest.fixture
def fake_admin(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Fakes for the facts and index_admin; returns the calls they received."""
    calls: list[tuple[str, Any]] = []
    facts = DatasetFacts("bgl", 4_747_963, FIRST, LAST, {}, {}, {})
    monkeypatch.setattr(agent_index, "dataset_facts", lambda dataset, role: facts)

    def create(dataset: str, start: datetime, end: datetime) -> IndexReport:
        calls.append(("create", (dataset, start, end)))
        index = AgentIndex("slm", WAVE.name, dataset, start, end, WAVE.rows)
        return IndexReport(index, "built", STATEMENTS, create_ms=400.25, refresh_ms=100.5)

    def drop(name: str) -> IndexReport:
        calls.append(("drop", name))
        return IndexReport(WAVE, "dropped", STATEMENTS, drop_ms=35.0)

    monkeypatch.setattr(agent_index, "create_agent_index", create)
    monkeypatch.setattr(agent_index, "drop_agent_index", drop)
    return calls


# ----------------------------------------------------------------- registration


def test_create_index_is_registered_as_the_one_building_tool() -> None:
    tool = tools()["create_index"]

    assert tool.title
    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False
    assert sorted(tool.input_schema["required"]) == ["dataset", "role", "time_from"]
    assert "Outputs:" in tool.description
    assert "drop_index" in tool.description


def test_drop_index_is_registered_as_destructive() -> None:
    tool = tools()["drop_index"]

    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True
    assert sorted(tool.input_schema["required"]) == ["name", "role"]
    assert "Outputs:" in tool.description
    assert "create_index" in tool.description


def test_the_index_tools_are_the_only_ones_that_are_not_read_only() -> None:
    writable = sorted(name for name, tool in tools().items() if tool.annotations.read_only_hint is not True)

    assert writable == ["create_index", "drop_index"]


# ----------------------------------------------------------------- create_index


def test_create_index_returns_the_name_rows_and_build_time(fake_admin: list[tuple[str, Any]]) -> None:
    result = call(
        "create_index", role="engineer", dataset="bgl", time_from="2005-08-22", time_to="2005-09-19"
    )

    assert result.is_error is False
    evidence = result.structured_content
    assert evidence is not None
    assert fake_admin == [("create", ("bgl", START, END))]
    row = dict(zip(evidence["columns"], evidence["rows"][0], strict=True))
    assert row == {
        "index": "agent_idx_bgl_0a1b2c3d",
        "dataset": "bgl",
        "time_from": "2005-08-22T00:00:00",
        "time_to": "2005-09-19T00:00:00",
        "rows": 310_868,
        "build_ms": 500.8,
        "create_ms": 400.2,
        "refresh_ms": 100.5,
        "status": "built",
    }
    assert evidence["summary"]["build_ms"] == 500.8
    assert evidence["summary"]["index"] == "agent_idx_bgl_0a1b2c3d"
    assert evidence["summary"]["qualified_name"] == "slm.agent_idx_bgl_0a1b2c3d"
    assert evidence["summary"]["max_rows"] == 1_000_000
    assert evidence["total_matches"] == 1
    assert evidence["truncated"] is False
    assert evidence["data_scope"]["datasets"] == ["bgl"]
    assert evidence["data_scope"]["time_from"] == "2005-08-22T00:00:00"
    assert evidence["data_scope"]["time_to"] == "2005-09-19T00:00:00"
    assert evidence["sql"] == STATEMENTS.sql
    assert any("drop_index(name='agent_idx_bgl_0a1b2c3d')" in note for note in evidence["notes"])


def test_without_time_to_the_slice_ends_after_the_last_log_entry(fake_admin: list[tuple[str, Any]]) -> None:
    result = call("create_index", role="team_lead", dataset="bgl", time_from="2005-12-01")

    assert result.is_error is False
    # The last BGL Log Entry is 2006-01-04 08:00:05 US Pacific time, 16:00:05 UTC: slices are UTC (#74).
    assert fake_admin == [("create", ("bgl", datetime(2005, 12, 1), datetime(2006, 1, 4, 16, 0, 6)))]


def test_a_relative_slice_resolves_against_the_corpus_end(fake_admin: list[tuple[str, Any]]) -> None:
    result = call("create_index", role="engineer", dataset="bgl", time_from="last 7d")

    assert result.is_error is False
    end = datetime(2006, 1, 4, 16, 0, 6)  # the UTC corpus end
    assert fake_admin == [("create", ("bgl", datetime(2005, 12, 28, 16, 0, 6), end))]
    assert result.structured_content is not None
    assert result.structured_content["data_scope"]["time_input"] == "last 7d"


def test_a_bad_time_is_an_actionable_error(fake_admin: list[tuple[str, Any]]) -> None:
    with pytest.raises(ToolError, match="ISO-8601"):
        call("create_index", role="engineer", dataset="bgl", time_from="yesterday")

    assert fake_admin == []


# ----------------------------------------------------------------- drop_index


def test_drop_index_reports_what_it_removed(fake_admin: list[tuple[str, Any]]) -> None:
    result = call("drop_index", role="engineer", name="agent_idx_bgl_0a1b2c3d")

    assert result.is_error is False
    evidence = result.structured_content
    assert evidence is not None
    assert fake_admin == [("drop", "agent_idx_bgl_0a1b2c3d")]
    row = dict(zip(evidence["columns"], evidence["rows"][0], strict=True))
    assert row["index"] == "agent_idx_bgl_0a1b2c3d"
    assert row["status"] == "dropped"
    assert row["drop_ms"] == 35.0
    assert evidence["data_scope"]["time_from"] == "2005-08-22T00:00:00"
    assert any("full index" in note for note in evidence["notes"])
