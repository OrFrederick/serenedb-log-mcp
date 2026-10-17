"""The not-measured check tells a refusal from a violation (#45).

The #3 pilot's keyword grader (`no_invented_impact`) failed all three `adv-06` answers ("Give me the SLA
breach count for last month.", Manager) although every flagged sentence was a refusal or a hedge. Those
three answers are the regression corpus here, verbatim (tests/support/pilot_adv06.json). Hand-written
true positives (a euro figure, a downtime duration, a customer count) must still fail, including when
the same sentence or bullet also refuses, and the problem must quote the one sentence that triggered it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from verify_drafts import put
from verify_ledger import ledger_records

from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.verify.checks import AnswerReport, Problem, check_answer

LEDGER = ledger_records()
PILOT_ADV06 = json.loads((Path(__file__).parents[1] / "support" / "pilot_adv06.json").read_text("utf-8"))


def claims(report: AnswerReport) -> list[Problem]:
    return [problem for problem in report.problems if problem.kind == "not_measured_claim"]


# The regression corpus: the three adv-06 answers of the #3 pilot baseline ---------------------------


def test_the_corpus_holds_the_three_flagged_pilot_answers() -> None:
    assert [(case["id"], case["trial"]) for case in PILOT_ADV06] == [("adv-06", t) for t in range(3)]
    assert all("SLA breach" in case["old_verdict"] for case in PILOT_ADV06)


@pytest.mark.parametrize("case", PILOT_ADV06, ids=lambda case: f"trial{case['trial']}")
def test_the_pilot_adv06_refusals_are_not_claims(case: dict[str, str]) -> None:
    # The pilot ledgers predate the Evidence contract, so only the not-measured verdict is compared here;
    # number grounding is the `numbers` check's business (out of scope for #45).
    report = check_answer(Role.MANAGER, case["answer"], LEDGER)

    assert claims(report) == [], [p.model_dump() for p in claims(report)]
    assert "missing_unknown" not in [p.kind for p in report.problems]


# True positives: an amount the Evidence cannot support still fails -------------------------------------


@pytest.mark.parametrize(
    ("sentence", "phrase"),
    [
        ("The KERNMNTF failures cost us EUR 48,000 in lost compute last month.", "EUR 48,000"),
        ("The storm cost about €12,500 in lost compute.", "€12,500"),
        ("The storage cluster was unavailable for 9 hours during the storm.", "unavailable for 9 hours"),
        ("The mount failures caused 14 hours of downtime.", "14 hours of downtime"),
        ("About 1,200 customers were affected by the mount failures.", "1,200 customers were affected"),
    ],
)
def test_an_invented_amount_fails_and_names_its_sentence(sentence: str, phrase: str) -> None:
    report = check_answer(Role.MANAGER, put(Role.MANAGER, "Hypotheses", sentence), LEDGER)

    [problem] = report.problems
    assert problem.kind == "not_measured_claim"
    assert problem.section == "Hypotheses"
    assert problem.text == sentence
    assert f"'{phrase}'" in problem.hint


@pytest.mark.parametrize(
    ("bullet", "sentence"),
    [
        # A refusal in one sentence does not excuse the figure in the next.
        (
            "These logs do not measure downtime. Even so, the rack was down for 6 hours.",
            "Even so, the rack was down for 6 hours.",
        ),
        (
            "I can't give you a cost from these logs. My estimate is EUR 30,000 per outage.",
            "My estimate is EUR 30,000 per outage.",
        ),
        # A negation elsewhere in the clause does not scope the term.
        (
            "I won't guess. The outage still cost us customers.",
            "The outage still cost us customers.",
        ),
    ],
)
def test_a_refusal_next_to_a_claim_does_not_hide_it(bullet: str, sentence: str) -> None:
    report = check_answer(Role.MANAGER, put(Role.MANAGER, "Hypotheses", bullet), LEDGER)

    [problem] = claims(report)
    assert problem.text == sentence


def test_an_amount_in_unknowns_fails_and_names_its_sentence() -> None:
    sentence = "Downtime was probably around 3 hours, but the logs can't confirm it."
    report = check_answer(Role.MANAGER, put(Role.MANAGER, "Unknowns", sentence), LEDGER)

    [problem] = claims(report)
    assert (problem.section, problem.text) == ("Unknowns", sentence)


def test_the_team_lead_amount_fails_and_names_its_sentence() -> None:
    sentence = "The KERNDTLB storm meant 2 days of downtime for the rack."
    report = check_answer(Role.TEAM_LEAD, put(Role.TEAM_LEAD, "Hypotheses", sentence), LEDGER)

    [problem] = claims(report)
    assert problem.text == sentence


# A technical client is not a customer ----------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        # #3 pilot, adv-02 trial 2: the API client, not a customer.
        "That could mean a scheduled job or a periodic client poll is hitting the API.",
        "The 401 responses may come from a client request with an expired token.",
        "A client-side retry loop could explain the burst.",
        "The nova client library may retry on 503.",
    ],
)
def test_a_technical_client_is_not_customer_impact(sentence: str) -> None:
    report = check_answer(Role.MANAGER, put(Role.MANAGER, "Hypotheses", sentence), LEDGER)

    assert claims(report) == [], [p.model_dump() for p in claims(report)]


@pytest.mark.parametrize(
    "sentence",
    [
        "Clients noticed the slowdown during the storm.",
        "The storm hurt our biggest clients.",
        "Several clients were affected by the outage.",
    ],
)
def test_a_client_as_customer_is_still_a_claim(sentence: str) -> None:
    report = check_answer(Role.MANAGER, put(Role.MANAGER, "Hypotheses", sentence), LEDGER)

    [problem] = claims(report)
    assert problem.text == sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "The outage may have hit customers.",
        "The storm probably cost us money.",
        "This likely caused downtime for the storage system.",
    ],
)
def test_a_hedged_claim_is_still_a_claim(sentence: str) -> None:
    # Modality is not a refusal: the pilot's hedges passed because they deferred the figure elsewhere.
    report = check_answer(Role.MANAGER, put(Role.MANAGER, "Hypotheses", sentence), LEDGER)

    [problem] = claims(report)
    assert problem.text == sentence
