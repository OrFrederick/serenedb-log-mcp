"""The answer checks shared by `verify_answer` and the eval's L2 grader (T12).

Every broken answer below is a correct answer with one deliberate fault, so each test shows that the
checks catch that fault and nothing else. The faults include the #3 pilot failures: rounding,
bracketed ranges, self-computed sums and missing citations.
"""

from __future__ import annotations

import random
import string
import textwrap

import pytest
from verify_ledger import ledger_records

from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.verify.checks import PREAMBLE, AnswerReport, check_answer, numbers_in, split_sections

LEDGER = ledger_records()

ENGINEER = """\
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

TEAM_LEAD = """\
## Observed Facts
Data Scope: BGL, whole Dataset, alert categories [E1].

**Top BGL alert categories by Log Entries [E1]**

| Category | Log Entries | Share |
|---|---|---|
| KERNDTLB | 152,734 | 43.83% |
| KERNSTOR | 63,491 | 18.22% |
| APPSEV | 49,651 | 14.25% |

- The top 3 categories hold 265,876 of 348,460 alert Log Entries, 76.3% [E1].
- KERNDTLB is 3 Incidents [E3]: the largest has 152,182 Log Entries on 1 node, 100% on its top node [E3].
- The other two Incidents reached 380 and 150 nodes across 44 and 30 midplanes [E3].
- Weekly, KERNDTLB peaked at 152,252 Log Entries in the week of 2005-06-06 [E4].

## Hypotheses
- The single-node storm may be one looping node rather than a machine-wide problem.

## Unknowns
- Which Team owns KERNEL: the Ownership Table is a stub.

## Next Step
- Investigate the two multi-midplane KERNDTLB Incidents first, with the KERNEL owners.
"""

MANAGER = """\
## Observed Facts
Bottom line: KERNMNTF deserves attention first; KERNDTLB is largest by Log Entries but mostly one node.

1. KERNMNTF: 26,426 Log Entries in 83 Incidents across 64 midplanes, active in 9 weeks [E8].
2. KERNDTLB: 152,734 Log Entries [E1] in 3 Incidents [E8]; 152,182 are one Incident on one node [E3].

Data Scope: BGL, whole Dataset [E1][E8].

## Hypotheses
- The order could change with another weighting: KERNDTLB leads by volume [E1].

## Unknowns
- Cost or financial loss: these logs hold no prices or billing.
- Downtime: these logs hold no uptime or outage records.
- Customer impact: these logs name no customers or users.

