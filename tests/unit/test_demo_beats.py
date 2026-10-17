"""The demo's beats (T24): `docs/demo/beats.yaml` against the run sheet and the server, and
`serenedb_log_mcp.demo` on its own."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.demo import (
    ENTRY_ID,
    Beat,
    BeatsFileError,
    Call,
    DemoBeats,
    load_beats,
    match_beats,
    resolve_arguments,
)
from serenedb_log_mcp.server import create_server

ROOT = Path(__file__).parents[2]
BEATS = ROOT / "docs" / "demo" / "beats.yaml"
SCRIPT = ROOT / "docs" / "demo" / "script.md"


@pytest.fixture(scope="module")
def beats() -> DemoBeats:
    return load_beats(BEATS)


def test_the_beats_fit_the_budget_of_5_minutes(beats: DemoBeats) -> None:
    assert beats.budget_s == 300
    assert beats.planned_s <= beats.budget_s


def test_the_beats_keep_the_decided_order(beats: DemoBeats) -> None:
    titles = [beat.title for beat in beats.beats]

    reveal, verification, score, refusal = (
        titles.index("Lines versus Incidents"),
        titles.index("Live self-verification"),
        titles.index("The ground-truth score"),
        titles.index("The refusal"),
    )
    assert reveal < verification < score < refusal


def test_every_prompt_is_in_the_run_sheet_verbatim(beats: DemoBeats) -> None:
    script = " ".join(SCRIPT.read_text(encoding="utf-8").split())

    for beat in beats.beats:
        assert beat.prompt in script, beat.title


def test_every_call_names_a_tool_and_parameters_the_server_offers(beats: DemoBeats) -> None:
    tools = {tool.name: tool for tool in anyio.run(create_server().list_tools)}

    for beat in beats.beats:
        for call in beat.calls:
            assert call.tool in tools, call.tool
            parameters = set(tools[call.tool].input_schema["properties"])
            assert set(call.arguments) <= parameters - {"role"}, (call.tool, set(call.arguments) - parameters)


def test_the_beats_use_both_datasets_full_text_and_the_whole_base_corpus(beats: DemoBeats) -> None:
    calls = [call for beat in beats.beats for call in beat.calls]

    assert any(c.tool == "search_logs" and c.arguments.get("query") for c in calls)
    assert any(c.tool == "count_by" and c.arguments == {"group_by": "dataset"} for c in calls)
    assert {c.arguments.get("dataset") for c in calls} >= {"bgl", "openstack"}
    assert {beat.role for beat in beats.beats if beat.calls} == set(Role)


def test_the_placeholder_follows_a_call_in_the_same_beat(beats: DemoBeats) -> None:
    for beat in beats.beats:
        for index, call in enumerate(beat.calls):
            if ENTRY_ID in call.arguments.values():
                assert index > 0, beat.title


# serenedb_log_mcp.demo -------------------------------------------------------------------------------


def _record(evidence_id: str, tool: str, role: str, **arguments: Any) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "tool": tool, "role": role, "arguments": arguments}


def _beats(*beats: Beat) -> DemoBeats:
    return DemoBeats(title="t", budget_s=300, beats=list(beats))


SEARCH = Call(tool="search_logs", arguments={"dataset": "bgl", "query": "x"})
CONTEXT = Call(tool="get_context", arguments={"dataset": "bgl", "entry_id": ENTRY_ID})


def test_the_placeholder_takes_the_first_entry_id_of_the_previous_result() -> None:
    previous = {"columns": ["ts", "entry_id"], "rows": [["2005", 104], ["2005", 105]]}

    assert resolve_arguments(CONTEXT, previous) == {"dataset": "bgl", "entry_id": 104}
    assert resolve_arguments(SEARCH, None) == {"dataset": "bgl", "query": "x"}


@pytest.mark.parametrize(
    "previous", [None, {"columns": ["entry_id"], "rows": []}, {"columns": ["n"], "rows": [[1]]}]
)
def test_the_placeholder_without_a_first_entry_id_is_an_error(previous: dict[str, Any] | None) -> None:
    with pytest.raises(ValueError, match="entry_id"):
        resolve_arguments(CONTEXT, previous)


def test_records_match_the_calls_in_order_by_tool_role_and_named_arguments() -> None:
    beat = Beat(title="b", role=Role.ENGINEER, seconds=10, prompt="p", expected="e", calls=[SEARCH, CONTEXT])
    records = [
        _record("E1", "search_logs", "manager", dataset="bgl", query="x", limit=20),
        _record("E2", "search_logs", "engineer", dataset="bgl", query="x", limit=20),
        _record("E3", "get_context", "engineer", dataset="bgl", entry_id=104, before=10),
        _record("E4", "count_by", "engineer", group_by="dataset"),
    ]

    matches, leftover = match_beats(_beats(beat), records)

    assert [r["evidence_id"] for r in matches[0].records] == ["E2", "E3"]
    assert matches[0].missing == []
    assert [r["evidence_id"] for r in leftover] == ["E1", "E4"]


def test_a_call_without_a_record_of_its_tool_and_role_is_missing() -> None:
    beat = Beat(title="b", role=Role.TEAM_LEAD, seconds=10, prompt="p", expected="e", calls=[SEARCH])

    matches, leftover = match_beats(_beats(beat), [_record("E1", "search_logs", "engineer", query="x")])

    assert matches[0].records == []
    assert matches[0].missing == [SEARCH]
    assert len(leftover) == 1


def test_other_arguments_match_loosely_after_every_exact_match() -> None:
    first = Beat(title="a", role=Role.ENGINEER, seconds=10, prompt="p", expected="e", calls=[SEARCH])
    second = Beat(
        title="b",
        role=Role.ENGINEER,
        seconds=10,
        prompt="p",
        expected="e",
        calls=[Call(tool="search_logs", arguments={"dataset": "bgl", "query": "z"})],
    )
    records = [
        _record("E1", "search_logs", "engineer", dataset="bgl", query="y", limit=10),
        _record("E2", "search_logs", "engineer", dataset="bgl", query="z", limit=10),
    ]

    matches, leftover = match_beats(_beats(first, second), records)

    assert [r["evidence_id"] for r in matches[0].records] == ["E1"]
    assert matches[0].loose == {"E1"}
    assert [r["evidence_id"] for r in matches[1].records] == ["E2"]
    assert matches[1].loose == set()
    assert leftover == []


def test_a_call_may_name_its_own_role() -> None:
    call = Call(tool="count_by", role=Role.MANAGER, arguments={"group_by": "team"})
    beat = Beat(title="b", role=Role.ENGINEER, seconds=10, prompt="p", expected="e", calls=[call])

    matches, _ = match_beats(_beats(beat), [_record("E1", "count_by", "manager", group_by="team", top=25)])

    assert len(matches[0].records) == 1


def test_a_beat_with_calls_needs_a_role() -> None:
    with pytest.raises(ValueError, match="without a Role"):
        Beat(title="b", seconds=10, prompt="p", expected="e", calls=[SEARCH])


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("title: [", "not valid YAML"),
        ("- a\n- b\n", "must be a mapping"),
        ("title: t\nbudget_s: 300\nbeats: []\n", "not a beats file"),
    ],
)
def test_an_unusable_beats_file_names_the_problem(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "beats.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(BeatsFileError, match=message):
        load_beats(path)


def test_a_missing_beats_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(BeatsFileError, match="cannot read"):
        load_beats(tmp_path / "nope.yaml")
