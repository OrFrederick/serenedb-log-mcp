"""The eval graders (T17): infra, L1 trajectory and L2 grounding, on synthetic trials (no model call).

The L2 answer is the correct Engineer answer of tests/unit/test_verify_checks.py over the shared
fixture ledger (tests/support/verify_ledger.py), so the grader and `verify_answer` are checked on the
same Evidence.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from eval_trials import make_trial
from verify_ledger import ledger_records

from graders import Grade, arg_matches, grade_trial, is_subsequence, jaccard, l1_checks, tool_signature
from questions import ArgPredicate, Question
from serenedb_log_mcp.toolkit import CONTRACT_BUG_HINT, RETRY_HINT
from transcript import ToolCall

LEDGER = ledger_records()

ANSWER = """\
## Observed Facts
- Node R30-M0-N9-C:J16-U01 logged `data TLB error interrupt` at FATAL level from 2005-06-11 17:32:07 [E2].
- The search matched 152,734 Log Entries in total [E2]; entry 222715 is the first one shown [E2].
- Just before it, at 17:32:05, the same node corrected an instruction cache parity error [E7].
- The host reported 64172 MB of total memory [E6], and one request returned status 503 after 0.0080531 s [E6].

## Hypotheses
- The parity error 2.15 s before the TLB error [E7] may point to failing memory on that node card.

## Unknowns
- Whether the node was replaced afterwards: the logs hold no hardware records.

