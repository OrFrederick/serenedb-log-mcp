"""Deterministic contract tests: NO LLM. Run on every PR (needs a SereneDB with the Base Corpus).

1. Tool contract: every tool returns Evidence with the required keys and appends to the ledger.
2. Golden parity: the tool result equals the golden SQL run directly.
3. Safety/limits: injection is inert, limits clamp, truncated flag is right.
4. Checker self-tests: synthetic good/bad answers prove each assertion fires.
"""
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import assertions as A  # noqa: E402


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    import importlib
    import stub_server
    importlib.reload(stub_server)
    return stub_server


REQ_KEYS = {"evidence_id", "tool", "params", "data_scope", "sql", "total_count", "returned_count", "truncated", "query_ms", "rows"}


def test_every_tool_returns_evidence_and_ledger(srv):
    evs = [srv.describe_corpus(), srv.search_logs("BGL", "ciod", limit=3), srv.get_context("BGL", 100, 2, 2),
           srv.count_by("OpenStack", "level"), srv.trend("OpenStack", "hour", level="ERROR")]
    for i, e in enumerate(evs, 1):
        assert REQ_KEYS <= e.keys()
        assert e["evidence_id"] == f"E{i}"
        assert e["sql"].upper().startswith("SELECT")
    ledger = A.load_ledger(os.environ["LEDGER_PATH"])
    assert [l["evidence_id"] for l in ledger] == ["E1", "E2", "E3", "E4", "E5"]


PARITY = [
    (lambda s: s.search_logs("BGL", "ciod", start="2005-06-01", end="2005-07-01")["total_count"],
     "SELECT count(*) FROM bgl WHERE message ILIKE '%ciod%' AND ts >= '2005-06-01' AND ts < '2005-07-01'"),
    (lambda s: s.count_by("OpenStack", "level")["total_count"], "SELECT count(*) FROM openstack"),
    (lambda s: {r["grp"]: r["n"] for r in s.count_by("BGL", "component", level="FATAL")["rows"]}["KERNEL"],
     "SELECT count(*) FROM bgl WHERE level='FATAL' AND component='KERNEL'"),
    (lambda s: s.trend("BGL", "month", level="FATAL")["rows"][0]["n"],
     "SELECT count(*) FROM bgl WHERE level='FATAL' AND ts < '2005-07-01'"),
    (lambda s: s.count_by("BGL", "node", level="FATAL")["groups_total"],
     "SELECT count(DISTINCT node) FROM bgl WHERE level='FATAL'"),
]


@pytest.mark.parametrize("call,golden", PARITY)
def test_golden_parity(srv, call, golden):
    assert call(srv) == A.golden_value(golden)


def test_injection_is_inert(srv):
    e = srv.search_logs("BGL", "'; DROP TABLE bgl; --")
    assert e["total_count"] == 0
    assert A.golden_value("SELECT count(*) FROM bgl") == 4747963
    with pytest.raises(ValueError):
        srv.count_by("BGL", "message; DROP TABLE bgl")


def test_limits_and_truncation(srv):
    e = srv.search_logs("BGL", "ciod", limit=10_000)
    assert e["returned_count"] == 50 and e["truncated"] is True and e["total_count"] > 50
    e = srv.count_by("OpenStack", "level", top=50)
    assert e["truncated"] is False


def test_out_of_range_dates_return_zero(srv):
    assert srv.count_by("BGL", "component", level="FATAL", start="2007-03-01", end="2007-04-01")["total_count"] == 0


def test_question_file_is_valid():
    qs = yaml.safe_load(open(HERE / "questions.yaml"))["questions"]
    tools = {"describe_corpus", "search_logs", "get_context", "count_by", "trend"}
    assert len({q["id"] for q in qs}) == len(qs)
    for q in qs:
        assert q["role"] in {"Engineer", "Team Lead", "Manager"}
        e = q["expect"]
        for t in e.get("required_tools", []) + e.get("forbidden_tools", []) + sum(e.get("any_of_tools", []), []):
            assert t in tools, (q["id"], t)
        for g in e.get("golden", []):
            assert A.golden_value(g["sql"]) is not None, (q["id"], g["name"])


