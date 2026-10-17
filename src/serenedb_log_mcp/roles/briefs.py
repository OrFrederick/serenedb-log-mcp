"""The Role playbooks: one text per Role, served as an MCP prompt and by `get_role_brief` (#4).

Each playbook sets the questions the Role asks, the tools and Evidence it uses, what it avoids and
the four answer sections with the Role's kind of Next Step. The content follows the per-Role answer
templates of both #4 Research comments (OrFrederick's Role templates and guard rails, NoNaim95's
contracts and refusal wording). The answer sections are the Role's `answer_frame` lines verbatim and
the Manager's not-measured items are `NOT_MEASURED` verbatim, so the playbook, every Evidence result
and `verify_answer` (T12) can never disagree.

Tools that land in later tickets (top_patterns, list_incidents, verify_answer) are named with "if
listed", so the playbooks stay true while the server grows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.roles.frames import NOT_MEASURED, answer_frame

TITLES: Mapping[Role, str] = MappingProxyType(
    {Role.ENGINEER: "Engineer", Role.TEAM_LEAD: "Team Lead", Role.MANAGER: "Manager"}
)

_PURPOSE: Mapping[Role, str] = MappingProxyType(
    {
        Role.ENGINEER: "You investigate one failure: what failed, in which Component and on which node, "
        "request or instance, and what happened just before and just after.",
        Role.TEAM_LEAD: "You look for recurring problems over a stated period: which recur, how often, "
        "whether they are rising, which Components and Teams they involve, and what to investigate first.",
        Role.MANAGER: "You decide which observed problems deserve attention first, using measured "
        "frequency and affected Components only.",
    }
)

_QUESTIONS: Mapping[Role, tuple[str, ...]] = MappingProxyType(
    {
        Role.ENGINEER: (
            "What failed: the Component, level, node (BGL) or request and instance (OpenStack), and the "
            "first and last timestamp.",
            "What happened just before and just after, on the same source and on every source nearby; "
            '"nothing else was logged" is a finding too.',
            "Which possible cause the Evidence is consistent with, and what would confirm or rule it out.",
        ),
        Role.TEAM_LEAD: (
            "Which problems recur in the period, with exact counts of Log Entries and, where measured, "
            "of Incidents.",
            "Whether each one is rising, steady, a one-off burst or gone: the period against its baseline.",
            "Which Components and Teams (Ownership Table) are involved; an unmapped Component is an Unknown.",
            "What to investigate first, and which Team owns it.",
        ),
        Role.MANAGER: (
            "Which observed problems deserve attention first, and why in that order.",
            "How widespread (Components, Teams, sources) and how persistent (active weeks, still active) "
            "each one is.",
            "What decision is asked of you.",
        ),
    }
)

_WORKFLOW: Mapping[Role, tuple[str, ...]] = MappingProxyType(
    {
        Role.ENGINEER: (
            "describe_corpus once, for the Datasets, time ranges and valid filter values.",
            'search_logs with words, a "quoted phrase" or a Pattern filter (alert_categories, '
            "pattern_ids) and a time range, to find the failing Log Entries; read total_matches and the "
            "facets in summary, not only the rows.",
            "get_context(entry_id) on the first failing Log Entry: its node (BGL) or request or instance "
            "(OpenStack), then scope='window' for what other sources logged at the same time.",
            "count_by on that node, Component or a short window, to say whether this is one Log Entry or "
            "many and whether it is unusual against the baseline.",
        ),
        Role.TEAM_LEAD: (
            "describe_corpus once, then state the period. A relative period such as time_from='last 30d' "
            "resolves against the Dataset's last Log Entry, not today.",
            "count_by with group_by alert_category, pattern, component or team over the period; every "
            "row carries a baseline and a normal or unusual flag.",
            "trend per day or week for the top groups (at most 5 series): rising, steady, one-off or gone.",
            "top_patterns and list_incidents, if listed, to tell recurring problems from single bursts, "
            "with Incidents and Spread next to the Log Entry counts.",
            "search_logs only for one to three sample lines per problem: this Role receives at most 3 rows, "
            "a sample, not a frequency.",
        ),
        Role.MANAGER: (
            "describe_corpus once, then state the Data Scope (Datasets and period).",
            "count_by with group_by dataset or team over the whole Base Corpus, and alert_category or "
            "component per Dataset; trend per week to see whether a problem is still active.",
            "top_patterns (Manager priority) and list_incidents, if listed, for Incidents and Spread. "
            "Volume is not priority: many Log Entries from one node can matter less than a few on most "
            "of the machine.",
            "This Role receives no raw Log Entries: search_logs, get_context and get_evidence return "
            "total_matches and the facets, without rows.",
        ),
    }
)

_ALLOWED: Mapping[Role, str] = MappingProxyType(
    {
        Role.ENGINEER: "raw Log Entries, nearby events, full-text hits and small local counts. Quote the "
        'key Log Entries verbatim with their timestamps, and say "N of M shown" when a result is Truncated.',
        Role.TEAM_LEAD: "counts, trends, baselines, Incidents, co-occurrence, and at most 3 sample Log "
        "Entries per problem.",
        Role.MANAGER: "aggregates and rankings only. No raw log lines, node names or SQL in the answer body.",
    }
)

_AVOID: Mapping[Role, str] = MappingProxyType(
    {
        Role.ENGINEER: "corpus-wide rankings, business impact, and a cause stated as fact.",
        Role.TEAM_LEAD: "line-by-line timelines, a frequency read from sample rows, and money or time "
        "impact.",
        Role.MANAGER: 'any estimate of the items below, "incidents" for Log Entries, and adjectives beyond '
        'the logged level (say "logged at FATAL level", never "critical outage").',
    }
)

EXAMPLE_QUESTIONS: Mapping[Role, tuple[str, str]] = MappingProxyType(
    {
        Role.ENGINEER: (
            "What failed in the first /p/gb1 mount failure on BGL, and what happened just before it?",
            "What happened on node R27-M0-NA-C:J11-U11 around 03:25 on 2006-01-03?",
        ),
        Role.TEAM_LEAD: (
            "Which problems recurred over the last 30 days of BGL, and which Teams own them?",
            "Is the KERNMNTF mount failure rising or falling week by week?",
        ),
        Role.MANAGER: (
            "Which observed problems deserve attention first?",
            "Which problems are widespread and still active, and which were one-off bursts?",
        ),
    }
)
"""Questions the prompts offer when the user has not asked one yet, drawn from #4 and #5."""

