"""Render an investigation as a Markdown report (T13, Differentiator 8).

`render_investigation(audience, title, answers, records)` turns the answers of a session and the
Evidence Ledger records behind them into one document: per answering Role the question, the tool
calls with their parameters, the Data Scope, the result tables (first 10 rows, totals, `truncated`),
the SQL with its bound parameters and the answer, then a closing "What the data does and does not
establish" section. It is pure: it reads the records it is given and writes nothing, so
`export_investigation` stays read-only and `scripts/export_report.py` writes the same text to disk.

Which Evidence goes under which answer (`_owners`): a record an answer cites is shown in full under
the first answer that cites it, whichever Role produced it; a later answer that cites it gets a
reference. A record of the selection that no answer cites goes under an answer of the Role it was
produced for: the first such answer that cites a record from later in the ledger, else the last
such answer, since tool calls come before the answer they are made for. So when one Role answers
twice, each answer keeps its own Evidence. Records left over (no answer of their Role) go into
"Other Evidence".

The audience Role decides the tables: for `manager`, raw-line rows (`roles.is_raw_line`) are left out
of every table while counts, summary, SQL and Data Scope stay; for `engineer` and `team_lead`,
tables show the first `TABLE_ROWS` rows. The output is deterministic (no export time), so a report
can be committed and diffed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.export.answers import Answer
from serenedb_log_mcp.roles import NOT_MEASURED, RAW_LINE_TOOLS
from serenedb_log_mcp.roles.shaping import RAW_LINE_COLUMN
from serenedb_log_mcp.verify import AnswerReport, check_answer, split_sections

__all__ = ["ROLE_TITLES", "TABLE_ROWS", "Report", "render_investigation"]

TABLE_ROWS = 10
"""Rows a result table shows; the rest are counted below the table."""

ROLE_TITLES: Mapping[Role, str] = {
    Role.ENGINEER: "Engineer",
    Role.TEAM_LEAD: "Team Lead",
    Role.MANAGER: "Manager",
}

ANSWER_HEADING_LEVEL = 4
"""The level an answer's own top headings get, so they nest below its `### Answer` heading."""

CLOSING_TITLE = "What the data does and does not establish"

