"""The pure parts of scripts/score_patterns.py: agreement, the merge gate and the report."""

from __future__ import annotations

import pytest

import score_patterns as sp
from score_patterns import Coverage, Outcome

OUTCOMES = [
    Outcome(gold="E1", chosen="E1", lines=990, messages=10, example="a 1", gold_template="a <*>"),
    Outcome(gold="E2", chosen="E2", lines=5, messages=2, example="b 1", gold_template="b <*>"),
    Outcome(gold="E2", chosen="E1", lines=3, messages=1, example="b|x", gold_template="b <*>"),
    Outcome(gold="E7", chosen=None, lines=2, messages=1, example="trunc", gold_template="c <*> d"),
]


def test_score_counts_agreeing_and_covered_lines() -> None:
    score = sp.score("bgl", OUTCOMES)

    assert (score.lines, score.agreeing, score.covered) == (1000, 995, 998)
    assert score.agreement == pytest.approx(0.995)
    assert score.covered_agreement == pytest.approx(995 / 998)


def test_disagreements_are_every_group_whose_choice_differs_most_lines_first() -> None:
    score = sp.score("bgl", OUTCOMES)

    assert [(d.gold, d.chosen, d.lines) for d in score.disagreements] == [("E2", "E1", 3), ("E7", None, 2)]


def test_no_labelled_lines_score_zero_without_dividing_by_zero() -> None:
    score = sp.score("bgl", [])

    assert (score.agreement, score.covered_agreement) == (0.0, 0.0)


def test_the_gate_passes_at_the_minimum_and_names_each_dataset_below_it() -> None:
    good = sp.score("openstack", [Outcome("E1", "E1", 10_000, 1, "x", "x")])
    bad = sp.score("bgl", OUTCOMES)

    assert sp.gate_problems([good], 0.9999) == []
    problems = sp.gate_problems([good, bad], 0.9999)
    assert len(problems) == 1
    assert "bgl" in problems[0]
    assert "99.5000%" in problems[0]


def test_a_dataset_without_tier1_matches_fails_the_gate_with_a_hint() -> None:
    off = sp.score("bgl", [Outcome("E1", None, 10, 1, "x", "x")])

    (problem,) = sp.gate_problems([off], 0.9999)

    assert "SLM_PATTERN_TIER1" in problem


def test_percent_keeps_four_decimals_and_never_rounds_up_to_100() -> None:
    assert sp.percent(0.99999913) == "99.9999%"
    assert sp.percent(0.999999999) == "99.9999%"
    assert sp.percent(1.0) == "100%"


def test_the_report_lists_scores_disagreements_and_coverage() -> None:
    scores = [sp.score("bgl", OUTCOMES)]
    coverage = [
        Coverage("bgl", "loghub2", patterns=2, messages=12, entries=998),
        Coverage("bgl", "sqlmask", patterns=1, messages=1, entries=2),
    ]

    text = sp.report("slm", "public", scores, coverage, {"bgl": (13, 13)}, 0.9999)

    assert "| bgl | 1,000 | 995 | 99.5000% | 99.8000% | 99.6993% |" in text
    assert "| E2 | E1 | 3 | 1 | b <*> | `b\\|x` |" in text
    assert "| E7 | none (Tier 2) | 2 | 1 | c <*> d | `trunc` |" in text
    assert "| bgl | loghub2 | 2 | 12 | 998 | 99.8000% |" in text
    assert "| Tier 1 coverage of labelled lines |" in text
    assert "13 of 13" in text
    assert "FAIL" in text


def test_the_report_says_pass_when_the_gate_holds() -> None:
    scores = [sp.score("openstack", [Outcome("E1", "E1", 10, 1, "x", "x")])]

    text = sp.report("slm", "public", scores, [], {}, 0.9999)

    assert "PASS" in text
    assert "no disagreeing template" in text
