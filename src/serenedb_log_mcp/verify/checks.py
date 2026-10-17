"""The answer checks shared by `verify_answer` and the eval's L2 grader (T12, plan section 2.7).

One pure module, so the tool the agent calls and the grader that scores the agent can never disagree.
`check_answer(role, draft, ledger)` reads a draft answer and the Evidence Ledger records and returns
every problem it finds, each with the offending text and a fix hint. It never raises on a bad draft.

What it checks (decisions.md: Evidence Ledger, Differentiator 3; #3, #4 and #1 Decision proposals):

- Citations: every `[E<n>]` resolves in the ledger, and Observed Facts cite Evidence at all.
- Numbers: every number in a sentence that cites Evidence appears in that Evidence: its rows
  (including numbers inside text cells, such as "Total memory: 64172 MB"), summary, totals,
  arguments, bound parameters and Data Scope. A number that is not there is accepted only in a
  sentence marked "(derived from [E1],[E2])", and only when it recomputes from those records as a
  sum, a difference or a ratio of two of their numbers, a column total or a running total of a
  column. A number with no citation in its sentence is a problem everywhere except in Next Step,
  which holds suggested parameters rather than measurements.
- The #3 pilot failures get their own problem kinds, so the hint can say what to do: a rounded
  number ("about 150k", "43.8%" for 43.83), a bound or a bracketed range ("fewer than 2,500",
  "~32k-36k"), and a self-computed sum or share without the derived mark.
- Sections: Observed Facts, Hypotheses, Unknowns and Next Step are all present, not empty and in
  that order (roles.SECTIONS).
- Incidents: a count called Incident(s) cites `list_incidents` or `top_patterns` Evidence, because
  matching Log Entries are never Incidents by themselves (GLOSSARY.md).
- Side by side (#1): a Team Lead or Manager answer that cites a Log Entry count also cites Incident
  and Spread Evidence (`list_incidents` or `top_patterns`). While the server offers neither tool the
  check is skipped with a note, because a problem the agent cannot fix only blocks the answer.
- Manager (#4): no raw-line Evidence is cited (roles.is_raw_line); cost, money, downtime, customer
  and SLA terms appear only in Unknowns or in a sentence that says the logs cannot establish them
  (the #42 regrade showed that a plain keyword match flags correct refusals); and Unknowns names
  cost, downtime and customer impact (roles.NOT_MEASURED).

What a number is: digits, optionally with thousands separators, decimals, a percent sign or a k, M,
thousand or million suffix. Identifiers are not numbers and are masked first: dates, times, BGL
locations (R30-M0-N9-C:J16-U01), UUIDs, IP addresses and versions, hex ids, identifiers that contain
digits (p95, nova.api.v21, E1), line and entry numbers, HTTP status codes, ordinals, bare years,
inline code with letters in it, and ordered-list markers. Whole integers up to 10 need no Evidence
("the top 3", "5 lines before"), except a count of Incidents, which is the headline number.

Known limits: the checker cannot tell which of several cited records a number belongs to, so a
sentence citing two records is checked against both; a derived number is accepted when any two
numbers of its sources recompute it, which a small difference can do by chance; and a sentence is
the unit, so a citation at the end of a bullet does not cover an earlier sentence of that bullet.
"""

from __future__ import annotations

import bisect
import math
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.evidence import Evidence
from serenedb_log_mcp.roles import NOT_MEASURED, SECTIONS, answer_frame, is_raw_line
from serenedb_log_mcp.roles.shaping import RAW_LINE_COLUMN, RAW_LINE_TOOLS

__all__ = [
    "INCIDENT_TOOLS",
    "LOG_ENTRY_TOOLS",
    "PREAMBLE",
    "AnswerReport",
    "CheckedNumber",
    "Number",
    "Problem",
    "check_answer",
    "numbers_in",
    "split_sections",
]

INCIDENT_TOOLS: frozenset[str] = frozenset({"list_incidents", "top_patterns"})
"""Tools whose Evidence counts Incidents and carries their Spread (T27, T28; matched by name)."""

LOG_ENTRY_TOOLS: frozenset[str] = frozenset(
    {"count_by", "trend", "search_logs", "get_context", "describe_corpus"}
)
"""Tools whose numbers count Log Entries."""

PREAMBLE = "(before Observed Facts)"
"""The section name of text above the first section heading."""

SMALL_INT = 10
"""Whole numbers up to this need no Evidence, unless they count Incidents."""

NEGLIGIBLE = 0.005
"""A sum or difference counts as a computation only when the smaller operand is at least this share of
the larger number: 152,734 + 1 is a wrong number, not a sum anyone computed."""

DERIVATION_FLOOR = 100
"""A missing whole number below this is not reported as a self-computed value: with a few dozen
numbers in a record, some two of them differ by any small number by chance."""

MAX_TEXT = 240
"""Problem texts are cut to this many characters."""

FOUR_SECTIONS = ", ".join(SECTIONS)


# The report ------------------------------------------------------------------------------------------


class Problem(BaseModel):
    """One problem in a draft answer, with the text that has it and how to fix it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(
        description="What is wrong: unknown_evidence, no_citation, uncited_number, number_not_in_evidence, "
        "rounded_number, range, unmarked_derivation, derived_mismatch, missing_section, empty_section, "
        "section_order, incident_count_without_incident_evidence, log_entries_without_incidents, "
        "manager_raw_line_evidence, not_measured_claim, missing_unknown or checker_error."
    )
    section: str = Field(description="The answer section the problem is in, for example 'Observed Facts'.")
    text: str = Field(description="The offending text: the sentence, table row or section concerned.")
    hint: str = Field(description="How to fix it. Fix every problem, then call verify_answer again.")


class CheckedNumber(BaseModel):
    """One number of the draft that was checked against the Evidence Ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(description="The number as written in the draft, for example '152,734' or '43.83%'.")
    value: float = Field(description="Its value, with a k, M, thousand or million suffix applied.")
    section: str = Field(description="The answer section it is in.")
    cited: list[str] = Field(description="The Evidence ids its sentence cites.")
    status: str = Field(
        description="in_evidence (found in the cited Evidence), derived (marked '(derived from ...)' and "
        "recomputed), or the kind of the problem it caused."
    )
    found_in: list[str] = Field(description="The cited Evidence ids that hold this value.")


