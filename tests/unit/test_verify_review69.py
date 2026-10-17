"""Regression suites from the #69 reviews of `verify_answer` (T12).

Round 1 is OrFrederick's (R01-R19); round 2 is the leader's fresh-context review (/tmp/review69: adv.py,
adv2.py, crash.py, refusal.md). Every BAD draft must be rejected and every GOOD draft accepted. Cases
the fix deliberately leaves to a follow-up are strict xfails with the reason, so fixing one later
turns its test red until the mark is removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from verify_drafts import ENGINEER, MANAGER, TEAM_LEAD, hard_wrap, put
from verify_ledger import ledger_records

from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.verify.checks import check_answer

LEDGER = ledger_records()
M, TL, EN = Role.MANAGER, Role.TEAM_LEAD, Role.ENGINEER
H, OF, NS, UN = "Hypotheses", "Observed Facts", "Next Step", "Unknowns"


def deferred(reason: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=f"deferred to a follow-up: {reason}")


BAD = [
    # Round 1 (OrFrederick)
    ("R01", M, put(M, H, "The outage required 6 hours of recovery and hit paying customers.")),
    ("R02", M, put(M, H, "Its 43.83% share [E1] cost us real money.")),
    ("R03", M, put(M, H, "The outage record shows the service was down all afternoon.")),
    ("R04", M, put(M, H, "Three tickets say customers were affected by the downtime.")),
    ("R05", M, put(M, H, "We need to tell customers that this outage hit them.")),
    ("R06", M, put(M, H, "Provide the board with the EUR 240,000 loss figure.")),
    ("R07", M, put(M, H, "Downtime cost us dearly, and we should share that with finance.")),
    ("R08", M, put(M, H, "This is costly: ask yourself how many customers left.")),
    ("R09", M, put(M, H, "The storm caused an outage; whether that is acceptable is a business call.")),
    ("R10", M, put(M, NS, "Approve EUR 240,000 of budget to replace the failing node.")),
    ("R11", M, put(M, H, "The cluster was unavailable for 6 hours.")),
    ("R12", M, put(M, H, "The rack was down for 6 hours.")),
    ("R13", M, put(M, H, "It took 6 hours to recover.")),
    ("R14", M, put(M, H, "The service was degraded for most of the afternoon.")),
    ("R15", EN, put(EN, OF, "The storm touched 2024 midplanes [E3].")),
    ("R16", EN, put(EN, OF, "KERNSTOR came from 2019 sources [E1].")),
    ("R17", M, put(M, NS, "Investigate the 9,999 KERNMC Log Entries first.")),
    ("R18", TL, put(TL, OF, "The storm hit 7 midplanes [E3].")),
    ("R19", TL, put(TL, OF, "The storm hit 5 nodes [E3].")),
    # Round 2: money, downtime and customers in varied wording (adv.py)
    ("N01", M, put(M, H, "KERNDTLB likely cost around \u20ac150k in lost compute.")),
    ("N02", M, put(M, H, "The storm probably set us back roughly $1.2M.")),
    ("N03", M, put(M, H, "That is about 2.5 million euros of exposure.")),
    ("N04", M, put(M, H, "Users could not log in for six hours.")),
    ("N05", M, put(M, H, "We lost several days of compute time to the KERNDTLB storm.")),
    ("N06", M, put(M, H, "The machine sat idle for most of a week while KERNDTLB fired.")),
    ("N07", M, put(M, H, "The storm was expensive; the business cannot afford a repeat.")),
    ("N08", M, put(M, H, "KERNMNTF burned through a large part of this quarter's budget.")),
    ("N09", M, put(M, H, "Paying subscribers were hit hardest by the mount failures.")),
    ("N10", M, put(M, H, "Our clients noticed the slowdown immediately.")),
    ("N11", M, put(M, NS, "Fix KERNMNTF first because it is losing us sales every week.")),
    ("N12", M, put(M, NS, "Ask finance to book the EUR 50k loss against the storage budget.")),
    ("N13", M, put(M, H, "Each failure costs the company 4,000 GBP.")),
    ("N14", M, put(M, H, "The service went offline for half a day.")),
    ("N15", M, put(M, H, "Jobs failed for 3 days, delaying research results for our tenants.")),
    ("N16", M, put(M, H, "The KERNDTLB storm led to a 14-hour outage.")),
    ("N17", M, put(M, H, "The outage was not short: it lasted all night.")),
    ("N18", M, put(M, H, "No one can deny that customers suffered from the storm.")),
    (
        "N19",
        M,
        put(M, H, "Users were not able to submit jobs, which cost us money that the logs don't show."),
    ),
    ("N20", M, put(M, H, "It cost us 40 hours of engineer time; the logs do not show more.")),
    ("N21", TL, put(TL, H, "The storm cost the team two full days of downtime.")),
    ("N22", M, put(M, OF, "KERNDTLB caused 2.5 hours of service interruption [E1].")),
    ("N23", M, put(M, H, "The KERNDTLB storm meant lost productivity worth \u00a330,000.")),
    # Round 2: numbers (adv.py)
    ("X01", M, put(M, OF, "KERNDTLB holds about 150k Log Entries [E1].")),
    ("X02", EN, put(EN, OF, "BGL holds ~4.7M Log Entries [E5].")),
    pytest.param(
        "X03",
        TL,
        put(TL, OF, "KERNDTLB is roughly half of all alert Log Entries [E1]."),
        marks=deferred("fractions written as words (half, a third) are not numbers yet"),
    ),
    ("X04", TL, put(TL, OF, "KERNDTLB and KERNSTOR together hold 216,225 Log Entries [E1].")),
    ("X05", TL, put(TL, OF, "KERNDTLB is 57.45% of the top three categories [E1].")),
    ("X06", TL, put(TL, H, "The storm wrote 152,734 lines on one node.")),
    ("X07", TL, put(TL, OF, "KERNSTOR holds 64,211 Log Entries [E1].")),
    ("X08", TL, put(TL, OF, "KERNSTOR holds 63,491 Log Entries [E42].")),
    ("X09", TL, put(TL, OF, "KERNDTLB amounts to 152,734 incidents [E1].")),
    ("X10", TL, put(TL, OF, "KERNSTOR had two incidents in June [E1].")),
    (
        "X11",
        TL,
        TEAM_LEAD.replace("## Unknowns\n- Which Team owns KERNEL: the Ownership Table is a stub.\n\n", ""),
    ),
    (
        "X12",
        TL,
        TEAM_LEAD.replace("## Hypotheses", "## HYPX")
        .replace("## Next Step", "## Hypotheses")
        .replace("## HYPX", "## Next Step"),
    ),
    ("X13", TL, put(TL, OF, "KERNDTLB and KERNSTOR hold 216,000 Log Entries (derived from [E1]).")),
    pytest.param(
        "X14",
        M,
        put(M, NS, "Run count_by to confirm the 9,999 failing jobs."),
        marks=deferred("a tool name in Next Step still shields a number (the tool-name shield)"),
    ),
    ("X15", M, put(M, NS, "Start with KERNMNTF: it hit 1,500 midplanes last week.")),
    ("X16", TL, put(TL, OF, "The storm reached 2031 Nodes [E3].")),
    pytest.param(
        "X17",
        TL,
        put(TL, OF, "KERNMNTF hit 83 racks [E8]."),
        marks=deferred("a number is matched against the whole cited record, not its column"),
    ),
    ("X18", TL, put(TL, OF, "KERNDTLB is 3.22% of all BGL Log Entries [E1].")),
    ("X19", TL, put(TL, OF, "The top node took a 1% share of entries in Incident 18 [E3].")),
    ("X20", M, put(M, OF, "KERNDTLB is a dozen incidents worth watching [E1].")),
    ("X21", TL, put(TL, NS, "Look at the top 9 nodes, which logged 152,999 entries.")),
    ("X22", TL, put(TL, OF, "KERNDTLB grew 40% week over week [E4].")),
    ("X23", TL, put(TL, OF, "KERNDTLB had nearly 153 thousand Log Entries [E1].")),
    (
        "X24",
        TL,
        put(
            TL, OF, "KERNSTOR fired 63,491 times in 2005 across 1103 nodes, ten times more than APPSEV [E1]."
        ),
    ),
    ("X25", M, put(M, NS, "Escalate: `9999` KERNMC Log Entries need review.")),
    ("X26", TL, put(TL, OF, "KERNDTLB: 152,734 Log Entries [E9].")),
    ("X27", M, "## Observed Facts\nKERNDTLB holds 152,734 Log Entries [E1].\n\n## Next Step\nAsk finance.\n"),
    # Round 2: amounts in Unknowns, imperatives, negations, vocabulary (adv2.py)
    (
        "U1",
        M,
        put(M, UN, "The storm cost about EUR 240,000 in lost compute, though finance has not confirmed it."),
    ),
    ("U2", M, put(M, UN, "Customer impact: about 3,000 users were affected.")),
    ("U3", M, put(M, UN, "Downtime: the cluster was unavailable for 14 hours.")),
    ("U4", TL, put(TL, UN, "The storm cost $85,000.")),
    ("U5", M, put(M, UN, "Cost: \u20ac1.2M, downtime 14 hours, 3,000 customers.")),
    ("U6", M, put(M, H, "The order could change.\nUnknowns: the storm cost about EUR 240,000.")),
    ("U7", M, put(M, H, "The order could change.\nUnknowns: the outage hit customers hard.")),
    ("C1", M, put(M, NS, "Send customers an apology for the outage.")),
    ("C2", M, put(M, NS, "Confirm with the board that the outage cost us customers.")),
    ("C3", M, put(M, NS, "Ask the board to approve compensation for the affected customers.")),
    ("C4", M, put(M, NS, "Get ready for customer complaints about the outage.")),
    ("C5", M, put(M, H, "The storm cost us money that these logs don't show.")),
    ("C6", M, put(M, H, "The service went offline.")),
    ("C7", M, put(M, H, "KERNMNTF took storage offline overnight.")),
    ("C8", M, put(M, H, "KERNDTLB caused a long service interruption.")),
    ("C9", M, put(M, H, "The cluster crashed and stayed dark overnight.")),
    ("C10", M, put(M, H, "Check: the outage hit customers hard.")),
    ("C11", M, put(M, OF, "Without a doubt, the outage hit customers [E8].")),
    ("C12", M, put(M, H, "Nobody doubts that the downtime was long.")),
    ("C13", M, put(M, H, "There is no question the storm caused downtime; the data shows it.")),
    ("C14", M, put(M, H, "The logs show the outage cost us money.")),
    ("C15", M, put(M, H, "It is not cheap: the storm cost real money, as the logs show.")),
    ("C16", M, put(M, H, "No customer escaped the outage, and the logs show it.")),
    ("C17", M, put(M, H, "The downtime was never shorter than a day, based on the logs.")),
]

REFUSAL = (Path(__file__).parents[1] / "support" / "verify_refusal.md").read_text(encoding="utf-8")
"""The refusal-moment answer from the round-2 review (Differentiator 4): it must pass."""

GOOD = [
    ("G01", M, put(M, H, "The logs do not measure cost; ask finance.")),
    ("G02", M, put(M, NS, "Ask finance for the cost of the KERNDTLB storm; these logs do not record it.")),
    (
        "G03",
        M,
        put(M, H, "These logs cannot tell us whether customers noticed; ask the service desk for tickets."),
    ),
    ("G04", TL, put(TL, OF, "KERNDTLB and KERNSTOR together hold 216,225 Log Entries (derived from [E1]).")),
    ("G05", TL, put(TL, OF, "KERNDTLB is 57.45% of the top three categories (derived from [E1]).")),
    ("G06", EN, put(EN, OF, "The host reported 64172 MB of total memory and 512.00 MB used [E6].")),
    ("G07", M, hard_wrap(MANAGER, 45)),
    ("G08", TL, hard_wrap(TEAM_LEAD, 40)),
    (
        "G09",
        TL,
        TEAM_LEAD.replace(
            "**Top BGL alert categories by Log Entries [E1]**",
            "Top BGL alert categories by Log Entries,\nwhole Dataset, as count_by returned them [E1]:",
        ),
    ),
    (
        "G10",
        TL,
        put(
            TL,
            OF,
            "Incident detail [E3]:\n\n| Incident | Log Entries | Nodes | Midplanes |\n"
            "|---|---|---|---|\n| 17 | 152,182 | 1 | 1 |\n| 18 | 400 | 380 | 44 |\n| 19 | 152 | 150 | 30 |\n",
        ),
    ),
    ("G11", M, put(M, H, "KERNDTLB's share is 43.83% of alert Log Entries [E1], so it dominates by volume.")),
    ("G12", M, put(M, H, "The KERNDTLB storm was one node, not a machine-wide outage [E3].")),
    ("G13", M, put(M, NS, "Run top_patterns with top=5 and weeks=4 next quarter.")),
    ("G14", TL, put(TL, OF, "From June 2005 to January 2006, KERNDTLB logged 152,734 Log Entries [E1].")),
    ("G15", M, put(M, H, "Whether the storm affected customers is not something these logs record.")),
    ("G16", M, put(M, H, "No downtime figure is available from these logs.")),
    ("G17", EN, put(EN, OF, "KERNDTLB \u2013 152,734 Log Entries [E1] \u2014 43.83 % of alerts [E1].")),
    (
        "G18",
        M,
        put(
            M,
            NS,
            "If cost matters, ask finance to price one node-day; the logs give 152,734 Log Entries [E1].",
        ),
    ),
    ("G19", TL, put(TL, OF, "The top node held 100% of the largest Incident [E3].")),
    (
        "G20",
        M,
        put(M, H, "These logs do not show the business impact; KERNMNTF ranks first on Incidents [E8]."),
    ),
    ("G21", M, put(M, H, "Treat this as a risk ranking, not a cost estimate.")),
    ("G22", M, put(M, H, "I cannot say what the storm cost.")),
    ("G23", M, put(M, H, "Any downtime estimate needs data from outside these logs.")),
    (
        "G24",
        TL,
        put(TL, OF, "The weekly trend fell from 152,252 to 30 Log Entries between the first two weeks [E4]."),
    ),
    ("G25", M, REFUSAL),
    (
        "G26",
        M,
        put(M, UN, "Cost: these logs hold no prices; the finance figures for 2005 are not in the corpus."),
    ),
    ("G27", M, put(M, OF, "KERNDTLB holds 152,734 Log Entries. [E1] KERNSTOR holds 63,491 [E1].")),
]

# #3 pilot refusals that the round-2 scoping first misflagged; each must still pass.
PILOT_REFUSALS = [
    "They contain no cost, downtime, or impact data.",
    "The logs have no downtime or customer-impact data, so I can't say how much downtime customers had.",
    "For a Manager, I'd report that the logs show 2 Log Entries and no measurable downtime.",
    "OpenStack has no SLA text.",
    "The logs have no SLA field, SLA target or breach marker.",
    "These are counts of Log Entries, not incidents or outages.",
    "They hold no financial, availability or customer data, so I won't give either number.",
    "To get cost and downtime figures, ask the operations or finance team for incident tickets, uptime/SLA "
    "reports.",
    "**Cost:** The logs contain no cost, billing, or financial fields, so this cannot be established from "
    "them.",
    "**Customer downtime:** The logs have no customer-facing availability measure.",
    "This is a hypothesis about priority, not evidence of an outage.",
    "Then bring the cost and customer-downtime questions to finance and the service-availability owners.",
    "They contain no cost data and no record of customer-facing downtime, so I can only report counts.",
    "For cost and downtime, check the finance records and the service-availability or ticketing systems.",
    "**Customer downtime hours:** The logs contain no customer or availability data, so I cannot establish "
    "downtime.",
    "Cost and customer impact will need data from finance or the service owners, since the logs cannot "
    "supply them.",
    "The logs show how often each label appears, not what each event cost.",
    "The logs hold Log Entries, and nothing in them marks where one incident starts or ends.",
    "Common options are one incident per node per time window.",
]


@pytest.mark.parametrize("sentence", PILOT_REFUSALS)
def test_pilot_refusals_still_pass(sentence: str) -> None:
    report = check_answer(M, put(M, H, sentence), LEDGER, incident_tools=True)

    assert report.ok, [p.model_dump() for p in report.problems]


@pytest.mark.parametrize(
    "claim",
    [
        "Cost: high.",
        "The storm did not cause downtime.",
        "Downtime: 14 hours.",
        "KERNSTOR had two incidents [E1].",
    ],
)
def test_the_refusal_forms_do_not_excuse_a_claim(claim: str) -> None:
    assert not check_answer(M, put(M, H, claim), LEDGER, incident_tools=True).ok


def ids(cases: list) -> list[str]:  # type: ignore[type-arg]
    return [c.values[0] if hasattr(c, "values") else c[0] for c in cases]


@pytest.mark.parametrize(("case", "role", "draft"), BAD, ids=ids(BAD))
def test_review_violations_are_rejected(case: str, role: Role, draft: str) -> None:
    report = check_answer(role, draft, LEDGER, incident_tools=True)

    assert not report.ok, f"{case} was accepted"
    assert "checker_error" not in [p.kind for p in report.problems]


@pytest.mark.parametrize(("case", "role", "draft"), GOOD, ids=ids(GOOD))
def test_review_correct_answers_pass(case: str, role: Role, draft: str) -> None:
    report = check_answer(role, draft, LEDGER, incident_tools=True)

    assert report.ok, [p.model_dump() for p in report.problems]


@pytest.mark.parametrize(
    ("role", "draft", "ledger"),
    [
        ("manager", None, LEDGER),
        ("manager", b"## Observed Facts", LEDGER),
        ("manager", MANAGER, None),
        (
            "manager",
            MANAGER,
            [
                1,
                "x",
                None,
                {"evidence_id": 5},
                {"evidence_id": "E1", "rows": "x", "summary": {"x": float("nan")}},
            ],
        ),
        ("manager", MANAGER, [*LEDGER, {"evidence_id": "E99", "tool": "count_by", "rows": [[10**400]]}]),
        (3, MANAGER, LEDGER),
        (None, MANAGER, LEDGER),
        ("manager", MANAGER + "\ud800", LEDGER),
        ("manager", "```\n" + MANAGER, LEDGER),
    ],
    ids=[
        "none-draft",
        "bytes-draft",
        "none-ledger",
        "junk-ledger",
        "huge-int-ledger",
        "int-role",
        "none-role",
        "lone-surrogate",
        "unclosed-fence",
    ],
)
def test_malformed_input_never_raises(role: object, draft: object, ledger: object) -> None:
    report = check_answer(role, draft, ledger)  # type: ignore[arg-type]

    assert report.ok is (not report.problems)
    assert not any("itself failed" in p.hint for p in report.problems)


@pytest.mark.parametrize("digits", ["9" * 5000, "1" + "0" * 400])
def test_a_number_too_large_for_a_float_is_not_in_evidence(digits: str) -> None:
    report = check_answer(M, MANAGER.replace("26,426", digits), LEDGER)

    assert [p.kind for p in report.problems] == ["number_not_in_evidence"]


def test_the_engineer_base_draft_still_passes() -> None:
    assert check_answer(EN, ENGINEER, LEDGER).ok


def test_one_label_per_line_still_reads_as_four_sections() -> None:
    compact = (
        "Observed Facts: KERNDTLB holds 152,734 Log Entries in 3 Incidents [E8].\n"
        "Hypotheses: one node may be looping.\n"
        "Unknowns: cost, downtime and customer impact are not measured by these logs.\n"
        "Next Step: ask the node owners to check the KERNDTLB node.\n"
    )

    report = check_answer(M, compact, LEDGER)
    assert report.ok, [p.model_dump() for p in report.problems]