NOT_MEASURED_INTRO = (
    "These logs do not measure the following, so never state or estimate them (every result repeats "
    "them in not_measured):"
)
REFUSAL = (
    'If asked about one of them, say: "This is not measurable from these logs; any figure would be '
    'invented." Then give the measured alternative (Log Entries, Incidents, affected Components and '
    "Teams, active weeks, each with [E<n>]) and name the data that would answer it, such as billing, "
    "uptime monitoring or support tickets."
)
EVERY_ROLE = (
    "For every Role: cite [E<n>] next to every number, quote it exactly as the result gives it (never "
    "round, never add up by hand; use the summary values), and state its Data Scope. Log Entries are not "
    'Incidents: never call matching Log Entries incidents. Tools named here with "if listed" may not be '
    "offered by this server yet; skip them when they are missing. If verify_answer is listed, call it "
    "with your draft and fix every problem it reports before you answer."
)


def role_brief(role: Role) -> str:
    """The playbook of `role`, the same text as its MCP prompt and `get_role_brief`."""
    lines = [
        f"Role: {TITLES[role]}. {_PURPOSE[role]}",
        "",
        "Questions this Role asks:",
        *_bullets(_QUESTIONS[role]),
        "",
        f"How to work (pass role='{role.value}' in every tool call):",
        *(f"{n}. {step}" for n, step in enumerate(_WORKFLOW[role], start=1)),
        "",
        f"Allowed Evidence: {_ALLOWED[role]}",
        f"Avoid: {_AVOID[role]}",
    ]
    if role is Role.MANAGER:
        lines += ["", NOT_MEASURED_INTRO, *_bullets(NOT_MEASURED), REFUSAL]
    lines += [
        "",
        "Answer in four sections, in this order:",
        *_bullets(answer_frame(role)),
        "",
        EVERY_ROLE,
    ]
    return "\n".join(lines)


def _bullets(items: Sequence[str]) -> list[str]:
    return [f"- {item}" for item in items]