class AnswerReport(BaseModel):
    """The result of checking one draft answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = Field(description="True when no problem was found: the draft may be sent as the answer.")
    problems: list[Problem] = Field(
        description="Every problem found, each with the offending text and a fix hint."
    )
    checked_numbers: list[CheckedNumber] = Field(
        description="Every number that was checked, with where it was found."
    )
    cited_evidence: list[str] = Field(description="The Evidence ids the draft cites, in order of first use.")
    notes: list[str] = Field(description="Checks that were skipped, and why.")


# Patterns ----------------------------------------------------------------------------------------------

_SECTION_NAMES = {
    "observed fact": "Observed Facts",
    "observed facts": "Observed Facts",
    "hypothesis": "Hypotheses",
    "hypotheses": "Hypotheses",
    "unknown": "Unknowns",
    "unknowns": "Unknowns",
    "next step": "Next Step",
    "next steps": "Next Step",
}
_NAME = (
    r"(?:recommended\s+|suggested\s+|proposed\s+|key\s+|main\s+|open\s+)?"
    r"(?P<name>observed\s+facts?|hypothes[ie]s|unknowns?|next\s+steps?)\b"
)
_HEADINGS = (
    # "## Observed Facts", "### 1. Hypotheses", "# Next Step (Manager)"
    re.compile(
        rf"^\s{{0,3}}#{{1,6}}\s*(?:[0-9]+[.)]\s*)?(?:\*\*|__)?{_NAME}\s*(?:\*\*|__)?\s*:?\s*(?P<rest>.*)$",
        re.I,
    ),
    # "**Observed Facts**", "**Unknowns:** none of ..."
    re.compile(rf"^\s*(?:[0-9]+[.)]\s*)?(?:\*\*|__){_NAME}\s*:?\s*(?:\*\*|__)\s*:?\s*(?P<rest>.*)$", re.I),
    # "Next Step: run ..."
    re.compile(rf"^\s*(?:[0-9]+[.)]\s*)?{_NAME}\s*:\s*(?P<rest>.*)$", re.I),
    # "Hypotheses"
    re.compile(rf"^\s*(?:[0-9]+[.)]\s*)?{_NAME}\s*(?P<rest>)$", re.I),
)

_CITE = re.compile(r"\[(E[0-9]+(?:\s*[,;]\s*E[0-9]+)*)\]")
_DERIVED = re.compile(
    r"derived\s+from\s+(?P<ids>\[E[0-9]+(?:\s*[,;]\s*E[0-9]+)*\](?:\s*(?:,|;|and|&)?\s*\[E[0-9]+(?:\s*[,;]\s*E[0-9]+)*\])*)",
    re.I,
)
_NEW_ITEM = re.compile(r"^\s*(?:[-*+\u2022]|[0-9]{1,3}[.)])\s+")
_LIST_MARKER = re.compile(r"^\s*(?:>\s*)*(?:[-*+\u2022]\s+|[0-9]+[.)]\s+)?")
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^[\s|:\-]+$")
_ABBREVIATIONS = re.compile(r"\b(?:e\.g|i\.e|vs|etc|approx|cf|ca|no|fig)\.(?=\s)", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")
_ONLY_CITATIONS = re.compile(r"^[\s.,;:()*_\-]*$")

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_ID_MASKS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, flags)
    for pattern, flags in (
        (_CITE.pattern, 0),
        (r"https?://\S+", 0),
        (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", 0),
        (r"\breq-[0-9a-fA-F-]+", 0),
        (r"\bR[0-9]{2}(?:-[A-Za-z0-9:]+)*", 0),  # BGL locations: R30-M0-N9-C:J16-U01, R63-M0, R76
        (
            r"\b[0-9]{4}[-./][0-9]{2}[-./][0-9]{2}(?:[-T ][0-9]{2}[:.][0-9]{2}(?:[:.][0-9]{2}(?:\.[0-9]+)?)?"
            r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?(?:/[0-9]{1,2}\b)?",
            0,
        ),
        (r"\b(?:19|20)[0-9]{2}-(?:0[1-9]|1[0-2])\b", 0),  # year and month: 2005-06
        (r"\b(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])\b", 0),  # month and day: 06-11, 12-14
        (r"\b[0-9]{1,2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?\b", 0),
        (
            rf"\b[0-9]{{1,2}}(?:st|nd|rd|th)?(?:\s*(?:,|and|or|&|\u2013|\u2014|-|to)\s*[0-9]{{1,2}}(?:st|nd|rd|th)?)*"
            rf",?\s+{_MONTH}(?:,?\s+[0-9]{{4}})?",
            0,
        ),
        (rf"\b{_MONTH}\s+[0-9]{{1,2}}(?:st|nd|rd|th)?(?:,?\s+[0-9]{{4}})?\b", 0),
        (rf"\b{_MONTH}\s+(?:19|20)[0-9]{{2}}\b", 0),  # month and year: June 2005
        (r"\b0x[0-9a-fA-F]+\b", 0),
        (r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{8,}\b", 0),  # hex ids such as template ids
        (r"\b[0-9]+(?:\.[0-9]+){2,}(?::[0-9]+)?\b", 0),  # IP addresses, versions
        (r"\b[A-Za-z_][A-Za-z0-9_.]*[0-9][A-Za-z0-9_.]*", 0),  # identifiers with digits: p95, E1, v21
    )
)
"""Identifiers, dates and times: never a measured number, in a draft or in Evidence text."""

_CLAIM_MASKS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, flags)
    for pattern, flags in (
        (r"`[^`\n]*[A-Za-z_=(][^`\n]*`", 0),  # inline code with letters: SQL, calls, arguments
        (
            r"\b(?:lines?|line_no|line\s+numbers?|entry_id|entry|pid|jobs?|job_id)\s*[:#=]?\s*[0-9][0-9,]*"
            r"(?:\s*(?:\u2013|\u2014|-|to|and|or)\s*[0-9][0-9,]*)*",
            re.I,
        ),
        (
            r"\b(?:HTTP(?:/[0-9.]+)?|status(?:es)?(?:\s+codes?)?|response\s+codes?|codes?)\s*[:=]?\s*[1-5][0-9]{2}\b"
            r"(?:\s*(?:,|/|or|and)\s*[1-5][0-9]{2}\b)*",
            re.I,
        ),
        (r"\b[1-5][0-9]{2}(?:s\b|(?=\s+(?:responses?|errors?|status(?:es)?|codes?)\b))", re.I),
        (r"\blen\s*[:=]?\s*[0-9]+", re.I),
        (r"\b[0-9]+(?:st|nd|rd|th)\b", 0),
    )
)
"""What reads as a number in prose but is not a measurement: line and entry numbers, HTTP status
codes, ordinals and code; bare years are masked separately (`_mask_years`). Evidence text keeps
these: "status: 503" in a row is the value a draft may quote as "returned 503"."""

# What the logs do not measure (challenge brief: the Manager must not invent financial loss, downtime or
# customer impact; T7's Team Lead playbook: "Avoid: ... money or time impact"). An amount is always a
# claim; a bare term is a claim unless its clause defers it or says the logs cannot establish it.
_QTY = (
    r"(?:[0-9][0-9,.]*|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|(?<!how\s)many|"
    r"a\s+few|few)"
)
_SPAN = r"(?:hours?|hrs?|h|minutes?|mins?|days?|weeks?|seconds?|secs?)"
_ABOUT = r"(?:about\s+|around\s+|nearly\s+|almost\s+|over\s+|roughly\s+|some\s+)?"
_MONEY_AMOUNTS = (
    r"[$\u20ac\u00a3]\s?[0-9][0-9,.]*(?:\s?(?:[kKmM]\b|bn\b|thousand|million|billion))?"
    r"|\b[0-9][0-9,.]*\s?(?:[kKmM]\s?|bn\s?)?(?:(?-i:USD|EUR|GBP|CHF)|dollars?|euros?|pounds?)\b"
    r"|\b(?-i:USD|EUR|GBP|CHF)\s?[0-9][0-9,.]*(?:\s?(?:[kKmM]\b|bn\b|thousand|million|billion))?"
)
_DOWNTIME_AMOUNTS = (
    rf"\b{_QTY}\s*-?\s*{_SPAN}\s+(?:of\s+)?(?:downtime|outages?|unavailability|recovery|disruption|degradation)\b"
    rf"|\b(?:unavailable|down|offline|degraded)\s+for\s+{_ABOUT}{_QTY}\s*-?\s*{_SPAN}\b"
    rf"|\btook\s+{_ABOUT}{_QTY}\s*-?\s*{_SPAN}\s+to\s+(?:recover|restore|come\s+back|be\s+restored)\b"
    r"|\b[0-9][0-9.]*\s*%\s+(?:availability|uptime)\b"
)
_CUSTOMER_AMOUNTS = (
    rf"\b{_QTY}\s+(?:customers|users|clients|tenants)\s+(?:were\s+|was\s+)?(?:affected|impacted|hit)\b"
)
_MONEY_TERMS = (
    r"\b(?:costs?|costly|costing|money|monetary|financial(?:ly)?|revenue|profits?|dollars?|euros?|prices?|"
    r"pricing|billing|invoices?|penalt(?:y|ies)|(?-i:USD|EUR|GBP|CHF))\b|[$\u20ac\u00a3]"
)
_DOWNTIME_TERMS = (
    r"\b(?:downtime|outages?|uptime|availability(?!\s+zones?)|unavailab\w*|"
    r"(?:was|were|went|is|are|been|being)\s+down(?!\s+(?:to|by|from|in|over|after|sharply|slightly|steadily|[0-9]))|"
    r"(?:was|were|is|are|been|became|got)\s+(?:\w+\s+)?degraded)\b"
)
_CUSTOMER_TERMS = (
    r"\b(?:customers?|end[- ]users?|users?\s+(?:were\s+|was\s+)?(?:affected|impacted)|"
    r"user[- ](?:impact|facing)|"
    r"business\s+impact)\b"
)
_SLA_TERMS = r"\b(?:(?-i:SLAs?|SLOs?)|service[- ]levels?)\b"


@dataclass(frozen=True)
class _ClaimRule:
    amounts: re.Pattern[str]
    terms: re.Pattern[str]
    hint: str


def _union(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


_CLAIM_RULES: Mapping[Role, _ClaimRule] = {
    Role.MANAGER: _ClaimRule(
        _union(_MONEY_AMOUNTS, _DOWNTIME_AMOUNTS, _CUSTOMER_AMOUNTS),
        _union(_MONEY_TERMS, _DOWNTIME_TERMS, _CUSTOMER_TERMS, _SLA_TERMS),
        "These logs do not measure cost, money, downtime, customer impact or SLAs, so a Manager answer never "
        "states or estimates them. Name them in Unknowns as not measured by these logs, or say here that the "
        "logs cannot establish them, and give the measured alternative (Log Entries, Incidents, affected "
        "Components and Teams, active weeks) with [E<n>].",
    ),
    Role.TEAM_LEAD: _ClaimRule(
        _union(_MONEY_AMOUNTS, _DOWNTIME_AMOUNTS),
        _union(_MONEY_TERMS, _DOWNTIME_TERMS),
        "A Team Lead answer states no money or time impact (cost, downtime, outages): these logs do not "
        "measure them. Say what the logs measure instead (Log Entries, Incidents, Spread, the trend) with "
        "[E<n>], and name the impact in Unknowns if it matters.",
    ),
}
"""The not-measured claims each Role must not make. The Engineer has none."""

_CLAUSE_BREAK = re.compile(  # independent clauses only: a list ("A, B, and C") or a colon stays whole
    r"\s*;\s+|\s+[\u2014\u2013-]\s+|,\s*(?=which\b)|,?\s+but\s+|"
    r",\s*(?:and|so|yet)\s+(?=(?:i|we|you|they|it|he|she|the|this|that|these|those|our|its|their)\b)",
    re.I,
)
_NEGATION = re.compile(r"\b(?:not|no|cannot|can\s?not|never|neither|nor|unable|without|none)\b|n't\b", re.I)
_EPISTEMIC = re.compile(
    r"\b(?:logs?|data|evidence|measur\w*|record\w*|tell|establish\w*|show\w*|say|know\w*|giv\w*|"
    r"estimat\w*|quantif\w*|track\w*|report\w*|count\w*|figures?|metrics?|captur\w*|determin\w*|infer\w*|"
    r"claim\w*|assess\w*|comput\w*|calculat\w*|label\w*|rank\w*|priorit\w*|weigh\w*|based|visible)\b",
    re.I,
)
_HEDGE = re.compile(r"\b(?:could|might|may)\b", re.I)
_DEFERRAL = re.compile(
    r"\b(?:unknown|not[ _]measured|proxy|proxies|elsewhere|"
    r"outside\s+(?:of\s+)?(?:these|the|this|our)\s+(?:logs?|data|corpus|datasets?)|"
    r"beyond\s+(?:what\s+)?(?:these|the)\s+logs|separate\s+(?:system|source|tool|dataset)s?|"
    r"other\s+(?:systems|sources|tools)|(?:would|you'?d|we'?d|i'?d|they'?d)\s+(?:need|have\s+to|require)|"
    r"only\s+way\s+to\s+(?:estimate|get|know|measure|tell|establish|quantify)|"
    r"ask\s+(?:[\w-]+\s+){0,8}?(?:for|to|whether|about)|request\s+(?:[\w-]+\s+){0,6}?(?:from|for)|"
    r"if\s+you\s+(?:can|could|have|share|send|tell))\b"
    r"|(?:^|,)\s*(?:ask|request|obtain|collect|pull|gather|get|send|check|confirm)\b",
    re.I,
)
"""Phrases that defer a figure to other data. Bare words such as share, record or need are not on it
(#69 review): they are a Manager answer's everyday vocabulary and switched the guard off."""
_QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d|`[^`\n]*`')
_REQUIRED_UNKNOWNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (NOT_MEASURED[0], re.compile(r"\b(?:cost|financial|money|monetary|revenue|price|billing)", re.I)),
    (NOT_MEASURED[1], re.compile(r"\b(?:downtime|availability|uptime|outage)", re.I)),
    (NOT_MEASURED[2], re.compile(r"\b(?:customer|user)", re.I)),
)
"""What a Manager answer's Unknowns must name: the first three not-measured items (frames.py)."""

_NUMBER = re.compile(
    r"(?<![0-9A-Za-z_.])"
    r"(?P<num>[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
    r"(?P<suffix>\s?%|\s?(?:percent|per\s+cent)\b|\s?[kK]\b|\s?M\b|\s?bn\b|\s+(?:thousand|million|billion)\b)?"
)
_MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
_APPROXIMATE = re.compile(
    r"(?:~|\u2248|<|>|\u2264|\u2265|\babout|\baround|\bapprox(?:\.|imately)?|\broughly|\bnearly|\balmost|"
    r"\bsome|\bover|\bmore\s+than|\bfewer\s+than|\bless\s+than|\bunder|\bbelow|\babove|\bat\s+least|"
    r"\bat\s+most|\bup\s+to|\bclose\s+to|\bupwards\s+of|\bin\s+excess\s+of|\bon\s+the\s+order\s+of|\bcirca)"
    r"\s*$",
    re.I,
)
_RANGE_JOIN = re.compile(r"^\s*(?:[\u2013\u2014-]|to|until)\s*~?\s*$", re.I)
_BETWEEN = re.compile(r"\b(?:between|from)\s*~?\s*$", re.I)
_AND = re.compile(r"^\s*(?:and|to)\s*~?\s*$", re.I)
_SPREAD_NOUNS = r"(?:incidents?|nodes?|node\s+cards?|midplanes?|racks?|hosts?|instances?)"
_COUNTED_AFTER = re.compile(rf"^\s+(?:(?!not\b|no\b|never\b)[A-Za-z-]+\s+){{0,2}}{_SPREAD_NOUNS}\b", re.I)
_COUNTED_COLUMN = re.compile(rf"\b{_SPREAD_NOUNS}\b", re.I)
_SELECTION_BEFORE = re.compile(r"\b(?:top|first|last|next)\s+$", re.I)
"""Small numbers that count Incidents or Spread (nodes, midplanes, racks) are measurements, not "the top
3": the #1 side-by-side rule rests on them (#69 review)."""
_PARAMETER_BEFORE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*[=:]\s*~?$")
_TOOL_NAME = re.compile(
    r"\b(?:describe_corpus|search_logs|get_context|count_by|trend|list_patterns|search_patterns|top_patterns|"
    r"list_incidents|latency_stats|find_precursors|get_evidence|export_investigation)\b"
)
_MEASURED_AFTER = re.compile(
    r"^\s*(?:[A-Za-z-]+\s+){0,2}?(?:log\s+entr\w*|entries|lines|incidents?|nodes?|node\s+cards?|midplanes?|"
    r"racks?|hosts?|instances?|sources?|components?|teams?|patterns?|requests?|errors?|events?|groups?|"
    r"categories)\b",
    re.I,
)
"""Next Step holds suggested parameters, which need no Evidence: a `name=value` or `name: value` pair, or
a number in a sentence that names a tool, unless a measured noun follows it (#69 review)."""
_INCIDENT_COUNT = re.compile(
    r"(?<![0-9A-Za-z_.])[0-9][0-9,]*(?:\.[0-9]+)?\s+(?:(?!not\b|no\b|never\b)[A-Za-z-]+\s+){0,2}incidents?\b"
    r"|\bincidents?\s*[:=]\s*[0-9]",
    re.I,
)
_INCIDENT_WORD = re.compile(r"\bincidents?\b", re.I)
_LOG_ENTRY_WORDS = re.compile(r"\b(?:log\s+entr(?:y|ies)|log\s+lines?|lines|entries)\b", re.I)


# Parsing -----------------------------------------------------------------------------------------------


@dataclass
class _Unit:
    """One sentence, or one table row: the unit a citation covers."""

    section: str
    text: str
    cites: list[str]
    derived: list[str] | None = None
    header: list[str] | None = None


@dataclass
class _Parsed:
    order: list[str] = field(default_factory=list)
    """Sections in order of appearance; a section that comes back after another one is listed again."""
    content: dict[str, list[str]] = field(default_factory=dict)
    """The content lines of each section (the first occurrence and any later one)."""
    units: list[_Unit] = field(default_factory=list)


def _heading(line: str) -> tuple[str, str] | None:
    for pattern in _HEADINGS:
        match = pattern.match(line)
        if match:
            name = _SECTION_NAMES[re.sub(r"\s+", " ", match.group("name").lower())]
            return name, match.group("rest").strip().strip("*_").strip()
    return None


def _cites(text: str) -> list[str]:
    return [part.strip() for group in _CITE.findall(text) for part in re.split(r"[,;]", group)]


def _derived(text: str) -> list[str] | None:
    match = _DERIVED.search(text)
    return _cites(match.group("ids")) if match else None


def _sentences(text: str) -> list[str]:
    protected = _ABBREVIATIONS.sub(lambda m: m.group(0).replace(".", "\u2024"), text)
    parts = [part.replace("\u2024", ".") for part in _SENTENCE_END.split(protected) if part.strip()]
    merged: list[str] = []
    for part in parts:
        # A citation after the full stop ("... Log Entries. [E1]") belongs to the sentence before it.
        if merged and _CITE.search(part) and _ONLY_CITATIONS.match(_CITE.sub("", part)):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def _split_cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _parse(draft: str) -> _Parsed:
    parsed = _Parsed()
    lines = draft.splitlines()
    section = PREAMBLE
    in_fence = False
    # (section, line) pairs of content, so tables and captions can be read with their neighbours.
    body: list[tuple[str, str]] = []
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
            parsed.content.setdefault(section, []).append(line)
            body.append((section, ""))  # a code block ends the paragraph around it
            continue
        if in_fence:
            parsed.content.setdefault(section, []).append(line)
            continue
        found = _heading(line)
        if found is not None and found[0] != section:
            section, rest = found
            parsed.order.append(section)
            parsed.content.setdefault(section, [])
            if rest:
                parsed.content[section].append(rest)
                body.append((section, rest))
            continue
        if found is not None:  # the current section named again inline: "**Hypothesis:** ..."
            body.append((section, ""))
            line = found[1]
        if line.strip():
            parsed.content.setdefault(section, []).append(line)
        body.append((section, line))
    parsed.units = _units(body)
    return parsed


def _units(body: list[tuple[str, str]]) -> list[_Unit]:
    """Sentences and table rows, with the lines of a Markdown paragraph joined first.

    In Markdown a single newline inside a paragraph is not a break, so a sentence hard-wrapped over two
    lines is one sentence and keeps the citation at its end (#69 review). A blank line, a heading, a
    code fence, a table row or a new list item ends the paragraph.
    """
    units: list[_Unit] = []
    paragraph: list[str] = []
    where = PREAMBLE

    def flush() -> None:
        if paragraph:
            for sentence in _sentences(" ".join(paragraph)):
                units.append(_Unit(where, sentence, _cites(sentence), _derived(sentence)))
            paragraph.clear()

    i = 0
    while i < len(body):
        section, line = body[i]
        if _TABLE_ROW.match(line):
            flush()
            i = _table(body, i, units)
            continue
        if not line.strip() or section != where or _NEW_ITEM.match(line):
            flush()
        if line.strip():
            paragraph.append(_LIST_MARKER.sub("", line, count=1).strip())
            where = section
        i += 1
    flush()
    return units


def _neighbour(lines: Iterator[tuple[str, str]], section: str) -> str:
    """The paragraph next to a table in `lines` (nearest first), if it is in `section`.

    A caption never crosses a heading, and a caption hard-wrapped over two lines is read whole.
    """
    found: list[str] = []
    for where, line in lines:
        if where != section or _TABLE_ROW.match(line):
            break
        if not line.strip():
            if found:
                break
            continue
        found.append(line)
    return " ".join(found)


def _table(body: list[tuple[str, str]], start: int, units: list[_Unit]) -> int:
    """Read one Markdown table; data rows without citations inherit the table's citations.

    The table's citations are those of its header row, else those of its caption (the nearest
    non-blank line above it), else those of a line just below it that holds citations and no
    numbers (a "Source: [E1]" line). Ported from the #3 pilot grader with the #42 fix.
    """
    section = body[start][0]
    end = start
    while end < len(body) and _TABLE_ROW.match(body[end][1]):
        end += 1
    rows = [line for _, line in body[start:end]]
    has_header = len(rows) > 1 and "-" in rows[1] and bool(_TABLE_SEPARATOR.match(rows[1]))
    header = _split_cells(rows[0]) if has_header else None
    inherited = _cites(rows[0]) if has_header else []
    if not inherited:
        inherited = _cites(_neighbour(reversed(body[:start]), section))
    if not inherited:
        below = _neighbour(iter(body[end:]), section)
        if _cites(below) and not numbers_in(below):
            inherited = _cites(below)
    data = rows[2:] if has_header else rows
    if has_header:
        units.append(_Unit(section, rows[0].strip(), _cites(rows[0]), _derived(rows[0])))
    for row in data:
        own = _cites(row)
        units.append(_Unit(section, row.strip(), own or list(inherited), _derived(row), header))
    return end


# Numbers -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Number:
    """One number in a text, after identifiers, dates and code were masked."""

    text: str
    value: float
    decimals: int
    percent: bool
    multiplier: float
    approximate: bool
    start: int
    end: int
    trailing_zeros: int

    @property
    def unit(self) -> float:
        """The precision the number is written with, for example 10,000 for '150k' or 0.1 for '43.8'."""
        if self.decimals:
            return 10.0**-self.decimals * self.multiplier
        return 10.0**self.trailing_zeros * self.multiplier

    @property
    def small(self) -> bool:
        """A whole number up to SMALL_INT, written plainly ('3', not '3.0', '3%' or '3k')."""
        return not self.decimals and not self.percent and self.multiplier == 1 and self.value <= SMALL_INT


_YEAR = re.compile(r"(?<![0-9,.])(?:19[89][0-9]|20[0-3][0-9])(?![0-9]|[,.][0-9])")
_YEAR_CONTEXT = re.compile(
    r"(?:\b(?:early|late|mid|since|during|until|till|before|after|years?)|(?:19|20)[0-9]{2}\s*[\u2013\u2014-])"
    r"\s*-?\s*$",
    re.I,
)
_NEXT_WORD = re.compile(r"\s+([A-Za-z][A-Za-z-]*)")
_YEAR_FOLLOWERS = frozenset(
    [
        "to",
        "and",
        "or",
        "until",
        "till",
        "through",
        "thru",
        "onwards",
        "onward",
        "when",
        "where",
        "while",
        "with",
        "was",
        "were",
        "is",
        "are",
        "had",
        "has",
        "have",
        "saw",
        "at",
        "on",
        "in",
        "by",
        "but",
        "for",
        "the",
        "a",
        "an",
        "as",
        "after",
        "before",
        "that",
        "which",
        "so",
        "then",
        "data",
        "logs",
        "log",
        "corpus",
        "dataset",
    ]
)
"""Words that may follow a year ("from 2005 to 2006", "in 2005 the storm", "the 2005 data")."""
_COUNTED_WORDS = frozenset(
    [
        "log",
        "entries",
        "entry",
        "lines",
        "incident",
        "incidents",
        "component",
        "components",
        "team",
        "teams",
        "pattern",
        "patterns",
        "burst",
        "bursts",
        "episode",
        "episodes",
    ]
)
"""Capitalised glossary nouns that make a four-digit number a count ("2005 Log Entries")."""


def _mask_years(masked: str) -> str:
    """Mask four-digit numbers between 1980 and 2039 that read as years, not as counts.

    A year is followed by punctuation, a date word or a capitalised name ("in 2005 KERNDTLB peaked");
    a number followed by any other lowercase noun, or by a glossary noun, is a count ("2024 midplanes",
    "2019 sources", "2005 Log Entries"). A whitelist of count nouns cannot be completed (#69 review).
    """

    def year_or_count(match: re.Match[str]) -> str:
        if _YEAR_CONTEXT.search(masked[: match.start()]):
            return " " * len(match.group(0))
        following = _NEXT_WORD.match(masked, match.end())
        if following is not None:
            word = following.group(1)
            if word.lower() in _COUNTED_WORDS or (word[0].islower() and word not in _YEAR_FOLLOWERS):
                return match.group(0)
        return " " * len(match.group(0))

    return _YEAR.sub(year_or_count, masked)


def _mask(text: str, *, role: Role | None = None, evidence: bool = False) -> str:
    masked = text
    patterns: Sequence[re.Pattern[str]] = _ID_MASKS if evidence else (*_ID_MASKS, *_CLAIM_MASKS)
    rule = _CLAIM_RULES.get(role) if role is not None else None
    if rule is not None:
        patterns = (*patterns, rule.amounts)
    for pattern in patterns:
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    if not evidence:
        masked = _mask_years(masked)
    # Ordered-list markers and Markdown headings at the start of a line.
    return re.sub(
        r"(?m)^(\s*(?:#{1,6}\s*)?)([0-9]+[.)])(?=\s)", lambda m: m.group(1) + " " * len(m.group(2)), masked
    )


def numbers_in(text: str, *, role: Role | str | None = None, evidence: bool = False) -> list[Number]:
    """The numbers in `text` that make measured claims, in order (identifiers, dates and code masked).

    With a `role` that may not state money, downtime or customer impact (manager, team_lead), such
    amounts are left out too: for that Role they are a not-measured claim, not a number to look up. With
    `evidence`, only
    identifiers, dates and times are masked, so status codes and sizes in Evidence text still count.
    """
    masked = _mask(text, role=None if role is None else Role(role), evidence=evidence)
    found: list[Number] = []
    for match in _NUMBER.finditer(masked):
        raw = match.group("num")
        suffix = (match.group("suffix") or "").strip().lower()
        digits = raw.replace(",", "")
        try:
            value = float(digits)
        except ValueError:  # pragma: no cover - the pattern only matches ASCII digits
            continue
        if not math.isfinite(value):
            continue
        whole, _, fraction = digits.partition(".")
        multiplier = _MULTIPLIERS.get(suffix.replace(" ", ""), 1.0)
        before = masked[max(0, match.start() - 30) : match.start()]
        found.append(
            Number(
                text=text[match.start() : match.end()].strip(),
                value=value * multiplier,
                decimals=len(fraction),
                percent=suffix in {"%", "percent", "per cent"} or suffix.startswith("per"),
                multiplier=multiplier,
                approximate=bool(_APPROXIMATE.search(before)),
                start=match.start(),
                end=match.end(),
                trailing_zeros=0 if fraction or len(whole) < 2 else len(whole) - len(whole.rstrip("0")),
            )
        )
    return found


def _key(value: float) -> float:
    return round(abs(value), 6)


@dataclass
class _Pool:
    """The numbers one Evidence record holds, for lookups, and the operands a derivation may use."""

    values: set[float] = field(default_factory=set)
    operands: list[float] = field(default_factory=list)

    def holds(self, number: Number) -> bool:
        if _key(number.value) in self.values:
            return True
        # A percentage may quote a share the record holds as a fraction (top_node_share 1.0 = 100%).
        return number.percent and _key(number.value / 100) in self.values


_SKIPPED_FIELDS = frozenset(
    {"sql", "notes", "answer_frame", "not_measured", "evidence_id", "tool", "role", "columns"}
)
"""Envelope fields that hold no measured values: query text, steering notes and the Role frame."""


def _walk(value: Any, out: list[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float):
        if math.isfinite(value):
            out.append(abs(float(value)))
    elif isinstance(value, str):
        out.extend(number.value for number in numbers_in(value, evidence=True))
    elif isinstance(value, Mapping):
        for item in value.values():
            _walk(item, out)
    elif isinstance(value, list | tuple):
        for item in value:
            _walk(item, out)


def _pool(record: Mapping[str, Any]) -> _Pool:
    found: list[float] = []
    for name, value in record.items():
        if name not in _SKIPPED_FIELDS:
            _walk(value, found)
    operands = set(found)
    rows = record.get("rows")
    if isinstance(rows, list) and rows and all(isinstance(row, list) for row in rows):
        width = max(len(row) for row in rows)
        for column in range(width):
            cells = [row[column] for row in rows if column < len(row)]
            if all(isinstance(c, int | float) and not isinstance(c, bool) for c in cells):
                running = 0.0
                for cell in cells:
                    running += float(cell)
                    operands.add(abs(running))  # running totals: "the top 3 hold ..."
    return _Pool(values={_key(v) for v in found}, operands=sorted(operands))


def _fmt(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return format(value, ",.10f").rstrip("0").rstrip(".")


def _recomputes(number: Number, operands: Sequence[float]) -> bool:
    """Whether two operands give `number` as a sum, a difference, a share in percent or a ratio."""
    keys = {_key(v) for v in operands}
    target = number.value
    if _key(target) in keys:
        return True
    if not number.percent:
        for a in operands:
            rest = target - a  # a + rest = target
            if rest >= 0 and _key(rest) in keys and min(a, rest) >= NEGLIGIBLE * target:
                return True
            taken = a - target  # a - taken = target
            if taken > 0 and _key(taken) in keys and taken >= NEGLIGIBLE * a:
                return True
    if number.percent or number.decimals:
        tolerance = 0.5 * 10.0**-number.decimals + 1e-9
        scale = 100.0 if number.percent else 1.0
        for b in operands:
            if b <= 0:
                continue
            wanted = target * b / scale
            index = bisect.bisect_left(operands, wanted)
            for a in operands[max(0, index - 1) : index + 1]:
                if abs(scale * a / b - target) <= tolerance:
                    return True
    return False


def _nearest_derivation(number: Number, operands: Sequence[float]) -> float | None:
    """What the sources most likely give instead of `number`, for the fix hint.

    A single value or running total within 1% wins over a sum or difference of two numbers, because a
    pair can land closer by chance (a running total plus the 100 that the share_pct column adds up to).
    """
    target = number.value
    index = bisect.bisect_left(operands, target)
    singles = [v for v in operands[max(0, index - 1) : index + 1] if abs(v - target) <= 0.01 * abs(target)]
    if singles:
        return min(singles, key=lambda v: abs(v - target))
    best: float | None = None
    for a in operands:
        for wanted in (target - a, a - target):
            position = bisect.bisect_left(operands, wanted)
            for b in operands[max(0, position - 1) : position + 1]:
                for candidate in (a + b, a - b):
                    if candidate > 0 and (best is None or abs(candidate - target) < abs(best - target)):
                        best = candidate
    return best


# Checking ----------------------------------------------------------------------------------------------


@dataclass
class _Context:
    role: Role
    records: dict[str, Mapping[str, Any]]
    pools: dict[str, _Pool]
    incident_tools: bool
    problems: list[Problem] = field(default_factory=list)
    numbers: list[CheckedNumber] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, kind: str, section: str, text: str, hint: str) -> None:
        self.problems.append(Problem(kind=kind, section=section, text=_cut(text), hint=hint))

    def tool(self, evidence_id: str) -> str | None:
        record = self.records.get(evidence_id)
        return None if record is None else str(record.get("tool"))

    def known(self, ids: Iterable[str]) -> list[str]:
        return [i for i in dict.fromkeys(ids) if i in self.records]


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "\u2026"


def _id_list(ids: Sequence[str]) -> str:
    return ",".join(f"[{i}]" for i in ids)


def check_answer(
    role: Role | str,
    draft: str,
    ledger: Iterable[Mapping[str, Any]],
    *,
    incident_tools: bool = True,
) -> AnswerReport:
    """Check a draft answer for `role` against the Evidence Ledger `ledger` (its records, any order).

    `incident_tools` says whether the server offers list_incidents or top_patterns; without them the
    side-by-side check is skipped with a note. It never raises on a bad draft: an unexpected failure
    of the checker itself comes back as a `checker_error` problem.
    """
    try:
        role = Role(role)
    except ValueError:
        roles = ", ".join(r.value for r in Role)
        problem = Problem(
            kind="checker_error",
            section=PREAMBLE,
            text=f"role={role!r}",
            hint=f"role={role!r} is not a Role, so the draft was not checked. Pass one of: {roles}.",
        )
        return AnswerReport(ok=False, problems=[problem], checked_numbers=[], cited_evidence=[], notes=[])
    try:
        return _check(role, draft, ledger, incident_tools)
    except Exception as exc:  # the agent must always get an answer it can act on
        problem = Problem(
            kind="checker_error",
            section=PREAMBLE,
            text=_cut(draft[:MAX_TEXT]),
            hint=f"verify_answer itself failed on this draft ({type(exc).__name__}: {exc}). This is a bug in "
            "the checker, not in your draft: answer anyway, and say that the draft could not be verified.",
        )
        return AnswerReport(ok=False, problems=[problem], checked_numbers=[], cited_evidence=[], notes=[])


def _check(role: Role, draft: str, ledger: Iterable[Mapping[str, Any]], incident_tools: bool) -> AnswerReport:
    records: dict[str, Mapping[str, Any]] = {}
    for record in ledger:
        evidence_id = record.get("evidence_id") if isinstance(record, Mapping) else None
        if isinstance(evidence_id, str):
            records[evidence_id] = record
    ctx = _Context(role, records, {i: _pool(r) for i, r in records.items()}, incident_tools)
    parsed = _parse(draft)
    _check_sections(ctx, parsed)
    _check_citations(ctx, parsed, draft)
    _check_numbers(ctx, parsed)
    _check_incident_counts(ctx, parsed)
    if role in (Role.TEAM_LEAD, Role.MANAGER):
        _check_side_by_side(ctx, parsed)
    _check_not_measured(ctx, parsed)
    if role is Role.MANAGER:
        _check_manager(ctx, parsed)
    cited = list(dict.fromkeys(i for unit in parsed.units for i in unit.cites))
    return AnswerReport(
        ok=not ctx.problems,
        problems=ctx.problems,
        checked_numbers=ctx.numbers,
        cited_evidence=cited,
        notes=ctx.notes,
    )


def _check_sections(ctx: _Context, parsed: _Parsed) -> None:
    frame = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in answer_frame(ctx.role)}
    seen = [s for s in parsed.order if s in SECTIONS]
    for name in SECTIONS:
        if name not in seen:
            extra = (
                " For the Manager it names what these logs do not measure: cost, downtime and customer "
                "impact."
                if name == "Unknowns" and ctx.role is Role.MANAGER
                else ""
            )
            ctx.add(
                "missing_section",
                name,
                f"No '{name}' section.",
                f"Add a '## {name}' section: {frame[name]}.{extra} Every answer has the four sections "
                f"{FOUR_SECTIONS}, in this order.",
            )
        elif not any(line.strip() for line in parsed.content.get(name, [])):
            ctx.add(
                "empty_section",
                name,
                f"'{name}' is empty.",
                f"Write at least one line under {name}: {frame[name]}.",
            )
    present = [s for s in SECTIONS if s in seen]
    if seen != present:
        repeated = sorted({s for s in seen if seen.count(s) > 1}, key=SECTIONS.index)
        detail = (
            f" {', '.join(repeated)} appears more than once; merge it into one section." if repeated else ""
        )
        ctx.add(
            "section_order",
            seen[0],
            f"Sections in this order: {', '.join(seen)}.",
            f"Put the sections in this order: {FOUR_SECTIONS}.{detail}",
        )


def _check_citations(ctx: _Context, parsed: _Parsed, draft: str) -> None:
    reported: set[str] = set()
    for unit in parsed.units:
        for evidence_id in unit.cites:
            if evidence_id in ctx.records or evidence_id in reported:
                continue
            reported.add(evidence_id)
            ctx.add("unknown_evidence", unit.section, unit.text, _unknown_hint(ctx, evidence_id))
    facts = [u for u in parsed.units if u.section in ("Observed Facts", PREAMBLE)]
    has_facts = any(line.strip() for line in parsed.content.get("Observed Facts", []))
    if (has_facts and not any(u.cites for u in facts if u.section == "Observed Facts")) or (
        not has_facts and draft.strip() and not any(u.cites for u in parsed.units)
    ):
        first = next((u.text for u in facts if u.text.strip()), draft.strip()[:MAX_TEXT] or "(empty)")
        ctx.add(
            "no_citation",
            "Observed Facts",
            first,
            "Observed Facts cite no Evidence. An Observed Fact is a statement directly supported by cited "
            "Evidence: put [E<n>] (the evidence_id of the tool result) next to every number and fact"
            + ("." if ctx.records else ". The Evidence Ledger is empty, so call a tool first."),
        )


def _unknown_hint(ctx: _Context, evidence_id: str) -> str:
    if not ctx.records:
        return (
            f"{evidence_id} is not in the Evidence Ledger, which is empty: call a search or aggregation tool "
            "first and cite the evidence_id its result carries."
        )
    ids = sorted(ctx.records, key=lambda i: int(i[1:]) if i[1:].isdigit() else 0)
    return (
        f"{evidence_id} is not in the Evidence Ledger, which holds {ids[0]} to {ids[-1]}. Cite the "
        "evidence_id of the tool result the statement comes from, or call the tool that measures it."
    )


def _check_numbers(ctx: _Context, parsed: _Parsed) -> None:
    for unit in parsed.units:
        numbers = numbers_in(unit.text, role=ctx.role)
        if not numbers:
            continue
        masked = _mask(unit.text, role=ctx.role)
        ranges = _ranges(numbers, masked)
        unknown_only = bool(unit.cites) and not ctx.known(unit.cites)
        reported_ranges: set[int] = set()
        for index, number in enumerate(numbers):
            if number.small and not _counts_spread(unit, number, masked):
                continue
            if not unit.cites:
                if unit.section != "Next Step" or not _is_parameter(unit.text, masked, number):
                    _uncited(ctx, unit, number)
                continue
            if unknown_only:
                continue  # unknown_evidence already says what to do with this sentence
            span = ranges.get(index)
            if span is not None and span[0] in reported_ranges:
                continue
            status = _ground(ctx, unit, number, span, numbers, masked)
            if span is not None and status == "range":
                reported_ranges.add(span[0])


def _counts_spread(unit: _Unit, number: Number, masked: str) -> bool:
    """Whether a small number counts Incidents or Spread, in prose or in a column named after them."""
    if _SELECTION_BEFORE.search(masked[: number.start]):
        return False
    if _COUNTED_AFTER.match(masked[number.end :]):
        return True
    if unit.header is None:
        return False
    column = unit.text[: number.start].count("|") - 1
    return 0 <= column < len(unit.header) and bool(_COUNTED_COLUMN.search(unit.header[column]))


def _is_parameter(text: str, masked: str, number: Number) -> bool:
    if _PARAMETER_BEFORE.search(masked[: number.start]):
        return True
    call = _TOOL_NAME.search(text) or "`" in text or re.search(r"[A-Za-z_]\s*=\s*\S", text)
    return bool(call) and not _MEASURED_AFTER.match(masked[number.end :])


