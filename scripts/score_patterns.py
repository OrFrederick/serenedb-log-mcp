#!/usr/bin/env python3
"""Score the Tier 1 Pattern chain against the Loghub-2.0 labels: the T9 merge gate (#6).

Usage (see `make help`):
    uv run python scripts/score_patterns.py [--schema S] [--labels-schema R] [--min-agreement 0.9999]

The chain is the one the derive step installed in SLM_SCHEMA (the macros pattern_tier1_bgl and
pattern_tier1_openstack, generated from the Loghub-2.0 template files). It runs in SQL over every
labelled Loghub-2.0 line (the tables lh2_bgl and lh2_openstack that the dev loader loads into
SLM_RAW_SCHEMA), and the EventId it picks is compared with the gold EventId:

    agreement            lines whose chosen EventId equals the gold one, over all labelled lines
                         (a line no template matches counts as a disagreement); this is the gate
    Tier 1 coverage      labelled lines that the chain gives any template, over all labelled
                         lines (not over the Log Entries of the corpus; see the coverage table)
    agreement on Tier 1  agreeing lines over the lines the chain covers

It also checks that every row of pattern_dict carries the EventId the chain gives its message, and
reports the coverage of each template_source over the Log Entries. It prints a Markdown report and
exits 0 when the agreement on every Dataset reaches --min-agreement (default 0.9999, the merge gate
of docs/overnight-decisions.md; the target is 0.999999), 1 when it does not, and 2 when it cannot
score (no labels, no derived Patterns, Tier 1 off).
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

from serenedb_log_mcp.config import SCHEMA_RE, ConfigError, load_settings

DATASETS = ("bgl", "openstack")
LABEL_TABLES: Mapping[str, str] = {"bgl": "lh2_bgl", "openstack": "lh2_openstack"}
DEFAULT_MIN_AGREEMENT = 0.9999
EXAMPLE_CHARS = 100
SOURCES = ("loghub2", "sqlmask", "empty")


class ScoreError(Exception):
    """Nothing to score; printed without a traceback."""


@dataclass(frozen=True)
class Outcome:
    """The labelled lines with one gold EventId for which the chain chose `chosen` (None: no match)."""

    gold: str
    chosen: str | None
    lines: int
    messages: int
    example: str
    gold_template: str


@dataclass(frozen=True)
class Score:
    dataset: str
    lines: int
    agreeing: int
    covered: int
    disagreements: list[Outcome] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        return self.agreeing / self.lines if self.lines else 0.0

    @property
    def coverage(self) -> float:
        return self.covered / self.lines if self.lines else 0.0

    @property
    def covered_agreement(self) -> float:
        return self.agreeing / self.covered if self.covered else 0.0


@dataclass(frozen=True)
class Coverage:
    """Patterns, distinct messages and Log Entries of one template_source in one Dataset."""

    dataset: str
    source: str
    patterns: int
    messages: int
    entries: int


def score(dataset: str, outcomes: Sequence[Outcome]) -> Score:
    disagreements = sorted(
        (o for o in outcomes if o.chosen != o.gold), key=lambda o: (-o.lines, o.gold, o.chosen or "")
    )
    return Score(
        dataset=dataset,
        lines=sum(o.lines for o in outcomes),
        agreeing=sum(o.lines for o in outcomes if o.chosen == o.gold),
        covered=sum(o.lines for o in outcomes if o.chosen is not None),
        disagreements=disagreements,
    )


def percent(share: float) -> str:
    """A share as a percentage with four decimals, rounded down so 99.99995% never shows as 100%."""
    if share >= 1:
        return "100%"
    return f"{math.floor(share * 1_000_000) / 10_000:.4f}%"


def gate_problems(scores: Sequence[Score], minimum: float) -> list[str]:
    problems = []
    for s in scores:
        if s.covered == 0:
            problems.append(
                f"{s.dataset}: the chain matches no labelled line; was the schema derived with "
                "SLM_PATTERN_TIER1=0 or without the Loghub-2.0 files?"
            )
        elif s.agreement < minimum:
            problems.append(f"{s.dataset}: agreement {percent(s.agreement)} is below {percent(minimum)}")
    return problems


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def report(
    schema: str,
    labels: str,
    scores: Sequence[Score],
    coverage: Sequence[Coverage],
    consistency: Mapping[str, tuple[int, int]],
    minimum: float,
) -> str:
    """The Markdown report: agreement, every disagreeing template, coverage, the gate."""
    out = [
        f"### Agreement with the Loghub-2.0 labels (schema `{schema}`, labels in `{labels}`)",
        "",
        "| Dataset | Labelled lines | Agreeing | Agreement | Tier 1 coverage of labelled lines "
        "| Agreement on Tier 1 lines |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in scores:
        out.append(
            f"| {s.dataset} | {s.lines:,} | {s.agreeing:,} | {percent(s.agreement)} | "
            f"{percent(s.coverage)} | {percent(s.covered_agreement)} |"
        )
    out += ["", "### Disagreeing templates", ""]
    for s in scores:
        if not s.disagreements:
            out.append(f"- {s.dataset}: no disagreeing template.")
            continue
        out += [
            f"{s.dataset}:",
            "",
            "| Gold EventId | Assigned | Lines | Messages | Gold template | Example message |",
            "|---|---|---:|---:|---|---|",
        ]
        for d in s.disagreements:
            example = d.example if len(d.example) <= EXAMPLE_CHARS else d.example[: EXAMPLE_CHARS - 3] + "..."
            out.append(
                f"| {d.gold} | {d.chosen or 'none (Tier 2)'} | {d.lines:,} | {d.messages:,} | "
                f"{_cell(d.gold_template)} | `{_cell(example)}` |"
            )
        out.append("")
    if coverage:
        out += [
            "",
            "### Coverage per template_source",
            "",
            "| Dataset | template_source | Patterns | Distinct messages | Log Entries "
            "| Share of Log Entries |",
            "|---|---|---:|---:|---:|---:|",
        ]
        totals: dict[str, int] = {}
        for c in coverage:
            totals[c.dataset] = totals.get(c.dataset, 0) + c.entries
        for c in coverage:
            share = c.entries / totals[c.dataset] if totals[c.dataset] else 0.0
            out.append(
                f"| {c.dataset} | {c.source} | {c.patterns:,} | {c.messages:,} | {c.entries:,} "
                f"| {percent(share)} |"
            )
    if consistency:
        out += ["", "### Dictionary consistency", ""]
        for dataset, (consistent, rows) in consistency.items():
            out.append(
                f"- {dataset}: {consistent:,} of {rows:,} pattern_dict rows carry the EventId the chain "
                "gives their message."
            )
    problems = gate_problems(scores, minimum)
    out += ["", f"### Gate: agreement of at least {percent(minimum)} on every Dataset", ""]
    out += [f"- FAIL {p}" for p in problems] or ["- PASS"]
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------- database


def _rows(
    conn: psycopg.Connection[Any], query: sql.Composed, params: Sequence[Any] = ()
) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in conn.execute(query, params or None).fetchall()]


def _exists(conn: psycopg.Connection[Any], schema: str, table: str) -> bool:
    found = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [schema, table],
    ).fetchone()
    return bool(found and found[0])


def outcomes(conn: psycopg.Connection[Any], schema: str, labels: str, dataset: str) -> list[Outcome]:
    query = sql.SQL(
        "WITH gold AS (SELECT content, event_id, min(event_template) AS template, count(*) AS n "
        "FROM {labels} GROUP BY content, event_id), "
        "chosen AS (SELECT event_id, {chain}(content) AS chosen, n, content, template FROM gold) "
        "SELECT event_id, chosen, sum(n), count(*), min(content), min(template) FROM chosen GROUP BY 1, 2"
    ).format(
        labels=sql.Identifier(labels, LABEL_TABLES[dataset]),
        chain=sql.Identifier(schema, f"pattern_tier1_{dataset}"),
    )
    return [
        Outcome(str(gold), None if chosen is None else str(chosen), int(n), int(m), str(ex), str(tpl))
        for gold, chosen, n, m, ex, tpl in _rows(conn, query)
    ]


def dictionary_consistency(conn: psycopg.Connection[Any], schema: str, dataset: str) -> tuple[int, int]:
    query = sql.SQL(
        "SELECT count(*) FILTER (WHERE event_id IS NOT DISTINCT FROM "
        "CASE WHEN trim(message) <> '' THEN {chain}(message) END), count(*) FROM {dict} WHERE dataset = %s"
    ).format(
        chain=sql.Identifier(schema, f"pattern_tier1_{dataset}"), dict=sql.Identifier(schema, "pattern_dict")
    )
    consistent, total = _rows(conn, query, [dataset])[0]
    return int(consistent), int(total)


def coverage_rows(conn: psycopg.Connection[Any], schema: str) -> list[Coverage]:
    query = sql.SQL(
        "SELECT p.dataset, p.template_source, count(*), sum(p.entries), "
        "(SELECT count(*) FROM {dict} d "
        "WHERE d.dataset = p.dataset AND d.template_source = p.template_source) "
        "FROM {pattern} p GROUP BY 1, 2"
    ).format(dict=sql.Identifier(schema, "pattern_dict"), pattern=sql.Identifier(schema, "pattern"))
    found = {(str(d), str(s)): (int(p), int(m), int(e)) for d, s, p, e, m in _rows(conn, query)}
    return [
        Coverage(dataset, source, *found[(dataset, source)])
        for dataset in DATASETS
        for source in SOURCES
        if (dataset, source) in found
    ]


# ----------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="score_patterns.py", description="Score the Tier 1 Pattern chain against the Loghub-2.0 labels."
    )
    parser.add_argument("--schema", help="derived schema (default: SLM_SCHEMA)")
    parser.add_argument(
        "--labels-schema", help="schema of lh2_bgl and lh2_openstack (default: SLM_RAW_SCHEMA)"
    )
    parser.add_argument("--min-agreement", type=float, default=DEFAULT_MIN_AGREEMENT, help="default: 0.9999")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        schema = args.schema or settings.slm_schema
        labels = args.labels_schema or settings.raw_schema
        for name in (schema, labels):
            if not SCHEMA_RE.fullmatch(name):
                raise ScoreError(f"schema {name!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            for table in ("pattern_dict", "pattern"):
                if not _exists(conn, schema, table):
                    raise ScoreError(f"{schema}.{table} is missing; run `make derive` first")
            for table in LABEL_TABLES.values():
                if not _exists(conn, labels, table):
                    raise ScoreError(
                        f"{labels}.{table} is missing: the Loghub-2.0 labels are loaded by the dev loader "
                        "(`make load-dev`) from the files of `make fetch-data`"
                    )
            scores = [score(d, outcomes(conn, schema, labels, d)) for d in DATASETS]
            consistency = {d: dictionary_consistency(conn, schema, d) for d in DATASETS}
            coverage = coverage_rows(conn, schema)
    except (ScoreError, ConfigError) as exc:
        print(f"score_patterns: {exc}", file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        print(f"score_patterns: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(report(schema, labels, scores, coverage, consistency, args.min_agreement), end="")
    if all(s.covered == 0 for s in scores):
        return 2
    inconsistent = [d for d, (ok, total) in consistency.items() if ok != total]
    if inconsistent:
        print(
            f"score_patterns: pattern_dict disagrees with the chain for {', '.join(inconsistent)}",
            file=sys.stderr,
        )
        return 1
    return 1 if gate_problems(scores, args.min_agreement) else 0


if __name__ == "__main__":
    sys.exit(main())
