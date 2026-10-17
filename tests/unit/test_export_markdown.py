"""The investigation report renderer (T13): `export.render_investigation`.

The snapshot tests pin the whole document for the fixture ledger, per audience. Regenerate a snapshot
after an intended change with `UPDATE_SNAPSHOTS=1 make test` and review the diff.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from export_fixture import SEARCH_ROWS, SEARCH_TOTAL, TITLE, answers, records

from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.export import Answer, render_investigation

SNAPSHOTS = Path(__file__).parent / "snapshots"


def render(audience: Role) -> str:
    return render_investigation(audience, TITLE, answers(), records(), incident_tools=False).markdown


@pytest.mark.parametrize("audience", [Role.ENGINEER, Role.MANAGER])
def test_the_report_matches_its_snapshot(audience: Role) -> None:
    markdown = render(audience)
    snapshot = SNAPSHOTS / f"export_investigation_{audience.value}.md"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(markdown, encoding="utf-8")
    assert snapshot.exists(), f"{snapshot.name} is missing; create it with UPDATE_SNAPSHOTS=1 make test"
    assert markdown == snapshot.read_text(encoding="utf-8")


def test_each_answer_has_its_question_tool_calls_sql_results_and_answer() -> None:
    markdown = render(Role.ENGINEER)

    engineer = markdown.split("## 1. Engineer", 1)[1].split("## 2. Team Lead", 1)[0]
    assert "What failed on R30-M0-N9-C:J16-U01?" in engineer
    assert "#### E1: search_logs" in engineer
    assert '`search_logs(role="engineer", dataset="bgl", query="data TLB error"' in engineer
    assert "```sql\nSELECT count(*) FROM" in engineer
    assert "152,734" in engineer
    assert "#### E2: get_context" in engineer
    assert "The search matched 152,734 Log Entries [E1]" in engineer


def test_none_arguments_are_left_out_of_the_call() -> None:
    assert "time_from" not in render(Role.ENGINEER).split("#### E1", 1)[1].split("- **Data Scope", 1)[0]


def test_engineer_and_team_lead_tables_show_the_first_10_rows() -> None:
    for audience in (Role.ENGINEER, Role.TEAM_LEAD):
        e1 = render(audience).split("#### E1: search_logs", 1)[1].split("####", 1)[0]
        assert "| 1009 |" in e1
        assert "| 1010 |" not in e1
        assert f"Showing the first 10 of {SEARCH_ROWS} returned rows" in e1


def test_manager_audience_gets_no_raw_line_rows_but_keeps_counts_sql_and_scope() -> None:
    markdown = render(Role.MANAGER)

    # The rows' text (every fixture row starts with "synthetic"); the answers are shown as sent.
    assert "synthetic" not in markdown
    e1 = markdown.split("#### E1: search_logs", 1)[1].split("####", 1)[0]
    assert "Rows withheld for the manager audience" in e1
    assert f"{SEARCH_TOTAL:,}" in e1
    assert "```sql" in e1
    assert "**Data Scope:** bgl" in e1
    # Aggregation rows are measured counts, so they stay.
    assert "| bgl | KERNDTLB | 152734 | 3.22 |" in markdown


def test_table_cells_escape_pipes_and_angle_brackets() -> None:
    markdown = render(Role.ENGINEER)

    assert r"interrupt \| sample 0" in markdown
    assert "&lt;machine check&gt;" in markdown


def test_answer_headings_nest_below_the_answer_heading() -> None:
    markdown = render(Role.ENGINEER)

    assert "\n#### Observed Facts\n" in markdown
    assert "\n## Observed Facts\n" not in markdown


def test_evidence_another_role_produced_is_referenced_not_repeated() -> None:
    markdown = render(Role.ENGINEER)

    assert markdown.count("#### E1: search_logs") == 1
    team_lead = markdown.split("## 2. Team Lead", 1)[1].split("## 3. Manager", 1)[0]
    assert "E1 (search_logs, engineer), shown under 1. Engineer" in team_lead


def test_evidence_no_answer_cites_is_shown_under_its_role() -> None:
    engineer = render(Role.ENGINEER).split("## 1. Engineer", 1)[1].split("## 2. Team Lead", 1)[0]

    assert "#### E6: count_by" in engineer


def test_evidence_ids_restrict_the_report_but_cited_evidence_stays() -> None:
    report = render_investigation(
        Role.ENGINEER, TITLE, answers(), records(), evidence_ids=["E1", "E3"], incident_tools=False
    )

    assert "#### E6" not in report.markdown
    assert "#### E2: get_context" in report.markdown  # cited by the Engineer's answer
    assert report.evidence_ids == ["E1", "E2", "E3", "E4", "E5"]


def test_a_citation_the_ledger_does_not_hold_is_reported() -> None:
    broken = Answer(
        role=Role.ENGINEER,
        question="q",
        answer="## Observed Facts\n- 5 nodes [E9].\n## Hypotheses\n- h\n## Unknowns\n- u\n"
        "## Next Step\n- s\n",
    )

    report = render_investigation(Role.ENGINEER, TITLE, [broken], records(), incident_tools=False)

    assert any("E9" in note for note in report.notes)
    assert "unknown_evidence" in report.markdown


def test_the_answers_are_checked_like_verify_answer() -> None:
    markdown = render(Role.ENGINEER)

    assert "verify_answer found no problem" in markdown


def test_the_closing_section_separates_what_is_and_is_not_established() -> None:
    closing = render(Role.ENGINEER).split("## What the data does and does not establish", 1)[1]

    established, not_established = closing.split("### What it does not establish", 1)
    assert "bgl, openstack" in established
    assert "search_logs" in established
    assert "count_by" in established
    assert "The node's memory hardware is failing" in not_established
    assert "Cost, downtime and customer impact are not measured" in not_established
    assert f"E1 returned {SEARCH_ROWS} of {SEARCH_TOTAL:,} matching Log Entries" in not_established
    assert "not Incidents" in not_established
    assert "cost or financial loss" in not_established


def test_the_report_is_deterministic() -> None:
    assert render(Role.ENGINEER) == render(Role.ENGINEER)


def test_an_empty_ledger_still_renders_the_answers() -> None:
    report = render_investigation(Role.ENGINEER, TITLE, answers(), [], incident_tools=False)

    assert "What failed on R30-M0-N9-C:J16-U01?" in report.markdown
    assert report.evidence_ids == []
    assert any("E1" in note for note in report.notes)


def test_an_answer_without_evidence_says_so() -> None:
    report = render_investigation(Role.ENGINEER, TITLE, answers()[:1], [], incident_tools=False)

    assert "No Evidence in the Evidence Ledger belongs to this answer." in report.markdown


def test_sql_with_backticks_gets_a_longer_fence() -> None:
    record = records()[0]
    record["sql"] = ["SELECT '```' AS fence"]
    record["params"] = [[]]

    markdown = render_investigation(
        Role.ENGINEER, TITLE, answers()[:1], [record], incident_tools=False
    ).markdown

    assert "````sql\nSELECT '```' AS fence\n````" in markdown


def _engineer_answer(fact: str) -> str:
    return f"## Observed Facts\n- {fact}\n## Hypotheses\n- h\n## Unknowns\n- u\n## Next Step\n- s\n"


def test_evidence_is_shown_under_the_answer_that_cites_it_when_a_role_answers_twice() -> None:
    first = Answer(
        role=Role.ENGINEER,
        question="What failed on the node?",
        answer=_engineer_answer("The search matched 152,734 Log Entries [E1]."),
    )
    second = Answer(
        role=Role.ENGINEER,
        question="How many FATAL Log Entries are there?",
        answer=_engineer_answer("855,501 Log Entries are FATAL [E6]."),
    )

    markdown = render_investigation(
        Role.ENGINEER, TITLE, [first, second], records(), evidence_ids=["E1", "E6"], incident_tools=False
    ).markdown

    one, two = markdown.split("## 1. Engineer", 1)[1].split("## 2. Engineer", 1)
    two = two.split("## What the data", 1)[0]
    assert "#### E1: search_logs" in one
    assert "#### E6: count_by" not in one
    assert "#### E6: count_by" in two
    assert "#### E1" not in two


def test_uncited_evidence_goes_to_the_answer_of_its_role_it_was_produced_for() -> None:
    first = Answer(
        role=Role.ENGINEER,
        question="What failed on the node?",
        answer=_engineer_answer("The search matched 152,734 Log Entries [E1]."),
    )
    second = Answer(
        role=Role.ENGINEER,
        question="How many FATAL Log Entries are there?",
        answer=_engineer_answer("855,501 Log Entries are FATAL [E6]."),
    )

    markdown = render_investigation(
        Role.ENGINEER,
        TITLE,
        [first, second],
        records(),
        evidence_ids=["E1", "E2", "E6"],
        incident_tools=False,
    ).markdown

    # E2 was produced after the last Evidence the first answer cites and before the second answer's.
    one, two = markdown.split("## 1. Engineer", 1)[1].split("## 2. Engineer", 1)
    assert "#### E2: get_context" not in one
    assert "#### E2: get_context" in two.split("## What the data", 1)[0]