def _ranges(numbers: list[Number], masked: str) -> dict[int, tuple[int, int]]:
    """Numbers written as a range ("32k-36k", "between 32,000 and 36,000"): index -> (first, second)."""
    spans: dict[int, tuple[int, int]] = {}
    for i in range(len(numbers) - 1):
        a, b = numbers[i], numbers[i + 1]
        between = masked[a.end : b.start]
        before = masked[max(0, a.start - 12) : a.start]
        if _RANGE_JOIN.match(between) or (_BETWEEN.search(before) and _AND.match(between)):
            spans[i] = spans[i + 1] = (i, i + 1)
    return spans


def _record_number(
    ctx: _Context, unit: _Unit, number: Number, status: str, found_in: Sequence[str] = ()
) -> None:
    ctx.numbers.append(
        CheckedNumber(
            text=number.text,
            value=number.value,
            section=unit.section,
            cited=list(dict.fromkeys(unit.cites)),
            status=status,
            found_in=list(found_in),
        )
    )


def _holders(ctx: _Context, number: Number, ids: Iterable[str]) -> list[str]:
    return [i for i in ids if ctx.pools[i].holds(number)]


def _uncited(ctx: _Context, unit: _Unit, number: Number) -> None:
    _record_number(ctx, unit, number, "uncited_number")
    holders = _holders(ctx, number, ctx.records)
    where = (
        f" It is in {', '.join(holders[:3])}: cite {_id_list(holders[:1])} in this sentence."
        if holders
        else " Cite the Evidence it comes from in the same sentence, or call a tool that measures it."
    )
    ctx.add(
        "uncited_number",
        unit.section,
        unit.text,
        f"{number.text} has no [E<n>] citation in its sentence or table.{where} Every number needs the "
        "evidence_id of the result it comes from next to it.",
    )


