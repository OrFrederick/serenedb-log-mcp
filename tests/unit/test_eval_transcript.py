"""Parsing one `claude -p` stream-json run into an eval trial (T17)."""

import json
from pathlib import Path

from eval_trials import init_event, make_trial, result_event, run_events, stream_lines, tool_result, tool_use

from transcript import Trial, parse_stream, read_ledger, short_name


def test_parse_collects_calls_results_and_the_last_answer() -> None:
    events = run_events([("count_by", {"role": "team_lead", "group_by": "level"})], "## Observed Facts\n...")

    stream = parse_stream(stream_lines(events))

    assert stream.server_status() == "connected"
    assert [c.short for c in stream.calls] == ["count_by"]
    assert stream.calls[0].input == {"role": "team_lead", "group_by": "level"}
    assert stream.results[0].tool_use_id == "toolu_0"
    assert stream.results[0].text == '{"evidence_id": "E1"}'
    assert stream.answer.startswith("## Observed Facts")
    assert stream.result_events == 2
    assert stream.cost_usd == 0.01  # the last result: Claude Code reports it per session
    assert "mcp__serenedb-log__trend" in stream.tools


def test_an_error_result_with_subtype_success_is_an_error() -> None:
    # #3: an authentication failure came back as subtype "success" with is_error true.
    stream = parse_stream(stream_lines([init_event(), result_event("Invalid API key", is_error=True)]))

    assert stream.is_error
    assert "Invalid API key" in stream.error_text


def test_malformed_lines_are_counted_not_raised() -> None:
    stream = parse_stream(["not json", "[1, 2]", "", json.dumps(result_event("ok"))])

    assert stream.bad_lines == 2
    assert stream.answer == "ok"


def test_tool_result_content_as_plain_string() -> None:
    event = tool_result("toolu_9", "x")
    event["message"]["content"][0]["content"] = "plain text"

    stream = parse_stream(stream_lines([tool_use("toolu_9", "trend", {}), event]))

    assert stream.results[0].text == "plain text"


def test_run_error_names_the_infra_failure() -> None:
    ok = make_trial("eng-01", "engineer", [], "answer")
    failed_server = make_trial("eng-01", "engineer", [], "answer", status="failed")
    no_result = Trial(
        question_id="eng-01",
        role="engineer",
        model="claude-haiku-5-5",
        repeat=0,
        stream=parse_stream([]),
        ledger_path="x",
        wall_s=0.1,
        exit_code=1,
        stderr_tail="boom",
    )

    assert ok.run_error() is None
    assert "status 'failed'" in (failed_server.run_error() or "")
    assert "no result event (exit code 1)" in (no_result.run_error() or "")


def test_a_trial_survives_a_json_round_trip() -> None:
    trial = make_trial("tl-01", "team_lead", [("trend", {"role": "team_lead", "bucket": "week"})], "answer")

    again = Trial.from_json(json.loads(json.dumps(trial.to_json())))

    assert again == trial


def test_read_ledger_skips_a_torn_last_line(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"evidence_id": "E1"}\n{"evidence_id": "E2"}\n{"evid')

    assert [r["evidence_id"] for r in read_ledger(path)] == ["E1", "E2"]
    assert read_ledger(tmp_path / "missing.jsonl") == []


def test_short_name_strips_only_our_prefix() -> None:
    assert short_name("mcp__serenedb-log__count_by") == "count_by"
    assert short_name("Bash") == "Bash"