_RECORD_ID = re.compile(r"^E[1-9][0-9]*$")
_HEADING = re.compile(r"^(#{1,6})(\s.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BACKTICKS = re.compile(r"`+")

Record = Mapping[str, Any]


@dataclass(frozen=True)
class Report:
    """A rendered investigation."""

    markdown: str
    evidence_ids: list[str]
    """The Evidence ids shown in full, in ledger order."""
    notes: list[str]
    """What the caller should know: cited or requested ids the ledger does not hold."""


def render_investigation(
    audience: Role,
    title: str,
    answers: Sequence[Answer],
    records: Sequence[Record],
    *,
    evidence_ids: Sequence[str] | None = None,
    incident_tools: bool = True,
) -> Report:
    """Render the report of one investigation for `audience`.

    `records` are the Evidence Ledger records (any session); `evidence_ids` restricts the report to
    those records plus the ones an answer cites. `incident_tools` is passed to the answer checks
    (`verify.check_answer`), so they run as `verify_answer` would on the same server.
    """
    audience = Role(audience)
    by_id: dict[str, Record] = {}
    for record in records:
        evidence_id = record.get("evidence_id")
        if isinstance(evidence_id, str) and _RECORD_ID.fullmatch(evidence_id):
            by_id[evidence_id] = record
    position = {evidence_id: index for index, evidence_id in enumerate(by_id)}
    wanted = None if evidence_ids is None else set(evidence_ids)
    selected = [i for i in by_id if wanted is None or i in wanted]
    notes = [
        f"{i} was asked for but is not in the Evidence Ledger."
        for i in dict.fromkeys(evidence_ids or ())
        if i not in by_id
    ]
    checks = [
        check_answer(a.role, a.answer, list(by_id.values()), incident_tools=incident_tools) for a in answers
    ]

    cited_by = [[i for i in report.cited_evidence if i in by_id] for report in checks]
    owner = _owners(answers, cited_by, selected, by_id, position)
    shown_under: dict[str, str] = {}
    body: list[str] = []
    for index, (answer, report) in enumerate(zip(answers, checks, strict=True)):
        heading = f"{index + 1}. {ROLE_TITLES[answer.role]}"
        cited = cited_by[index]
        notes += [
            f"{heading} cites {i}, which is not in the Evidence Ledger."
            for i in report.cited_evidence
            if i not in by_id
        ]
        full = sorted((i for i, k in owner.items() if k == index), key=position.__getitem__)
        earlier = [i for i in cited if owner[i] < index]
        body += _answer_section(
            heading, answer, report, [by_id[i] for i in full], earlier, by_id, shown_under, audience
        )
        for i in full:
            shown_under[i] = heading
    leftover = [i for i in selected if i not in shown_under]
    if leftover:
        body += ["## Other Evidence", "", "Evidence in this investigation that no answer above uses.", ""]
        for i in leftover:
            body += _record(by_id[i], audience)
            shown_under[i] = "Other Evidence"
    shown = sorted(shown_under, key=position.__getitem__)
    body += _closing(answers, checks, [by_id[i] for i in shown])

    head = _header(audience, title, answers, shown, bool(leftover))
    markdown = "\n".join([*head, *body]).rstrip("\n") + "\n"
    return Report(markdown=markdown, evidence_ids=shown, notes=notes)


def _owners(
    answers: Sequence[Answer],
    cited_by: Sequence[list[str]],
    selected: Sequence[str],
    by_id: Mapping[str, Record],
    position: Mapping[str, int],
) -> dict[str, int]:
    """The index of the answer each record is shown in full under (see the module docstring)."""
    owner: dict[str, int] = {}
    for index, cited in enumerate(cited_by):
        for i in cited:
            owner.setdefault(i, index)
    last_cited = [max((position[i] for i in cited), default=-1) for cited in cited_by]
    for i in selected:
        if i in owner:
            continue
        same_role = [k for k, a in enumerate(answers) if a.role.value == by_id[i].get("role")]
        if same_role:
            owner[i] = next((k for k in same_role if last_cited[k] >= position[i]), same_role[-1])
    return owner


# Sections -----------------------------------------------------------------------------------------------


def _header(
    audience: Role, title: str, answers: Sequence[Answer], shown: list[str], other: bool
) -> list[str]:
    roles = ", ".join(ROLE_TITLES[a.role] for a in answers)
    evidence = (
        f"{_plural(len(shown), 'Evidence record')} ({_id_span(shown)})" if shown else "no Evidence records"
    )
    tables = (
        "For the manager audience, raw Log Entries are left out of every table; counts, summaries, SQL "
        "and Data Scopes stay."
        if audience is Role.MANAGER
        else f"Result tables show the first {TABLE_ROWS} rows of each result."
    )
    lines = [
        f"# {_one_line(title)}",
        "",
        f"Exported by `export_investigation` for the {audience.value} audience: "
        f"{_plural(len(answers), 'answer')} ({roles}) and {evidence} from the Evidence Ledger.",
        "",
        "Every number in an answer cites the Evidence it comes from as [E<n>]. Each Evidence record shows "
        "the tool call with its parameters, the Data Scope, the result and the SQL that ran, as psycopg "
        "ran it: `%s` marks a bound parameter and `%%` is a literal `%`. `scripts/replay_ledger.py` "
        "re-runs every statement and checks the numbers. " + tables,
        "",
        "## Contents",
        "",
    ]
    lines += [f"{n}. {ROLE_TITLES[a.role]}: {_one_line(a.question)}" for n, a in enumerate(answers, start=1)]
    extra = ["Other Evidence"] if other else []
    lines += [f"{len(answers) + n}. {name}" for n, name in enumerate([*extra, CLOSING_TITLE], start=1)]
    return [*lines, ""]


def _answer_section(
    heading: str,
    answer: Answer,
    report: AnswerReport,
    full: list[Record],
    earlier: list[str],
    by_id: Mapping[str, Record],
    shown_under: Mapping[str, str],
    audience: Role,
) -> list[str]:
    lines = [f"## {heading}", "", f"**Question:** {_one_line(answer.question)}", "", "### Tool calls", ""]
    if not full:
        lines += [
            "No tool calls of its own; the answer builds on Evidence shown earlier."
            if earlier
            else "No Evidence in the Evidence Ledger belongs to this answer.",
            "",
        ]
    for record in full:
        lines += _record(record, audience)
    if earlier:
        lines += ["Also cites Evidence shown earlier in this report:", ""]
        lines += [
            f"- {i} ({by_id[i].get('tool')}, {by_id[i].get('role')}), shown under {shown_under[i]}"
            for i in earlier
        ]
        lines.append("")
    lines += ["### Answer", "", _demote(answer.answer.strip()), "", _verdict(report), ""]
    return lines


def _record(record: Record, audience: Role) -> list[str]:
    tool = str(record.get("tool"))
    lines = [
        f"#### {record.get('evidence_id')}: {tool}",
        "",
        f"- **Call:** {_code(_call(record))}",
        f"- **Data Scope:** {_scope(record.get('data_scope') or {})}",
        f"- **Result:** {_result(record)}",
    ]
    summary = record.get("summary") or {}
    if summary:
        lines.append(
            "- **Summary:** " + "; ".join(f"{key} {_value(value)}" for key, value in summary.items())
        )
    lines.append("")
    lines += _table(record, audience)
    lines += _sql(record)
    return lines


def _closing(answers: Sequence[Answer], checks: Sequence[AnswerReport], shown: list[Record]) -> list[str]:
    lines = [f"## {CLOSING_TITLE}", "", "### What it establishes", ""]
    verdicts = "; ".join(
        f"{ROLE_TITLES[a.role]} {_short_verdict(r)}" for a, r in zip(answers, checks, strict=True)
    )
    lines.append(
        "- **Observed Facts with Evidence.** Every Observed Fact above cites the Evidence it rests on, "
        f"and the answers were checked against it like verify_answer checks a draft: {verdicts}."
    )
    raw = [r for r in shown if _raw_line(r)]
    aggregated = [r for r in shown if not _raw_line(r)]
    datasets = sorted({d for r in shown for d in (r.get("data_scope") or {}).get("datasets") or []})
    lines.append(
        "- **Search and aggregation together.** "
        f"Retrieval: {_evidence_of(raw)}. Aggregation: {_evidence_of(aggregated)}. "
        f"Datasets: {', '.join(datasets) or 'none'}."
    )
    scopes: dict[str, list[str]] = {}
    for record in shown:
        scopes.setdefault(_scope(record.get("data_scope") or {}), []).append(str(record.get("evidence_id")))
    if scopes:
        lines.append("- **Data Scopes.** Each number holds for the Data Scope of its Evidence:")
        lines += [f"  - {scope}: {', '.join(ids)}" for scope, ids in scopes.items()]
    lines += ["", "### What it does not establish", ""]
    for section, lead in (
        ("Hypotheses", "Consistent with the Evidence, not established by it."),
        ("Unknowns", "What the answers state the logs cannot establish."),
    ):
        lines += [f"#### {section}", "", lead, ""]
        for answer in answers:
            text = split_sections(answer.answer).get(section, "").strip()
            lines += [f"**{ROLE_TITLES[answer.role]}**", "", _demote(text) if text else "(none stated)", ""]
    lines += ["#### Limits of the Evidence", ""]
    samples = [r for r in raw if r.get("truncated")]
    if samples:
        listed = "; ".join(
            f"{r.get('evidence_id')} returned {_int(r.get('returned'))} of {_int(r.get('total_matches'))} "
            "matching Log Entries"
            for r in samples
        )
        lines.append(
            f"- **Samples are not frequencies.** {listed}. Their rows are examples; only total_matches "
            "and the summary count every match."
        )
    lines.append(
        "- **Log Entries are not Incidents.** A count of matching Log Entries is a count of lines: one "
        "failing node can write thousands of lines about one problem."
    )
    lines.append("- **Not measured by these logs:**")
    lines += [f"  - {item}" for item in NOT_MEASURED]
    return [*lines, ""]


# Pieces -------------------------------------------------------------------------------------------------


def _call(record: Record) -> str:
    arguments = {"role": record.get("role"), **(record.get("arguments") or {})}
    shown = ", ".join(f"{name}={_json(value)}" for name, value in arguments.items() if value is not None)
    return f"{record.get('tool')}({shown})"


def _scope(scope: Mapping[str, Any]) -> str:
    datasets = ", ".join(scope.get("datasets") or []) or "no Dataset"
    start, end = scope.get("time_from"), scope.get("time_to")
    if start is None and end is None:
        span = "whole Dataset" if len(scope.get("datasets") or []) <= 1 else "whole Datasets"
    else:
        span = f"{start or 'first Log Entry'} to {end or 'last Log Entry'}"
    if scope.get("time_input"):
        span += f" (asked as {_json(scope['time_input'])}, resolved against {scope.get('resolved_against')})"
    parts = [datasets, span]
    known = {"datasets", "time_from", "time_to", "time_input", "resolved_against", "filters"}
    parts += [
        f"{key} {_value(value)}" for key, value in scope.items() if key not in known and value is not None
    ]
    filters = {key: value for key, value in (scope.get("filters") or {}).items() if value is not None}
    if filters:
        parts.append("filters: " + ", ".join(f"{key}={_json(value)}" for key, value in filters.items()))
    return "; ".join(parts)


def _result(record: Record) -> str:
    truncated = "yes" if record.get("truncated") else "no"
    return (
        f"total_matches {_int(record.get('total_matches'))}, returned {_int(record.get('returned'))}, "
        f"truncated: {truncated}, query time {record.get('query_ms')} ms"
    )


def _table(record: Record, audience: Role) -> list[str]:
    rows: list[Any] = list(record.get("rows") or [])
    columns = [str(c) for c in record.get("columns") or []]
    if not rows:
        return ["No rows.", ""]
    if audience is Role.MANAGER and _raw_line(record):
        return [
            f"Rows withheld for the manager audience: they are raw Log Entries. total_matches "
            f"({_int(record.get('total_matches'))}) and the summary are exact over every match.",
            "",
        ]
    width = max([len(columns), *(len(row) for row in rows if isinstance(row, list))])
    header = columns + [""] * (width - len(columns))
    lines = [
        "| " + " | ".join(_cell(c) for c in header) + " |",
        "|" + "---|" * width,
    ]
    for row in rows[:TABLE_ROWS]:
        cells = list(row) if isinstance(row, list) else [row]
        cells += [None] * (width - len(cells))
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    lines.append("")
    if len(rows) > TABLE_ROWS:
        lines += [
            f"Showing the first {TABLE_ROWS} of {len(rows)} returned rows; total_matches is "
            f"{_int(record.get('total_matches'))}.",
            "",
        ]
    return lines


def _sql(record: Record) -> list[str]:
    statements = [str(s) for s in record.get("sql") or []]
    if not statements:
        return []
    params: list[Any] = list(record.get("params") or [])
    lines = [f"<details><summary>SQL: {_plural(len(statements), 'statement')}</summary>", ""]
    for index, statement in enumerate(statements):
        fence = "`" * max(3, _longest_backticks(statement) + 1)
        lines += [f"{fence}sql", statement, fence, ""]
        bound = params[index] if index < len(params) else []
        lines += [f"Parameters: {_code(_json(bound))}", ""]
    return [*lines, "</details>", ""]


def _verdict(report: AnswerReport) -> str:
    if report.ok:
        cited = ", ".join(report.cited_evidence) or "no Evidence"
        return (
            f"**Checked:** verify_answer found no problem: {_plural(len(report.checked_numbers), 'number')} "
            f"checked against {cited}."
        )
    lines = [f"**Checked:** verify_answer found {_plural(len(report.problems), 'problem')}:", ""]
    lines += [f"- {p.kind} in {p.section}: {_one_line(p.text)}" for p in report.problems]
    return "\n".join(lines)


def _short_verdict(report: AnswerReport) -> str:
    if report.ok:
        return f"no problem ({_plural(len(report.checked_numbers), 'number')} checked)"
    return _plural(len(report.problems), "problem")


def _evidence_of(records: list[Record]) -> str:
    if not records:
        return "none"
    tools = ", ".join(dict.fromkeys(str(r.get("tool")) for r in records))
    return f"{', '.join(str(r.get('evidence_id')) for r in records)} ({tools})"


def _raw_line(record: Record) -> bool:
    """`roles.is_raw_line` on a stored record, which need not validate as Evidence."""
    return record.get("tool") in RAW_LINE_TOOLS or RAW_LINE_COLUMN in (record.get("columns") or [])


def _demote(markdown: str) -> str:
    """The answer's headings moved down so its top level is `ANSWER_HEADING_LEVEL`; code is untouched."""
    lines = markdown.splitlines()
    levels = []
    fenced = False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (match := _HEADING.match(line)):
            levels.append(len(match.group(1)))
    shift = max(0, ANSWER_HEADING_LEVEL - min(levels)) if levels else 0
    if not shift:
        return markdown
    out = []
    fenced = False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (match := _HEADING.match(line)):
            line = "#" * min(6, len(match.group(1)) + shift) + match.group(2)
        out.append(line)
    return "\n".join(out)


def _id_span(ids: list[str]) -> str:
    numbers = [int(i[1:]) for i in ids]
    if len(ids) > 1 and numbers == list(range(numbers[0], numbers[0] + len(numbers))):
        return f"{ids[0]} to {ids[-1]}"
    return ", ".join(ids)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _int(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else str(value)


def _value(value: Any) -> str:
    """A summary or scope value in running text: numbers as numbers, everything else as code."""
    if isinstance(value, bool) or value is None:
        return _code(_json(value))
    if isinstance(value, int | float):
        return format(value, ",")
    if isinstance(value, str):
        return _code(value)
    return _code(_json(value))


def _cell(value: Any) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int | float):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        text = _json(value)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return " ".join(text.replace("|", r"\|").split())


def _code(text: str) -> str:
    """`text` as inline code, with a fence longer than any run of backticks inside it."""
    fence = "`" * (_longest_backticks(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _longest_backticks(text: str) -> int:
    return max((len(run) for run in _BACKTICKS.findall(text)), default=0)