def _ground(
    ctx: _Context,
    unit: _Unit,
    number: Number,
    span: tuple[int, int] | None,
    numbers: list[Number],
    masked: str,
) -> str:
    """Check one cited number; record it and report a problem when it is not in the cited Evidence."""
    cited = ctx.known(unit.cites)
    holders = _holders(ctx, number, cited)
    if holders:
        _record_number(ctx, unit, number, "in_evidence", holders)
        return "in_evidence"
    sources = ctx.known((unit.derived or []) + unit.cites)
    operands = sorted({v for i in sources for v in ctx.pools[i].operands})
    near = _near_values(ctx, number, cited)
    exact = (
        " Values near it in the cited Evidence: "
        + ", ".join(f"{_fmt(value)} ({', '.join(ids)})" for value, ids in near)
        + "."
        if near
        else ""
    )
    if span is not None:
        first, second = numbers[span[0]], numbers[span[1]]
        start = first.start
        while start > 0 and masked[start - 1] in "~\u2248 ":
            start -= 1
        written = unit.text[start : second.end].strip()
        candidates = sorted(
            {
                v
                for i in cited
                for v in ctx.pools[i].values
                if first.value - first.unit <= v <= second.value + second.unit
            }
        )
        values = ", ".join(_fmt(v) for v in candidates[:3])
        _record_number(ctx, unit, number, "range")
        ctx.add(
            "range",
            unit.section,
            unit.text,
            f"'{written}' is a range, not a measured number. Quote the exact value from the Evidence"
            + (f" ({_id_list(cited)} holds {values})." if values else f".{exact}")
            + " Use count_by or trend for an exact count.",
        )
        return "range"
    if number.approximate or number.multiplier != 1:
        _record_number(ctx, unit, number, "rounded_number")
        ctx.add(
            "rounded_number",
            unit.section,
            unit.text,
            f"{number.text} is rounded or bracketed. Quote the number exactly as the Evidence gives it, "
            f"never rounded, abbreviated or as a bound.{exact}{_share_advice(number, cited)}",
        )
        return "rounded_number"
    if unit.derived:
        if _recomputes(number, operands):
            _record_number(ctx, unit, number, "derived", sources)
            return "derived"
        best = _nearest_derivation(number, operands)
        closest = f" The nearest value they give is {_fmt(best)}." if best is not None else ""
        _record_number(ctx, unit, number, "derived_mismatch")
        ctx.add(
            "derived_mismatch",
            unit.section,
            unit.text,
            f"{number.text} is marked as derived but does not recompute from "
            f"{', '.join(sources) or 'its sources'} as a sum, difference or share of two of their numbers, a "
            f"column total or a running total.{closest} "
            "Recompute it, or quote a value the tool returned (summary values such as top_n_sum, share_pct, "
            "other_count).",
        )
        return "derived_mismatch"
    rounding = any(
        abs(value - number.value) <= number.unit / 2
        and (number.decimals or abs(value - number.value) <= 0.05 * number.value)
        for value, _ in near
    )
    if rounding and (number.decimals or number.trailing_zeros):
        _record_number(ctx, unit, number, "rounded_number")
        ctx.add(
            "rounded_number",
            unit.section,
            unit.text,
            f"{number.text} is rounded. Quote the number exactly as the Evidence gives it.{exact}"
            f"{_share_advice(number, cited)}",
        )
        return "rounded_number"
    meaningful = number.value >= DERIVATION_FLOOR or number.percent or number.decimals
    if meaningful and _recomputes(number, operands):
        _record_number(ctx, unit, number, "unmarked_derivation")
        ctx.add(
            "unmarked_derivation",
            unit.section,
            unit.text,
            f"{number.text} is not a number {', '.join(cited)} returned; it looks computed from it. Quote a "
            "value the tool returned (summary values such as top_n_sum, share_pct, other_count), or mark the "
            f"sentence '(derived from {_id_list(cited)})' so the computation can be checked.",
        )
        return "unmarked_derivation"
    elsewhere = [i for i in _holders(ctx, number, ctx.records) if i not in cited]
    _record_number(ctx, unit, number, "number_not_in_evidence")
    if elsewhere:
        hint = (
            f"{number.text} is not in {', '.join(cited)} but is in {', '.join(elsewhere[:3])}: cite "
            f"{_id_list(elsewhere[:1])} in this sentence."
        )
    else:
        hint = (
            f"{number.text} is not in {', '.join(cited)}.{exact} Quote the number exactly as the Evidence "
            "gives it, cite the Evidence that holds it, or call a tool that measures it."
        )
    ctx.add("number_not_in_evidence", unit.section, unit.text, hint)
    return "number_not_in_evidence"