## Next Step
- Run get_context on entry 222715 with before=25 and scope='window' to see what other nodes logged.
- Check the hardware history of node R30-M0-N9-C:J16-U01.
"""

ENGINEER_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_logs", {"role": "engineer", "dataset": "bgl", "query": '"data TLB error"'}),
    ("get_context", {"role": "engineer", "dataset": "bgl", "entry_id": 222715, "scope": "node"}),
]


def question(**expect: Any) -> Question:
    return Question.model_validate(
        {
            "id": "eng-01",
            "role": "engineer",
            "split": "tune",
            "question": "What failed on R30-M0-N9-C:J16-U01?",
            "expect": expect,
        }
    )


def checks(grade: Grade) -> dict[str, bool | None]:
    return {c.name: c.passed for c in grade.checks}


def test_a_good_trial_passes_every_check() -> None:
    q = question(
        must_call=["search_logs", "get_context"],
        order=["search_logs", "get_context"],
        must_not_call=["trend"],
        args=[{"tool": "search_logs", "arg": "query", "op": "icontains", "value": "TLB"}],
        golden=[{"name": "kerndtlb", "sql": "SELECT count(*) FROM {bgl} WHERE alert_tag = 'KERNDTLB'"}],
        mention=["(?i)parity"],
        forbid=["(?i)incidents"],
        unknowns=["(?i)replaced"],
    )
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER)

    grade = grade_trial(trial, q, LEDGER, {"kerndtlb": 152_734.0})

    assert grade.verdict == "pass", grade.failed()
    assert checks(grade)["l2.golden"] is True
    assert checks(grade)["l2.verify"] is True


def test_l1_catches_a_missing_a_forbidden_and_an_out_of_order_call() -> None:
    q = question(must_call=["get_context"], must_not_call=["trend"], order=["search_logs", "get_context"])
    calls = [
        ("get_context", {"role": "engineer", "entry_id": 1}),
        ("trend", {"role": "engineer"}),
        ("search_logs", {"role": "engineer"}),
    ]

    result = {c.name: c for c in l1_checks(make_trial("eng-01", "engineer", calls, ANSWER), q)}

    assert result["l1.must_call"].passed is True
    assert result["l1.must_not_call"].passed is False
    assert result["l1.order"].passed is False
    assert result["l1.any_of"].passed is None  # not asked for


def test_l1_any_of_needs_one_tool_of_each_group() -> None:
    q = question(any_of=[["count_by", "top_patterns"], ["get_context"]])
    trial = make_trial("eng-01", "engineer", [("count_by", {"role": "engineer"})], ANSWER)

    result = {c.name: c for c in l1_checks(trial, q)}

    assert result["l1.any_of"].passed is False
    assert "get_context" in result["l1.any_of"].detail


def test_l1_flags_a_foreign_tool_and_a_wrong_role() -> None:
    trial = make_trial("eng-01", "engineer", [("count_by", {"role": "manager"})], ANSWER)
    foreign = ToolCall(id="x", name="Bash", input={})
    trial = replace(trial, stream=replace(trial.stream, calls=[*trial.stream.calls, foreign]))

    result = {c.name: c for c in l1_checks(trial, question())}

    assert result["l1.only_our_tools"].passed is False
    assert result["l1.only_our_tools"].detail == "Bash"
    assert result["l1.role_arg"].passed is False


def test_l1_budget_counts_calls_turns_and_cost() -> None:
    q = question(budget={"max_calls": 1, "max_turns": 2, "max_cost_usd": 0.001})
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER)

    budget = {c.name: c for c in l1_checks(trial, q)}["l1.budget"]

    assert budget.passed is False
    assert "2 calls > 1" in budget.detail
    assert "3 turns > 2" in budget.detail
    assert "$0.0100 > $0.0010" in budget.detail


@pytest.mark.parametrize(
    ("value", "op", "want", "expected"),
    [
        ("BGL", "equals", "bgl", True),
        ("openstack", "equals", "bgl", False),
        ('"data TLB error"', "icontains", "tlb", True),
        (["FATAL", "ERROR"], "contains", "fatal", True),
        (["ERROR"], "contains", "FATAL", False),
        ("team", "in", ["component", "team"], True),
        (None, "present", None, False),
        ("2005-06-11", "present", None, True),
        (None, "absent", None, True),
        ([], "absent", None, True),
        ("bgl", "absent", None, False),
        (None, "equals", "bgl", False),
    ],
)
def test_argument_predicates(value: Any, op: str, want: Any, expected: bool) -> None:
    predicate = ArgPredicate(tool="count_by", arg="x", op=op, value=want)  # type: ignore[arg-type]

    assert arg_matches(value, predicate) is expected


def test_l1_args_reports_what_was_passed() -> None:
    q = question(args=[{"tool": "count_by", "arg": "group_by", "op": "equals", "value": "node"}])
    trial = make_trial("eng-01", "engineer", [("count_by", {"role": "engineer", "group_by": "rack"})], ANSWER)

    result = {c.name: c for c in l1_checks(trial, q)}["l1.args"]

    assert result.passed is False
    assert "count_by.group_by equals 'node'; got ['rack']" in result.detail


def test_an_infra_tool_error_makes_the_trial_infra_not_a_model_failure() -> None:
    error = f"Error executing tool count_by: the database did not answer. {RETRY_HINT}"
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER, errors={0: error})

    grade = grade_trial(trial, question(), LEDGER)

    assert checks(grade)["infra.tool_errors"] is False
    assert grade.verdict == "infra"


@pytest.mark.parametrize(
    "error",
    [
        f"Error executing tool count_by: broken envelope. {CONTRACT_BUG_HINT}",
        "Error executing tool count_by",
        "Error executing tool search_logs: The Evidence Ledger could not be read or written: line 3.",
        "Error executing tool trend: canceling statement due to statement timeout",
    ],
)
def test_infra_error_texts(error: str) -> None:
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER, errors={1: error})

    assert grade_trial(trial, question(), LEDGER).verdict == "infra"


def test_a_bad_argument_is_the_models_error_and_does_not_gate() -> None:
    error = "Error executing tool count_by: role='boss' is not a Role. Pass one of: engineer, manager."
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER, errors={0: error})

    grade = grade_trial(trial, question(), LEDGER)
    by_name = {c.name: c for c in grade.checks}

    assert by_name["infra.tool_errors"].passed is True
    assert by_name["l1.arg_errors"].passed is False
    assert by_name["l1.arg_errors"].gate is False
    assert grade.verdict == "pass"


def test_a_failed_run_skips_l1_and_l2() -> None:
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER, status="failed")

    grade = grade_trial(trial, question(must_call=["trend"]), LEDGER)

    assert grade.verdict == "infra"
    assert [c.name for c in grade.checks] == ["infra.run", "infra.tool_errors"]


def test_l2_verify_fails_an_ungrounded_number() -> None:
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER.replace("152,734", "152,743"))

    grade = grade_trial(trial, question(), LEDGER)

    assert checks(grade)["l2.verify"] is False
    assert "number_not_in_evidence" in {c.name: c for c in grade.checks}["l2.verify"].detail
    assert grade.verdict == "fail"


def test_l2_verify_resolves_citations_in_the_trials_own_ledger(tmp_path: Path) -> None:
    path = tmp_path / "eng-01__t0.ledger.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in LEDGER if r["evidence_id"] != "E7"))
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER, ledger_path=path)

    grade = grade_trial(trial, question())

    assert "unknown_evidence" in {c.name: c for c in grade.checks}["l2.verify"].detail


def test_l2_golden_must_appear_and_is_skipped_without_a_database() -> None:
    q = question(golden=[{"name": "g", "sql": "SELECT count(*) FROM {bgl}"}])
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER)

    assert checks(grade_trial(trial, q, LEDGER, {"g": 4_747_963.0}))["l2.golden"] is False
    assert checks(grade_trial(trial, q, LEDGER, {"g": None}))["l2.golden"] is None


def test_l2_mention_forbid_and_unknowns() -> None:
    q = question(mention=["(?i)kubernetes"], forbid=["(?i)parity"], unknowns=["(?i)cost"])
    trial = make_trial("eng-01", "engineer", ENGINEER_CALLS, ANSWER)

    result = checks(grade_trial(trial, q, LEDGER))

    assert result["l2.mention"] is False
    assert result["l2.forbid"] is False
    assert result["l2.unknowns"] is False


def test_subsequence_and_triple_overlap() -> None:
    assert is_subsequence(["a", "c"], ["a", "b", "c"])
    assert not is_subsequence(["c", "a"], ["a", "b", "c"])
    trial = make_trial(
        "tl-01",
        "team_lead",
        [("count_by", {"group_by": "node"}), ("trend", {}), ("verify_answer", {"draft": "x"})],
        ANSWER,
    )

    signature = tool_signature(trial.stream.calls)

    assert signature == frozenset({"count_by:node", "trend"})
    assert jaccard(signature, frozenset({"trend"})) == 0.5
    assert jaccard(frozenset(), frozenset()) == 1.0