# ---------------- checker self-tests (the tests of the tests) ----------------
def _ledger(tmp_path, entries):
    p = tmp_path / "l.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries))
    return str(p)


GOOD = """## Observed Facts
- BGL has 855,195 FATAL Log Entries [E1]; KERNEL emitted 626,450 of them [E1], i.e. 73.3% [E1].

## Hypotheses
- The KERNEL share may reflect a few noisy nodes.

## Unknowns
- Financial cost and downtime cannot be established from the logs.

## Next Step
- Investigate KERNEL first.
"""
Q = {"id": "mgr-x", "role": "Manager", "question": "Which Component?",
     "expect": {"required_tools": ["count_by"], "args": [{"tool": "count_by", "field": "level", "op": "equals", "value": "FATAL"}],
                "golden": [{"name": "k", "must_appear": True, "sql": "x"}], "unknown_regex": ["(?i)cost"]}}
LEDGER = [{"evidence_id": "E1", "sql": "SELECT", "total_count": 855195, "truncated": False,
           "rows": [{"grp": "KERNEL", "n": 626450}, {"grp": "APP", "n": 228536}]}]


def _run(tmp_path, answer, calls=None):
    return {"answer": answer, "ledger": _ledger(tmp_path, LEDGER),
            "calls": calls or [{"name": "mcp__logs__count_by", "input": {"level": "FATAL"}}]}


def _by(res):
    return {r.name: r.passed for r in res}


def test_checker_good_answer_passes(tmp_path):
    r = _by(A.grade(_run(tmp_path, GOOD), Q, {"k": 626450}))
    assert all(v in (True, None) for v in r.values()), r


@pytest.mark.parametrize("mutation,check", [
    (lambda a: a.replace("626,450", "626,451"), "numbers.grounded_in_ledger"),
    (lambda a: a.replace("626,450", "626,451"), "numbers.golden_in_answer"),
    (lambda a: a.replace("[E1], i.e.", "[E7], i.e."), "citations.resolve"),
    (lambda a: a.replace("## Unknowns", "## Gaps"), "structure.sections"),
    (lambda a: a.replace("626,450 of them [E1], i.e. 73.3% [E1]", "626,450 of them"), "citations.numbers_near_citation"),
    (lambda a: a.replace("Investigate KERNEL first.", "This outage cost us $40,000 in revenue."), "manager.no_invented_impact"),
    (lambda a: a.replace("Investigate KERNEL first.", "Expect about 6 hours of downtime for customers."), "manager.no_invented_impact"),
    (lambda a: a.replace("Financial cost and", ""), "unknowns.flagged"),
    (lambda a: a.replace("FATAL Log Entries", "incidents"), "glossary.no_entry_count_as_incidents"),
])
def test_checker_catches_bad_answer(tmp_path, mutation, check):
    r = _by(A.grade(_run(tmp_path, mutation(GOOD)), Q, {"k": 626450}))
    assert r[check] is False, r


def test_checker_tool_selection(tmp_path):
    r = _by(A.grade(_run(tmp_path, GOOD, calls=[{"name": "mcp__logs__search_logs", "input": {"level": "FATAL"}}]), Q, {"k": 626450}))
    assert r["tools.required"] is False and r["tools.args"] is False


def test_checker_allows_deferral_and_years_and_derived(tmp_path):
    ans = GOOD.replace("Investigate KERNEL first.",
                       "Investigate KERNEL first. Ask the finance owner for cost or SLA records.") \
              .replace("i.e. 73.3% [E1].", "i.e. 73.3% [E1]. KERNEL plus APP is 854,986 [E1] in June 2005.")
    r = _by(A.grade(_run(tmp_path, ans), Q, {"k": 626450}))
    assert r["manager.no_invented_impact"] is True
    assert r["numbers.grounded_in_ledger"] is False      # 854,986 is a derived sum, not in Evidence
    assert r["numbers.grounded_or_derived"] is True      # ... but is KERNEL+APP


def test_ledger_has_tool_extras(srv):
    srv.count_by("BGL", "node", level="FATAL", top=3)
    e = A.load_ledger(os.environ["LEDGER_PATH"])[-1]
    assert e["groups_total"] > 3 and e["truncated"] is True