def _share_advice(number: Number, cited: Sequence[str]) -> str:
    if not number.percent:
        return ""
    return (
        " For a share, quote the share_pct the tool returned, or compute it to two decimals and mark the "
        f"sentence '(derived from {_id_list(cited)})'."
    )


def _near_values(ctx: _Context, number: Number, cited: Sequence[str]) -> list[tuple[float, list[str]]]:
    """Up to three values of the cited Evidence near `number` (within its precision or 10%), nearest first."""
    window = max(number.unit, 0.1 * abs(number.value))
    holders: dict[float, list[str]] = {}
    for evidence_id in cited:
        for value in ctx.pools[evidence_id].values:
            if abs(value - number.value) <= window:
                holders.setdefault(value, []).append(evidence_id)
    nearest = sorted(holders, key=lambda v: abs(v - number.value))[:3]
    return [(value, holders[value]) for value in nearest]


def _incident_ids(ctx: _Context, ids: Iterable[str]) -> list[str]:
    return [i for i in ids if ctx.tool(i) in INCIDENT_TOOLS]


def _check_incident_counts(ctx: _Context, parsed: _Parsed) -> None:
    for unit in parsed.units:
        masked = _mask(unit.text)
        counts = _INCIDENT_COUNT.search(masked)
        in_column = (
            bool(unit.header)
            and any(_INCIDENT_WORD.search(c) for c in unit.header or [])
            and bool(numbers_in(unit.text))
        )
        if not (counts or in_column) or _incident_ids(ctx, unit.cites):
            continue
        written = unit.text[counts.start() : counts.end()].strip() if counts else unit.text
        if ctx.incident_tools:
            fix = (
                "A count of Incidents must come from list_incidents (or top_patterns): call it and cite its "
                "evidence_id here."
            )
        else:
            fix = (
                "This server offers no Incident tool (list_incidents, top_patterns) yet, so nothing can "
                "count Incidents."
            )
        ctx.add(
            "incident_count_without_incident_evidence",
            unit.section,
            unit.text,
            f"'{written}' counts Incidents without citing Incident Evidence. {fix} If the number counts "
            "matching Log Entries, call them Log Entries: Log Entries are never Incidents by themselves.",
        )


