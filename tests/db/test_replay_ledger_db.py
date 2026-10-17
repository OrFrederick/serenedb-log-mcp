"""The ledger replay against SereneDB (T13): record a real Evidence Ledger on the canonical corpus,
replay it with `scripts/replay_ledger.py`, then tamper with one number and watch the replay fail.

The tools run through an in-process MCP client against SLM_SCHEMA `slm`, read-only, and the ledger
lives in a temporary file. The test skips when `slm` holds no derived corpus.

Time-filtered `search_logs` records replay only with the casts of #71 (PR #76): without them the
stored ISO-string bound reaches `ts_ge(%s)` as VARCHAR. The records here therefore use count_by and
trend with a time range (bound as stored, cast by their templates) and searches without one; the
test for a time-filtered search turns itself on when the cast is in `fts`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from aggregation_tools import call
from psycopg import sql

import replay_ledger
from serenedb_log_mcp import config, fts, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts
from serenedb_log_mcp.ledger import Ledger

SCHEMA = "slm"
CAST_LANDED = "%s::" in fts.filter_at_or_after(sql.Identifier("ts"), datetime(2005, 6, 1))[0].as_string(None)


@pytest.fixture(scope="module")
def canonical(database_url: str) -> str:
    """The canonical schema, or a skip when it holds no derived corpus."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        found = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name IN ('bgl_entry', 'os_entry')",
            [SCHEMA],
        ).fetchone()
    if not found or found[0] != 2:
        pytest.skip(f"schema {SCHEMA} holds no derived corpus (bgl_entry, os_entry); run make derive")
    return SCHEMA


@pytest.fixture
def ledger_path(canonical: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """LEDGER_PATH in a temporary file, SLM_SCHEMA the canonical corpus; restored afterwards."""
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=canonical, ledger_path=path))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    yield path
    facts.clear_cache()


def records(path: Path) -> list[dict[str, Any]]:
    return Ledger(path).all()


def by_tool(path: Path, tool: str, **arguments: Any) -> dict[str, Any]:
    return next(
        r
        for r in records(path)
        if r["tool"] == tool and all(r["arguments"].get(k) == v for k, v in arguments.items())
    )


def write_answers(path: Path, answer: str) -> Path:
    answers = path.parent / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "title": "data TLB errors",
                "answers": [{"role": "engineer", "question": "What failed?", "answer": answer}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return answers


def answer_for(path: Path) -> str:
    search = by_tool(path, "search_logs", sort="time")
    corpus = by_tool(path, "count_by", group_by="dataset")
    return (
        "## Observed Facts\n"
        f'- The words "data TLB error" match {search["total_matches"]:,} BGL Log Entries '
        f"[{search['evidence_id']}].\n"
        f"- The Base Corpus holds {corpus['summary']['total']:,} Log Entries [{corpus['evidence_id']}].\n"
        "## Hypotheses\n- One node may write most of them.\n"
        "## Unknowns\n- Whether a job failed: these logs hold no job records.\n"
        "## Next Step\n- Run get_context on one of the matches.\n"
    )


@pytest.fixture
def recorded(ledger_path: Path) -> Path:
    """A ledger of every kind of record the replay must handle, recorded through the MCP client."""
    call("describe_corpus", role="engineer", dataset="openstack")
    call("count_by", role="team_lead", group_by="dataset")
    call(
        "trend",
        role="team_lead",
        dataset="bgl",
        bucket="day",
        alert_categories=["KERNDTLB"],
        time_from="2005-06-10",
        time_to="2005-06-13",
    )
    found = call("search_logs", role="engineer", dataset="bgl", query="data TLB error", limit=5)
    call("search_logs", role="engineer", dataset="bgl", query="data TLB error", sort="relevance", limit=5)
    call("get_context", role="engineer", dataset="bgl", entry_id=found["rows"][0][0], before=3, after=3)
    assert [r["tool"] for r in records(ledger_path)] == [
        "describe_corpus",
        "count_by",
        "trend",
        "search_logs",
        "search_logs",
        "get_context",
    ]
    return ledger_path


def test_a_recorded_ledger_and_its_answer_replay(recorded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = recorded.read_bytes()
    answers = write_answers(recorded, answer_for(recorded))

    code = replay_ledger.main([str(recorded), "--answers", str(answers)])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "Replay passed: 6 records and 1 answers hold." in out
    assert "E5 search_logs: ok" in out
    assert "rows not compared (sorted by relevance" in out
    assert "E1 describe_corpus: ok, 2 of 2 statements re-run as stored" in out
    assert "E3 trend: ok" in out
    assert recorded.read_bytes() == before  # the replay wrote nothing to the ledger


def test_a_tampered_number_in_the_answer_fails_the_replay(
    recorded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    search = by_tool(recorded, "search_logs", sort="time")
    total = search["total_matches"]
    tampered = answer_for(recorded).replace(f"{total:,}", f"{total + 1:,}")
    assert tampered != answer_for(recorded)

    code = replay_ledger.main([str(recorded), "--answers", str(write_answers(recorded, tampered))])

    out = capsys.readouterr().out
    assert code == 1, out
    assert "number_not_in_evidence" in out


def test_a_tampered_stored_total_fails_the_replay(recorded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lines = recorded.read_text(encoding="utf-8").splitlines()
    rewritten = []
    for line in lines:
        record = json.loads(line)
        if record["tool"] == "count_by":
            record["summary"]["total"] += 1
            record["total_matches"] += 1
        rewritten.append(json.dumps(record))
    recorded.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    code = replay_ledger.main([str(recorded)])

    out = capsys.readouterr().out
    assert code == 1, out
    assert "E2 count_by: MISMATCH" in out
    assert "total_changed: total_matches recorded" in out
    assert "summary_changed: summary.total" in out
    assert "E4 search_logs: ok" in out


@pytest.mark.skipif(
    not CAST_LANDED,
    reason="needs the #71 cast in fts.filter_at_or_after/filter_before (PR #76); without it a stored "
    "time-filtered search_logs statement binds its ISO-string bound as VARCHAR and cannot replay",
)
def test_a_time_filtered_search_replays_as_stored(
    ledger_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    call(
        "search_logs",
        role="engineer",
        dataset="bgl",
        query="ciod failed",
        # Wide enough to match in the Loghub 2k sample as well as in the full Base Corpus: with no
        # match the facet statements do not run, and the replay would check one statement, not three.
        time_from="2005-06-01",
        time_to="2006-01-01",
        limit=5,
    )

    code = replay_ledger.main([str(ledger_path)])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "E1 search_logs: ok, 3 of 3 statements re-run as stored" in out
