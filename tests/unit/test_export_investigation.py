"""`export_investigation(role, title, answers, evidence_ids?)`: the report as Markdown, read-only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from export_fixture import TITLE, answers, records
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult

from serenedb_log_mcp import config
from serenedb_log_mcp.config import load_settings, set_settings
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.export import render_investigation
from serenedb_log_mcp.ledger import reset_ledger
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META


@pytest.fixture
def fixture_ledger(ledger_path: Path) -> Path:
    """The fixture investigation's records at LEDGER_PATH."""
    ledger_path.write_text("".join(json.dumps(r) + "\n" for r in records()), encoding="utf-8")
    reset_ledger()
    return ledger_path


def call(**arguments: Any) -> CallToolResult:
    server = create_server()
    result = anyio.run(lambda: server.call_tool("export_investigation", arguments))
    assert isinstance(result, CallToolResult)
    return result


def answer_args() -> list[dict[str, Any]]:
    return [a.model_dump(mode="json") for a in answers()]


def test_it_is_registered_read_only_with_a_description_of_the_audience() -> None:
    tools = {tool.name: tool for tool in anyio.run(create_server().list_tools)}

    tool = tools["export_investigation"]
    assert tool.title
    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert sorted(tool.input_schema["required"]) == ["answers", "role", "title"]
    description = tool.description or ""
    for phrase in ("audience", "manager", "first 10 rows", "role='engineer'", "Outputs:", "writes no file"):
        assert phrase in description


def test_it_returns_the_rendered_markdown_and_its_size(fixture_ledger: Path) -> None:
    result = call(role="engineer", title=TITLE, answers=answer_args())

    assert not result.is_error, result.content
    out = result.structured_content
    assert out is not None
    # The product server offers list_incidents and top_patterns (T27, T28), so the side-by-side check runs.
    expected = render_investigation(Role.ENGINEER, TITLE, answers(), records(), incident_tools=True)
    assert out["markdown"] == expected.markdown
    assert out["size_chars"] == len(expected.markdown)
    assert out["size_bytes"] == len(expected.markdown.encode("utf-8"))
    assert out["evidence_ids"] == ["E1", "E2", "E3", "E4", "E5", "E6"]
    assert out["role"] == "engineer"


def test_the_manager_audience_gets_no_raw_log_entries(fixture_ledger: Path) -> None:
    out = call(role="manager", title=TITLE, answers=answer_args()).structured_content

    assert out is not None
    assert "synthetic" not in out["markdown"]
    assert "Rows withheld for the manager audience" in out["markdown"]


def test_evidence_ids_restrict_the_report(fixture_ledger: Path) -> None:
    out = call(role="engineer", title=TITLE, answers=answer_args(), evidence_ids=["E1"]).structured_content

    assert out is not None
    assert "E6" not in out["evidence_ids"]


def test_it_writes_nothing(fixture_ledger: Path, tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    set_settings(config.get_settings().model_copy(update={"export_dir": exports}))
    before = fixture_ledger.read_bytes()
    files = sorted(tmp_path.rglob("*"))

    call(role="engineer", title=TITLE, answers=answer_args())

    assert fixture_ledger.read_bytes() == before
    assert sorted(tmp_path.rglob("*")) == files
    assert not exports.exists()


def test_an_empty_ledger_renders_the_answers_with_notes() -> None:
    set_settings(load_settings({}))
    out = call(role="team_lead", title=TITLE, answers=answer_args()).structured_content

    assert out is not None
    assert out["evidence_ids"] == []
    assert any("not in the Evidence Ledger" in note for note in out["notes"])


def test_an_answer_with_an_unknown_role_is_a_tool_error() -> None:
    with pytest.raises(ToolError, match=r"answers\.0\.role"):
        call(role="engineer", title=TITLE, answers=[{"role": "cfo", "question": "q", "answer": "a"}])
