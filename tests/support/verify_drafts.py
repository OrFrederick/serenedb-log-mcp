"""Correct answers over the fixture Evidence Ledger, one per Role, for the answer-check tests (T12).

Each is a draft that `verify.checks.check_answer` must accept against `verify_ledger.ledger_records()`;
the tests break it in one place at a time. Shared by the unit tests and the review regression suites,
and meant for T17's L2 grader tests too.
"""

from __future__ import annotations

import textwrap

from serenedb_log_mcp.evidence import Role

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


def put(role: Role, section: str, sentence: str) -> str:
    """The correct answer for `role` with `sentence` added as the first bullet of `section`."""
    head = f"## {section}\n"
    draft = GOOD[role]
    assert head in draft, head
    return draft.replace(head, f"{head}- {sentence}\n", 1)


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