def _check_side_by_side(ctx: _Context, parsed: _Parsed) -> None:
    def counts_log_entries(unit: _Unit) -> bool:
        known = ctx.known(unit.cites)
        if not known or not any(not n.small for n in numbers_in(unit.text, role=ctx.role)):
            return False
        return any(ctx.tool(i) in LOG_ENTRY_TOOLS for i in known) or bool(_LOG_ENTRY_WORDS.search(unit.text))

    first = next((u for u in parsed.units if counts_log_entries(u)), None)
    if first is None or _incident_ids(ctx, (i for u in parsed.units for i in u.cites)):
        return
    if not ctx.incident_tools:
        ctx.notes.append(
            "Side-by-side check skipped: this server offers neither list_incidents nor top_patterns yet, so "
            "Log Entry counts cannot stand next to Incident and Spread Evidence."
        )
        return
    who = "A Team Lead" if ctx.role is Role.TEAM_LEAD else "A Manager"
    ctx.add(
        "log_entries_without_incidents",
        first.section,
        first.text,
        f"{who} answer reports Log Entries, Incidents and Spread side by side: one failing node can write "
        "thousands of Log Entries about one problem. Call list_incidents (or top_patterns) for the same Data "
        "Scope and cite its Incident count and Spread (nodes, midplanes, top_node_share) next to the Log "
        "Entry count.",
    )


