"""How fast `verify_answer` checks a draft: a large Evidence Ledger and very long drafts (#81).

The agent calls verify_answer before every final answer and reads the whole ledger each time, so the
check must stay fast as the ledger grows, and a hostile or runaway draft must never stall the server:
the Evidence lookups are built only for the records a draft cites, and the draft and each sentence
have a size cap above which the check fails fast with a problem that says so.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

import pytest
from verify_drafts import ENGINEER, GOOD, MANAGER, TEAM_LEAD, put
from verify_ledger import ledger_records

from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.verify import checks
from serenedb_log_mcp.verify.checks import MAX_DRAFT, MAX_SENTENCE, AnswerReport, check_answer

LEDGER = ledger_records()
LEDGER_SIZE = 10_000
BUDGET_S = 1.0


def big_ledger() -> list[dict[str, Any]]:
    """LEDGER_SIZE records: the fixture ledger, then copies of its records under new ids."""
    records = list(LEDGER)
    for n in range(len(LEDGER) + 1, LEDGER_SIZE + 1):
        records.append({**LEDGER[n % len(LEDGER)], "evidence_id": f"E{n}"})
    return records


BIG = big_ledger()


def timed(role: Role, draft: str, ledger: list[dict[str, Any]]) -> tuple[AnswerReport, float]:
    started = time.perf_counter()
    report = check_answer(role, draft, ledger)
    return report, time.perf_counter() - started


def kinds(report: AnswerReport) -> list[str]:
    return [problem.kind for problem in report.problems]


# A large Evidence Ledger ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_a_correct_answer_is_checked_against_a_large_ledger_within_a_second(role: Role) -> None:
    report, elapsed = timed(role, GOOD[role], BIG)

    assert report.ok, [p.model_dump() for p in report.problems]
    assert elapsed < BUDGET_S, f"{elapsed:.2f} s for {LEDGER_SIZE} records"


@pytest.mark.parametrize(
    ("section", "sentence", "kind"),
    [
        ("Hypotheses", "The storm wrote 152,999 lines on one node.", "uncited_number"),
        ("Observed Facts", "The storm wrote 152,999 Log Entries [E1].", "number_not_in_evidence"),
    ],
)
def test_a_wrong_number_is_reported_against_a_large_ledger_within_a_second(
    section: str, sentence: str, kind: str
) -> None:
    report, elapsed = timed(Role.TEAM_LEAD, put(Role.TEAM_LEAD, section, sentence), BIG)

    assert kinds(report) == [kind]
    assert elapsed < BUDGET_S, f"{elapsed:.2f} s for {LEDGER_SIZE} records"


def test_evidence_lookups_are_built_only_for_cited_records(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []
    real = checks._pool

    def counting(record: Mapping[str, Any]) -> checks._Pool:
        built.append(str(record.get("evidence_id")))
        return real(record)

    monkeypatch.setattr(checks, "_pool", counting)

    report = check_answer(Role.TEAM_LEAD, TEAM_LEAD, BIG)

    assert report.ok
    assert sorted(set(built)) == sorted(report.cited_evidence)
    assert len(built) == len(set(built)), "a record's lookups were built twice"


def test_the_it_is_in_hint_still_names_the_evidence_that_holds_an_uncited_number() -> None:
    draft = put(Role.TEAM_LEAD, "Hypotheses", "The storm wrote 152,182 lines on one node.")

    report, _ = timed(Role.TEAM_LEAD, draft, BIG)

    assert kinds(report) == ["uncited_number"]
    named = re.search(r"It is in (E[0-9]+)", report.problems[0].hint)
    assert named is not None, report.problems[0].hint
    by_id = {record["evidence_id"]: record for record in BIG}
    assert by_id[named.group(1)]["tool"] == "list_incidents"  # a copy of E3, which holds 152,182


# Very long drafts and sentences -----------------------------------------------------------------------

FILLERS = [
    "word ",
    "12,345 ",
    "1-",
    "1,",
    "1.",
    "2005 ",
    '"',
    "x.",
    "| 1 ",
    "6 hours ",
    "not cost ",
    "cost 1-",
    "not a ",
    "a: ",
    "ask a ",
    "a or ",
    "R1",
    "- ",
    "half ",
    "a third ",
    "152,734 Log Entries [E1] ",
    "[E1\u2013E9] ",
    "line numbers such as 1\u20132 ",
    "the 404 ",
]
"""Repeated units that made a regex search quadratic or worse on one long sentence (#69 review)."""


def repeat(filler: str, size: int) -> str:
    return (filler * (size // len(filler) + 1))[:size]


@pytest.mark.parametrize("filler", FILLERS)
def test_a_sentence_at_the_size_cap_is_checked_quickly(filler: str) -> None:
    sentence = repeat(filler, MAX_SENTENCE - 1)
    draft = put(Role.MANAGER, "Hypotheses", sentence)

    report, elapsed = timed(Role.MANAGER, draft, LEDGER)

    assert "checker_error" not in kinds(report)
    assert "sentence_too_long" not in kinds(report)
    assert elapsed < 0.25, f"{elapsed:.2f} s for one sentence of {len(sentence)} characters"


def test_a_draft_at_the_size_cap_is_checked_within_seconds() -> None:
    sentences = [repeat(filler, MAX_SENTENCE - 2) + "." for filler in FILLERS]
    body = ""
    while len(body) < MAX_DRAFT - len(MANAGER) - 2 * MAX_SENTENCE:
        body += sentences[len(body) % len(sentences)] + "\n\n"
    draft = put(Role.MANAGER, "Hypotheses", body)
    assert len(draft) <= MAX_DRAFT

    report, elapsed = timed(Role.MANAGER, draft, LEDGER)

    assert "checker_error" not in kinds(report)
    assert elapsed < 3 * BUDGET_S, f"{elapsed:.2f} s for a draft of {len(draft)} characters"


def test_a_sentence_over_the_size_cap_is_a_problem_not_a_stall() -> None:
    sentence = repeat("1-", 10 * MAX_SENTENCE) + " [E1]."
    draft = put(Role.ENGINEER, "Observed Facts", sentence)

    report, elapsed = timed(Role.ENGINEER, draft, LEDGER)

    assert kinds(report) == ["sentence_too_long"]
    assert report.problems[0].section == "Observed Facts"
    assert str(MAX_SENTENCE) in report.problems[0].hint.replace(",", "")
    assert elapsed < BUDGET_S


@pytest.mark.parametrize("filler", ["1-", "word ", "not cost "])
def test_a_draft_over_the_size_cap_is_a_problem_not_a_stall(filler: str) -> None:
    draft = ENGINEER + repeat(filler, 1_000_000)

    report, elapsed = timed(Role.ENGINEER, draft, LEDGER)

    assert kinds(report) == ["draft_too_long"]
    assert str(MAX_DRAFT) in report.problems[0].hint.replace(",", "")
    assert elapsed < BUDGET_S


def test_a_long_correct_answer_under_the_caps_still_passes() -> None:
    facts = "\n".join(["- KERNDTLB holds 152,734 Log Entries [E1] in 3 Incidents [E8]."] * 150)
    draft = MANAGER.replace("## Hypotheses", f"{facts}\n\n## Hypotheses")
    assert len(draft) < MAX_DRAFT

    report, elapsed = timed(Role.MANAGER, draft, LEDGER)

    assert report.ok, [p.model_dump() for p in report.problems][:3]
    assert elapsed < BUDGET_S