## Next Step
1. Ask the storage Team to review the KERNMNTF mount failures first.
2. Ask the node owners to check the KERNDTLB node.
"""

GOOD = {Role.ENGINEER: ENGINEER, Role.TEAM_LEAD: TEAM_LEAD, Role.MANAGER: MANAGER}


def check(role: Role, draft: str, **options: bool) -> AnswerReport:
    return check_answer(role, draft, LEDGER, **options)


def kinds(report: AnswerReport) -> list[str]:
    return [problem.kind for problem in report.problems]


def only(report: AnswerReport, kind: str) -> str:
    """The text and hint of the one problem the report holds, which must be of `kind`."""
    assert kinds(report) == [kind], [p.model_dump() for p in report.problems]
    problem = report.problems[0]
    return f"{problem.text} || {problem.hint}"


def replace(draft: str, old: str, new: str) -> str:
    assert old in draft, old
    return draft.replace(old, new, 1)


# The correct answers --------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_a_correct_answer_for_each_role_passes(role: Role) -> None:
    report = check(role, GOOD[role])

    assert report.ok, [p.model_dump() for p in report.problems]
    assert report.problems == []
    assert report.checked_numbers


def test_checked_numbers_say_where_each_number_was_found() -> None:
    report = check(Role.TEAM_LEAD, TEAM_LEAD)

    by_text = {n.text: n for n in report.checked_numbers}
    assert by_text["152,182"].status == "in_evidence"
    assert by_text["152,182"].found_in == ["E3"]
    assert by_text["152,182"].value == 152_182
    assert by_text["43.83%"].found_in == ["E1"]
    assert set(report.cited_evidence) == {"E1", "E3", "E4"}


def test_a_role_name_string_is_accepted() -> None:
    assert check_answer("team_lead", TEAM_LEAD, LEDGER).ok


# Broken answers: the #3 pilot failures -------------------------------------------------------------


def test_rounding_with_a_k_suffix_is_rejected() -> None:
    draft = replace(MANAGER, "152,734 Log Entries [E1]", "about 150k Log Entries [E1]")

    found = only(check(Role.MANAGER, draft), "rounded_number")
    assert "150k" in found
    assert "152,734" in found  # the hint names the exact value


def test_a_rounded_decimal_is_rejected_with_the_exact_value() -> None:
    draft = replace(TEAM_LEAD, "| 43.83% |", "| 43.8% |")

    found = only(check(Role.TEAM_LEAD, draft), "rounded_number")
    assert "43.83" in found


def test_a_bound_instead_of_the_number_is_rejected() -> None:
    draft = replace(TEAM_LEAD, "hold 265,876 of", "hold fewer than 270,000 of")

    assert "270,000" in only(check(Role.TEAM_LEAD, draft), "rounded_number")


def test_a_bracketed_range_is_rejected() -> None:
    draft = replace(TEAM_LEAD, "peaked at 152,252 Log Entries", "peaked at ~150k\u2013155k Log Entries")

    report = check(Role.TEAM_LEAD, draft)
    assert set(kinds(report)) == {"range"}
    assert "150k" in report.problems[0].text


def test_a_self_computed_sum_without_the_derived_mark_is_rejected() -> None:
    draft = replace(
        TEAM_LEAD,
        "- The top 3 categories",
        "- KERNDTLB and KERNSTOR together hold 216,225 Log Entries [E1].\n- The top 3 categories",
    )

    found = only(check(Role.TEAM_LEAD, draft), "unmarked_derivation")
    assert "216,225" in found
    assert "(derived from [E1])" in found


def test_a_derived_number_that_does_not_recompute_is_rejected() -> None:
    # The pilot's wrong arithmetic: a sum off by 200 that looked plausible.
    draft = replace(
        TEAM_LEAD,
        "- The top 3 categories",
        "- KERNDTLB and KERNSTOR together hold 216,425 Log Entries (derived from [E1]).\n"
        "- The top 3 categories",
    )

    found = only(check(Role.TEAM_LEAD, draft), "derived_mismatch")
    assert "216,425" in found
    assert "216,225" in found  # the nearest sum that does recompute


def test_a_number_without_a_citation_is_rejected() -> None:
    draft = replace(
        TEAM_LEAD, "peaked at 152,252 Log Entries in the week of 2005-06-06 [E4].", "peaked at 152,252."
    )

    found = only(check(Role.TEAM_LEAD, draft), "uncited_number")
    assert "152,252" in found
    assert "[E" in found


def test_an_answer_without_any_citation_is_rejected() -> None:
    draft = (
        "## Observed Facts\n- Node R30-M0-N9-C:J16-U01 logged a data TLB error at FATAL level.\n"
        "## Hypotheses\n- Failing memory.\n## Unknowns\n- Replacement history.\n"
        "## Next Step\n- Check the node.\n"
    )

    assert "[E<n>]" in only(check(Role.ENGINEER, draft), "no_citation")


# Broken answers: citations and Evidence ------------------------------------------------------------


def test_a_citation_to_an_unknown_evidence_id_is_rejected() -> None:
    draft = replace(
        ENGINEER, "corrected an instruction cache parity error [E7]", "corrected a parity error [E9]"
    )

    found = only(check(Role.ENGINEER, draft), "unknown_evidence")
    assert "[E9]" in found
    assert "E1" in found  # the hint names the ids that exist
    assert "E8" in found


def test_a_number_that_is_not_in_the_cited_evidence_is_rejected() -> None:
    draft = replace(MANAGER, "152,734 Log Entries [E1]", "152,812 Log Entries [E1]")

    found = only(check(Role.MANAGER, draft), "number_not_in_evidence")
    assert "152,812" in found
    assert "152,734" in found  # the nearest value E1 holds


def test_a_number_from_other_evidence_names_the_evidence_to_cite() -> None:
    draft = replace(
        TEAM_LEAD, "152,252 Log Entries in the week of 2005-06-06 [E4]", "152,182 Log Entries [E4]"
    )

    found = only(check(Role.TEAM_LEAD, draft), "number_not_in_evidence")
    assert "[E3]" in found


def test_a_number_absent_from_the_evidence_text_is_rejected() -> None:
    draft = replace(ENGINEER, "64172 MB", "64999 MB")

    assert "64999" in only(check(Role.ENGINEER, draft), "number_not_in_evidence")


# Broken answers: sections ---------------------------------------------------------------------------


def test_sections_out_of_order_are_rejected() -> None:
    facts, rest = ENGINEER.split("## Hypotheses")
    hypotheses, tail = rest.split("## Unknowns")
    draft = f"## Hypotheses{hypotheses}{facts}## Unknowns{tail}"

    found = only(check(Role.ENGINEER, draft), "section_order")
    assert "Observed Facts, Hypotheses, Unknowns, Next Step" in found


def test_a_missing_section_is_rejected() -> None:
    draft = replace(
        ENGINEER,
        "## Unknowns\n- Whether the node was replaced afterwards: the logs hold no hardware records.\n",
        "",
    )

    assert "Unknowns" in only(check(Role.ENGINEER, draft), "missing_section")


def test_an_empty_section_is_rejected() -> None:
    draft = replace(
        TEAM_LEAD, "- The single-node storm may be one looping node rather than a machine-wide problem.\n", ""
    )

    assert "Hypotheses" in only(check(Role.TEAM_LEAD, draft), "empty_section")


@pytest.mark.parametrize(
    "style",
    ["**{name}**", "**{name}:**", "{name}:", "### {name}", "## 1. {name}", "# {name} (BGL, 2005-06-11)"],
)
def test_common_section_heading_styles_are_recognised(style: str) -> None:
    draft = ENGINEER
    for name in ("Observed Facts", "Hypotheses", "Unknowns", "Next Step"):
        draft = replace(draft, f"## {name}", style.format(name=name))

    assert check(Role.ENGINEER, draft).ok


# Broken answers: Role checks ------------------------------------------------------------------------


def test_the_manager_citing_raw_line_evidence_is_rejected() -> None:
    draft = replace(MANAGER, "152,734 Log Entries [E1]", "152,734 Log Entries [E2]")

    found = only(check(Role.MANAGER, draft), "manager_raw_line_evidence")
    assert "E2" in found
    assert "count_by" in found


def test_other_roles_may_cite_raw_line_evidence() -> None:
    assert check(Role.ENGINEER, ENGINEER).ok  # cites E2 (search_logs) and E7 (get_context)


@pytest.mark.parametrize(
    "claim",
    [
        "The KERNDTLB storm cost about $40,000 in lost compute.",
        "The mount failures caused significant downtime for the storage system.",
        "There was no customer impact.",
        "KERNMNTF put the SLA at risk.",
        "Fixing it first protects revenue.",
    ],
)
def test_the_manager_claiming_what_the_logs_do_not_measure_is_rejected(claim: str) -> None:
    draft = replace(MANAGER, "## Hypotheses\n", f"## Hypotheses\n- {claim}\n")

    assert "Unknowns" in only(check(Role.MANAGER, draft), "not_measured_claim")


@pytest.mark.parametrize(
    "refusal",
    [
        # The #3 pilot's adv-06 answers, which a keyword grader flagged by mistake (#42).
        "I can't give you an SLA breach count.",
        "SLA breaches are probably tracked outside these logs, for example in a ticketing or monitoring "
        "system.",
        "I'd label that as a proxy and not as an SLA breach count.",
        "SLA breaches are probably tracked in a separate system, such as ticketing, monitoring or contract "
        "data.",
        "These logs do not measure downtime, so I cannot estimate the cost.",
    ],
)
def test_the_manager_may_say_what_the_logs_do_not_measure(refusal: str) -> None:
    draft = replace(MANAGER, "## Hypotheses\n", f"## Hypotheses\n- {refusal}\n")

    assert check(Role.MANAGER, draft).ok


def test_other_roles_are_not_held_to_the_manager_terms() -> None:
    draft = replace(
        TEAM_LEAD,
        "## Hypotheses\n",
        "## Hypotheses\n- A customer-facing service may depend on these nodes.\n",
    )

    assert check(Role.TEAM_LEAD, draft).ok


def test_the_manager_must_name_the_required_unknowns() -> None:
    draft = replace(MANAGER, "- Customer impact: these logs name no customers or users.\n", "")

    assert "customer" in only(check(Role.MANAGER, draft), "missing_unknown").lower()


def test_a_count_called_incidents_needs_incident_evidence() -> None:
    draft = replace(TEAM_LEAD, "peaked at 152,252 Log Entries", "peaked at 152,252 incidents")

    found = only(check(Role.TEAM_LEAD, draft), "incident_count_without_incident_evidence")
    assert "152,252 incidents" in found
    assert "list_incidents" in found


def test_a_count_of_incidents_with_incident_evidence_passes() -> None:
    assert "3 Incidents [E3]" in TEAM_LEAD
    assert check(Role.TEAM_LEAD, TEAM_LEAD).ok


def test_a_team_lead_log_entry_count_needs_incident_and_spread_evidence() -> None:
    draft = "\n".join(line for line in TEAM_LEAD.splitlines() if "[E3]" not in line)

    found = only(check(Role.TEAM_LEAD, draft), "log_entries_without_incidents")
    assert "list_incidents" in found
    assert "top_patterns" in found


def test_the_side_by_side_check_is_skipped_while_no_incident_tool_is_offered() -> None:
    draft = "\n".join(line for line in TEAM_LEAD.splitlines() if "[E3]" not in line)

    report = check(Role.TEAM_LEAD, draft, incident_tools=False)
    assert report.ok
    assert any("list_incidents" in note for note in report.notes)


def test_the_engineer_needs_no_incident_evidence_for_a_log_entry_count() -> None:
    assert "152,734 Log Entries in total [E2]" in ENGINEER
    assert check(Role.ENGINEER, ENGINEER).ok


# Correct answers that a naive checker would reject -------------------------------------------------


def test_a_marked_derived_sum_that_recomputes_passes() -> None:
    draft = replace(
        TEAM_LEAD,
        "- The top 3 categories",
        "- KERNDTLB and KERNSTOR together hold 216,225 Log Entries (derived from [E1]).\n"
        "- The top 3 categories",
    )

    report = check(Role.TEAM_LEAD, draft)
    assert report.ok, [p.model_dump() for p in report.problems]
    assert {n.text: n.status for n in report.checked_numbers}["216,225"] == "derived"


def test_a_marked_derived_share_across_two_evidence_records_passes() -> None:
    draft = replace(
        TEAM_LEAD,
        "- Weekly,",
        "- 152,182 of the 152,734 KERNDTLB Log Entries, 99.64%, are one Incident (derived from [E3],[E1]).\n"
        "- Weekly,",
    )

    assert check(Role.TEAM_LEAD, draft).ok


def test_a_marked_derived_average_passes() -> None:
    draft = replace(
        TEAM_LEAD,
        "- Weekly,",
        "- KERNDTLB averaged 38,183.5 Log Entries per active week (derived from [E4]).\n- Weekly,",
    )

    assert check(Role.TEAM_LEAD, draft).ok


def test_numbers_inside_evidence_row_text_count_as_evidence() -> None:
    assert "64172 MB of total memory [E6]" in ENGINEER
    assert check(Role.ENGINEER, ENGINEER).ok


def test_table_rows_inherit_the_citation_of_their_header_row() -> None:
    draft = replace(TEAM_LEAD, "**Top BGL alert categories by Log Entries [E1]**", "Breakdown:")
    draft = replace(draft, "| Category | Log Entries | Share |", "| Category | Log Entries [E1] | Share |")

    assert check(Role.TEAM_LEAD, draft).ok


def test_an_inherited_citation_does_not_excuse_a_wrong_number() -> None:
    draft = replace(TEAM_LEAD, "| 63,491 |", "| 63,941 |")

    assert "63,941" in only(check(Role.TEAM_LEAD, draft), "number_not_in_evidence")


def test_a_table_without_any_citation_is_uncited() -> None:
    draft = replace(TEAM_LEAD, "**Top BGL alert categories by Log Entries [E1]**", "Breakdown:")

    report = check(Role.TEAM_LEAD, draft)
    assert set(kinds(report)) == {"uncited_number"}
    assert any("152,734" in p.text for p in report.problems)


def test_a_citation_after_the_full_stop_belongs_to_the_sentence() -> None:
    draft = replace(TEAM_LEAD, "in the week of 2005-06-06 [E4].", "in the week of 2005-06-06. [E4]")

    assert check(Role.TEAM_LEAD, draft).ok


def test_grouped_citations_are_read_as_one_citation_each() -> None:
    draft = replace(
        MANAGER, "Data Scope: BGL, whole Dataset [E1][E8].", "Data Scope: BGL, whole Dataset [E1, E8]."
    )

    assert check(Role.MANAGER, draft).ok


@pytest.mark.parametrize(
    "fact",
    [
        "Between 2005-06-11 17:32:07 and 23:58:40 on R30-M0-N9-C:J16-U01 nothing else was logged [E7].",
        "Lines 222716\u2013222765 and line 222715 are on the same node [E2].",
        "Requests from 10.11.10.1 got HTTP 503 and status: 200, len: 323 [E6].",
        "Instance 3edec1e4-9678-4a3a-a21b-a145a4ee5e61 logged on 14 May and on June 3 [E6].",
        "SereneDB 26.10.1 ran it in the `count_by(top=25)` call on nova.api.v21 [E6].",
        "Template a0a0a0a0a0a0a001 (0x00004ed8) is the 2nd most common in 2005 [E1].",
        # #3 pilot answers the review round's stricter year and Next Step rules first flagged by mistake.
        "Jobs 65640 and 65641 failed together, and lines 201395 and 201396 delete the files [E2].",
        "It peaked on 5, 11 and 14 June 2005, again on 11\u201314 June and on 2005-06-13/14 [E2].",
        "The corpus only holds 2005\u2013early 2006 entries [E2].",
    ],
)
def test_identifiers_dates_and_times_are_not_measured_numbers(fact: str) -> None:
    draft = replace(ENGINEER, "## Hypotheses", f"- {fact}\n\n## Hypotheses")

    assert check(Role.ENGINEER, draft).ok, [p.model_dump() for p in check(Role.ENGINEER, draft).problems]


def test_parameters_in_the_next_step_need_no_citation() -> None:
    draft = replace(ENGINEER, "with before=25", "with before=25, after 40 and a 600 s window")

    assert check(Role.ENGINEER, draft).ok


def test_small_counts_need_no_evidence_but_incident_counts_do() -> None:
    small = replace(TEAM_LEAD, "## Next Step\n", "## Next Step\n- Start with the top 2 categories [E1].\n")
    assert check(Role.TEAM_LEAD, small).ok

    wrong_incidents = replace(TEAM_LEAD, "KERNDTLB is 3 Incidents [E3]", "KERNDTLB is 4 Incidents [E3]")
    assert kinds(check(Role.TEAM_LEAD, wrong_incidents)) == ["number_not_in_evidence"]


def test_a_percentage_may_quote_a_fraction_the_evidence_holds() -> None:
    assert "100% on its top node [E3]" in TEAM_LEAD  # top_node_share 1.0
    assert check(Role.TEAM_LEAD, TEAM_LEAD).ok


def test_a_relative_period_quotes_the_data_scope() -> None:
    draft = replace(ENGINEER, "of total memory [E6]", "of total memory over the last 30 days [E6]")

    assert check(Role.ENGINEER, draft).ok


# Every problem is actionable, and nothing raises ---------------------------------------------------


def test_every_problem_carries_its_section_the_offending_text_and_a_fix_hint() -> None:
    draft = replace(MANAGER, "152,734 Log Entries [E1]", "about 150k Log Entries [E2]")
    draft = replace(draft, "## Hypotheses\n", "## Hypotheses\n- This cost us $40,000. See [E12].\n")

    report = check(Role.MANAGER, draft)
    assert not report.ok
    assert len(report.problems) >= 4
    for problem in report.problems:
        assert problem.text.strip()
        assert len(problem.hint) > 20
        assert problem.section


@pytest.mark.parametrize(
    "draft",
    [
        "",
        "   \n\n",
        "## Observed Facts",
        "[E",
        "[E0] [E00] [E-1] [E99999999999999999999]",
        "| | |\n|---|\n| 1 |",
        "1e309 99999999999999999999999999 0.000000000001 % k M",
        "(derived from [E1]) (derived from) (derived from [E404])",
        "## Observed Facts\n" + "152,734 [E1] " * 2000,
        "\x00\x1b[31m \u2603 \U0001d7d9\U0001d7da\U0001d7db \u0661\u0662\u0663 \u00bd \u00b2 \u221e",
    ],
)
def test_a_malformed_draft_never_raises(draft: str) -> None:
    for role in Role:
        report = check(role, draft)
        assert isinstance(report, AnswerReport)
        assert report.ok is (not report.problems)


def test_random_drafts_never_raise() -> None:
    rng = random.Random(24)
    alphabet = string.printable + "\u2013\u2014~\u2248%$\u20ac[]()|#*_`"
    pieces = [
        "[E1]",
        "[E3]",
        "[E9]",
        "(derived from [E1])",
        "## Unknowns",
        "**Next Step**",
        "152,734",
        "43.83%",
    ]
    for _ in range(300):
        draft = "".join(
            rng.choice(pieces) if rng.random() < 0.1 else rng.choice(alphabet)
            for _ in range(rng.randint(0, 400))
        )
        report = check(rng.choice(list(Role)), draft)
        assert report.ok is (not report.problems)


def test_an_empty_ledger_says_to_call_a_tool_first() -> None:
    report = check_answer(Role.ENGINEER, ENGINEER, [])

    assert not report.ok
    assert "unknown_evidence" in kinds(report)
    assert any("empty" in p.hint for p in report.problems)


def test_a_rounded_share_is_told_how_to_quote_it() -> None:
    draft = replace(TEAM_LEAD, "| 18.22% |", "| about 18% |")

    found = only(check(Role.TEAM_LEAD, draft), "rounded_number")
    assert "share_pct" in found
    assert "(derived from [E1])" in found


def test_a_count_off_by_a_little_is_not_mistaken_for_a_sum() -> None:
    # 152,735 is 152,734 + 1, and 1 is in E1 (as a date part or a small count), but adding a negligible
    # number is not a computation anyone does: it is a wrong number.
    draft = replace(MANAGER, "152,734 Log Entries [E1]", "152,735 Log Entries [E1]")

    found = only(check(Role.MANAGER, draft), "number_not_in_evidence")
    assert "152,734" in found


# The helpers the eval's L2 grader uses (T17) ---------------------------------------------------------


def test_split_sections_returns_the_text_of_each_section() -> None:
    sections = split_sections("# Briefing\n" + TEAM_LEAD)

    assert list(sections) == [PREAMBLE, "Observed Facts", "Hypotheses", "Unknowns", "Next Step"]
    assert "Ownership Table is a stub" in sections["Unknowns"]
    assert "Briefing" in sections[PREAMBLE]


def test_numbers_in_skips_identifiers_and_reads_suffixes() -> None:
    text = "152,734 Log Entries on 2005-06-11 at R30-M0-N9-C:J16-U01 (line 222715), 43.83%, about 150k."

    found = numbers_in(text)

    assert [n.text for n in found] == ["152,734", "43.83%", "150k"]
    assert [n.value for n in found] == [152_734, 43.83, 150_000]
    assert [n.approximate for n in found] == [False, False, True]
    assert found[1].percent


def test_a_table_does_not_inherit_citations_across_a_section_heading() -> None:
    draft = replace(
        TEAM_LEAD,
        "## Unknowns\n",
        "- KERNSTOR is second [E1].\n## Unknowns\n"
        "| Group | Log Entries |\n|---|---|\n| KERNSTOR | 63,491 |\n",
    )  # the line above the heading cites [E1], but it belongs to Hypotheses, not to this table

    assert "uncited_number" in kinds(check(Role.TEAM_LEAD, draft))


# Review round 1 (#69) ---------------------------------------------------------------------------------


def hard_wrap(draft: str, width: int = 60) -> str:
    """`draft` with every prose line wrapped at `width`, list items continued with an indent."""
    out = []
    for line in draft.splitlines():
        if not line.strip() or line.lstrip().startswith(("|", "#")):
            out.append(line)
            continue
        marker = line[: len(line) - len(line.lstrip("-0123456789. "))]
        indent = " " * len(marker) if marker.strip() else ""
        out.append(textwrap.fill(line, width, subsequent_indent=indent, break_long_words=False))
    return "\n".join(out)


@pytest.mark.parametrize("role", list(Role))
def test_a_hard_wrapped_correct_answer_passes(role: Role) -> None:
    wrapped = hard_wrap(GOOD[role])
    assert wrapped.count("\n") > GOOD[role].count("\n")  # the wrap really split sentences

    report = check(role, wrapped)

    assert report.ok, [p.model_dump() for p in report.problems]


def test_a_sentence_wrapped_inside_a_paragraph_keeps_its_citation() -> None:
    draft = replace(
        TEAM_LEAD,
        "Data Scope: BGL, whole Dataset, alert categories [E1].",
        "Data Scope: BGL, whole Dataset. KERNDTLB holds 152,734 Log Entries, 43.83% of the 348,460\n"
        "matching entries [E1].",
    )

    assert check(Role.TEAM_LEAD, draft).ok


def test_a_blank_line_or_a_new_list_item_still_ends_the_sentence() -> None:
    paragraph = replace(
        TEAM_LEAD, "Data Scope: BGL, whole Dataset, alert categories [E1].", "KERNSTOR has 63,491\n\n[E1]."
    )
    bullet = replace(
        TEAM_LEAD,
        "- The top 3 categories",
        "- KERNSTOR has 63,491\n- Cited at last [E1].\n- The top 3 categories",
    )

    assert "uncited_number" in kinds(check(Role.TEAM_LEAD, paragraph))
    assert "uncited_number" in kinds(check(Role.TEAM_LEAD, bullet))


@pytest.mark.parametrize(
    "claim",
    [
        "The outage required 6 hours of recovery and hit paying customers.",
        "Its 43.83% share [E1] cost us real money.",
        "The outage record shows the service was down all afternoon.",
        "Three tickets say customers were affected by the downtime.",
        "We need to tell customers that this outage hit them.",
        "Provide the board with the EUR 240,000 loss figure.",
        "Downtime cost us dearly, and we should share that with finance.",
        "This is costly: ask yourself how many customers left.",
        "The storm caused an outage; whether that is acceptable is a business call.",
        "The outage cost us money, which the logs do not show.",
        "The outage cost us money, and the logs do not show it.",
        "Replacing the node would save USD 1.2M a year.",
        "The cluster was unavailable for 6 hours.",
        "The cluster was unavailable for six hours.",
        "The rack was down for 6 hours.",
        "It took 6 hours to recover.",
        "The service was degraded for most of the afternoon.",
    ],
)
def test_planted_manager_violations_are_rejected(claim: str) -> None:
    draft = replace(MANAGER, "## Hypotheses\n", f"## Hypotheses\n- {claim}\n")

    assert "not_measured_claim" in kinds(check(Role.MANAGER, draft))


@pytest.mark.parametrize(
    "refusal",
    [
        "For a cost figure, ask operations or finance for the outage window, affected jobs and staff hours.",
        "I ranked them by volume and severity, not by business impact.",
        "Get cost estimates and an impact assessment from the owning teams before committing budget.",
        "Those are the only way to estimate a cost.",
        "For a real SLA number, you'd need the SLA definitions and service-level records from the owning "
        "team.",
        "A high-volume day could be a burst of repeated messages and not an outage.",
        "None of them is labelled as an outage.",
        "Send me the SLA definition and its source, and I'll check whether the logs can support it.",
        'A search for "outage" found nothing.',
        # #3 pilot answers that an over-eager clause split flagged (lists, colons, "because", "how many").
        "It would not measure SLA compliance, because that needs a defined threshold, such as availability.",
        "The logs can't tell you either number: what it cost or how many hours customers were down.",
        "The logs can't tell us what June 2005 cost or how many hours of downtime customers saw.",
        "Ask the team that owns the BGL system for its incident records (start and end times, affected "
        "services, and any cost or SLA-credit figures).",
        "KERNDTLB Log Entries went down to 30 per week [E4].",
    ],
)
def test_requests_for_other_data_and_negated_mentions_still_pass(refusal: str) -> None:
    draft = replace(MANAGER, "## Hypotheses\n", f"## Hypotheses\n- {refusal}\n")

    report = check(Role.MANAGER, draft)
    assert report.ok, [p.model_dump() for p in report.problems]


def test_a_currency_first_amount_in_the_next_step_is_rejected() -> None:
    draft = replace(
        MANAGER,
        "## Next Step\n",
        "## Next Step\nApprove EUR 240,000 of budget to replace the failing node.\n",
    )

    found = only(check(Role.MANAGER, draft), "not_measured_claim")
    assert "EUR 240,000" in found


@pytest.mark.parametrize(
    "claim",
    ["The KERNDTLB storm cost about $40,000 in lost compute.", "The storm caused 6 hours of downtime."],
)
def test_the_team_lead_states_no_money_or_time_impact(claim: str) -> None:
    # T7's Team Lead playbook: "Avoid: ... money or time impact" (deferred from #63, nit 4).
    draft = replace(TEAM_LEAD, "## Hypotheses\n", f"## Hypotheses\n- {claim}\n")

    assert "not_measured_claim" in kinds(check(Role.TEAM_LEAD, draft))


def test_the_engineer_is_not_held_to_the_not_measured_terms() -> None:
    draft = replace(
        ENGINEER, "## Hypotheses\n", "## Hypotheses\n- The storm may have caused downtime for users.\n"
    )

    assert check(Role.ENGINEER, draft).ok


@pytest.mark.parametrize(
    ("fact", "number"),
    [("The storm touched 2024 midplanes [E3].", "2024"), ("KERNSTOR came from 2019 sources [E1].", "2019")],
)
def test_a_count_that_looks_like_a_year_is_still_checked(fact: str, number: str) -> None:
    draft = replace(ENGINEER, "## Hypotheses", f"- {fact}\n\n## Hypotheses")

    assert number in only(check(Role.ENGINEER, draft), "number_not_in_evidence")


@pytest.mark.parametrize(
    "fact",
    [
        "In 2005 KERNDTLB peaked on one node [E2].",
        "The data runs from 2005 to 2006 [E2].",
        "Nothing like it was logged since 2005, or in June 2005 [E2].",
        "Between 2005 and 2006 the node logged it daily [E2].",
    ],
)
def test_years_in_date_context_are_still_not_numbers(fact: str) -> None:
    draft = replace(ENGINEER, "## Hypotheses", f"- {fact}\n\n## Hypotheses")

    assert check(Role.ENGINEER, draft).ok, [p.model_dump() for p in check(Role.ENGINEER, draft).problems]


def test_a_measurement_in_the_next_step_is_checked() -> None:
    draft = replace(
        MANAGER, "## Next Step\n", "## Next Step\n- Investigate the 9,999 KERNMC Log Entries first.\n"
    )

    assert "9,999" in only(check(Role.MANAGER, draft), "uncited_number")


@pytest.mark.parametrize(
    "step",
    [
        "Re-run list_incidents with gap_s: 600.",
        "Call `count_by(top=40)` next.",
        "Check the top 3 nodes first.",
        "Search `data storage interrupt` with `start=2005-06-13T17:36:00`, using a higher limit (up to 50).",
    ],
)
def test_parameters_in_the_next_step_stay_exempt(step: str) -> None:
    draft = replace(ENGINEER, "## Next Step\n", f"## Next Step\n- {step}\n")

    assert check(Role.ENGINEER, draft).ok


@pytest.mark.parametrize("fact", ["The storm hit 7 midplanes [E3].", "The storm hit 5 nodes [E3]."])
def test_a_small_spread_number_is_checked(fact: str) -> None:
    draft = replace(TEAM_LEAD, "- Weekly,", f"- {fact}\n- Weekly,")

    assert only(check(Role.TEAM_LEAD, draft), "number_not_in_evidence")


def test_a_small_number_in_a_spread_or_incident_column_is_checked() -> None:
    draft = replace(
        TEAM_LEAD,
        "- Weekly,",
        "\n| Incident | Nodes | Midplanes |\n|---|---|---|\n| 17 | 1 | 7 |\n\nSource: [E3]\n\n- Weekly,",
    )

    assert "7" in only(check(Role.TEAM_LEAD, draft), "number_not_in_evidence")


def test_an_unknown_role_comes_back_as_a_problem_not_an_exception() -> None:
    report = check_answer("boss", TEAM_LEAD, LEDGER)

    assert not report.ok
    assert "team_lead" in only(report, "checker_error")


def test_a_count_after_an_entry_id_is_still_checked() -> None:
    draft = replace(
        ENGINEER, "entry 222715 is the first one shown [E2]", "entry 222715, 26,999 more follow [E2]"
    )

    assert "26,999" in only(check(Role.ENGINEER, draft), "number_not_in_evidence")
