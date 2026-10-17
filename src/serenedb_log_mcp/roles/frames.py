"""The answer frame of each Role and the Manager's not-measured list (#4, plan section 2.3).

Every Evidence result carries the asking Role's `answer_frame`: the four sections of an answer, in
order, each with what that Role puts in it. The sections are the same for every Role (decisions.md,
Evidence Ledger); what goes in them, and above all the kind of Next Step, differs (GLOSSARY.md, Next
Step: a check for the Engineer, an investigation priority for the Team Lead, a prioritised action
for the Manager).

Each frame line starts with its section name and a colon, so `verify_answer` (T12) and the eval's
L2 grader can read the section names back with `line.split(":", 1)[0]`.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from serenedb_log_mcp.config import Role

SECTIONS: tuple[str, ...] = ("Observed Facts", "Hypotheses", "Unknowns", "Next Step")
"""The four sections of every answer, in the order they must appear."""

NEXT_STEP_KIND: Mapping[Role, str] = MappingProxyType(
    {
        Role.ENGINEER: "a check",
        Role.TEAM_LEAD: "an investigation priority",
        Role.MANAGER: "a prioritised action",
    }
)
"""The kind of Next Step each Role gets (GLOSSARY.md, Next Step)."""

_GUIDANCE: Mapping[Role, tuple[str, str, str, str]] = MappingProxyType(
    {
        Role.ENGINEER: (
            "what failed (Component, node, request or instance, timestamp) and what happened just "
            "before and after, with the key Log Entries quoted and [E<n>] next to every number and line",
            "possible causes the Evidence is consistent with, each with what would confirm or rule it "
            "out; Log Entries before a failure are candidates, not causes",
            "what these logs cannot establish about this failure",
            f"{NEXT_STEP_KIND[Role.ENGINEER]}. One to three concrete checks: an exact tool call with "
            "its parameters, or a record or command to inspect on the named node",
        ),
        Role.TEAM_LEAD: (
            "the recurring problems over the stated period with exact counts, the trend against the "
            "baseline, and the Components and Teams involved, with [E<n>] next to every number",
            "links between problems, stated as associations, not causes",
            "unmapped Teams, gaps in the period, and what the logs cannot show",
            f"{NEXT_STEP_KIND[Role.TEAM_LEAD]}. Which problem to investigate first, the owning Team, "
            "and the measured factor that put it first",
        ),
        Role.MANAGER: (
            "a one-sentence bottom line, then 3 to 5 problems ranked by measured frequency and "
            "affected Components, with [E<n>] next to every number and the Data Scope; no raw log "
            "lines, node names or SQL",
            "why the order could change (ties, another weighting), labelled as a Hypothesis",
            "always present: name what not_measured lists (cost, downtime, customer impact) as not "
            "measured by these logs, plus any gap in the data",
            f"{NEXT_STEP_KIND[Role.MANAGER]}. One to three decisions or actions in priority order, "
            "with who acts; not a technical fix",
        ),
    }
)

NOT_MEASURED: tuple[str, ...] = (
    "cost or financial loss: these logs hold no prices, billing or job accounting",
    "downtime or availability: these logs hold no uptime or outage records",
    "customer or user impact: these logs name no customers, users or sessions",
    "SLA or SLO breaches: these logs hold no service-level targets",
    "root cause: the order of Log Entries shows what came first, not why; state a cause as a Hypothesis",
)
"""What the Manager must not claim from any Evidence (OrFrederick's #4 Research; challenge brief:
the Manager must not invent financial loss, downtime or customer impact). Each item is the term,
a colon and why these logs cannot measure it."""


def answer_frame(role: Role) -> list[str]:
    """The four sections of an answer for `role`, each as '<Section>: <what goes in it>'."""
    return [f"{section}: {guidance}" for section, guidance in zip(SECTIONS, _GUIDANCE[role], strict=True)]


def not_measured(role: Role) -> list[str]:
    """What `role` must not claim from Evidence: the not-measured list for the Manager, else nothing."""
    return list(NOT_MEASURED) if role is Role.MANAGER else []