def _raw_line(record: Mapping[str, Any]) -> bool:
    try:
        return is_raw_line(Evidence.model_validate(record))
    except ValidationError:
        columns = record.get("columns")
        return record.get("tool") in RAW_LINE_TOOLS or (
            isinstance(columns, list) and RAW_LINE_COLUMN in columns
        )


def _check_manager(ctx: _Context, parsed: _Parsed) -> None:
    reported: set[str] = set()
    for unit in parsed.units:
        for evidence_id in ctx.known(unit.cites):
            if evidence_id in reported or not _raw_line(ctx.records[evidence_id]):
                continue
            reported.add(evidence_id)
            ctx.add(
                "manager_raw_line_evidence",
                unit.section,
                unit.text,
                f"{evidence_id} is {ctx.tool(evidence_id)} Evidence, which holds raw Log Entries. A Manager "
                "answer uses measured frequency and affected Components only: count the same filter with "
                "count_by (or trend) and cite that result instead.",
            )
    unknowns = " ".join(parsed.content.get("Unknowns", []))
    if not unknowns.strip():
        return  # missing_section or empty_section already asks for it
    for item, pattern in _REQUIRED_UNKNOWNS:
        if not pattern.search(unknowns):
            ctx.add(
                "missing_unknown",
                "Unknowns",
                unknowns,
                f"A Manager answer's Unknowns names what these logs do not measure. Add: '{item}'.",
            )


def _check_not_measured(ctx: _Context, parsed: _Parsed) -> None:
    """Money, downtime, customer or SLA claims outside Unknowns; the Team Lead only money and time."""
    rule = _CLAIM_RULES.get(ctx.role)
    if rule is None:
        return
    for unit in parsed.units:
        if unit.section == "Unknowns":
            continue
        claim = _not_measured_claim(unit.text, rule)
        if claim is not None:
            ctx.add("not_measured_claim", unit.section, unit.text, f"'{claim.strip()}': {rule.hint}")


def _not_measured_claim(text: str, rule: _ClaimRule) -> str | None:
    """The phrase that states money, downtime, customer impact or SLAs as measured, if there is one.

    An amount ("EUR 240,000", "unavailable for 6 hours", "took 6 hours to recover") always does. A term
    alone does unless its own clause defers the figure to other data ("ask finance for the billing
    records", "tracked outside these logs", "you'd need the SLA definitions") or says the logs cannot
    establish it (a negation with a word about measuring or knowing, or with a hedge: "these logs do not
    measure downtime", "a burst, and not an outage"). The clause is the window, so a disclaimer in one
    clause does not excuse a claim in another ("The outage cost us money, which the logs do not show").
    A quoted search term is a mention, not a claim.
    """
    plain = _QUOTED.sub(lambda m: " " * len(m.group(0)), text.replace("\u2019", "'"))
    amount = rule.amounts.search(plain)
    if amount is not None:
        return amount.group(0)
    for clause in _CLAUSE_BREAK.split(plain):
        term = rule.terms.search(clause)
        if term is None or _DEFERRAL.search(clause):
            continue
        if _NEGATION.search(clause) and (_EPISTEMIC.search(clause) or _HEDGE.search(clause)):
            continue
        return term.group(0)
    return None


# Helpers for graders -----------------------------------------------------------------------------------


def split_sections(draft: str) -> dict[str, str]:
    """The text of each section of `draft` ('Observed Facts', ..., and PREAMBLE for text above them)."""
    parsed = _parse(draft)
    return {name: "\n".join(lines) for name, lines in parsed.content.items()}
