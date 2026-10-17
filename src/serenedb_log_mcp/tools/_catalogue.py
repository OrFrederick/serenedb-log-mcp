"""Shared SQL and shaping for the Pattern catalogue tools `list_patterns` and `search_patterns` (T14).

Both return Patterns in the same columns (`COLUMNS`), so an id picked from either goes straight into
`pattern_ids` of count_by, trend and search_logs:

- `template_id`: the Pattern id;
- `component` and `level`: the most frequent Component and level among its Log Entries;
- `entries`, `first_seen`, `last_seen`: its Log Entries over the whole Dataset, with ts as logged,
  cut to the second (BGL is US Pacific local time);
- `family_id`: its Pattern Family, only when the family holds more than one Pattern (a wording
  drift such as a trailing "over N seconds"); a family of one is the Pattern itself, and its 32 hex
  characters on every row would cost about a sixth of the catalogue's tokens;
- `template_text`: the template text, `(no message)` for the Pattern of empty messages, and at most
  `TEXT_CHARS` characters (a longer text keeps its start and end around an ellipsis).

The catalogue of a large Dataset does not fit in one result, so `paginate` cuts it into pages of at
most `ROW_BUDGET_CHARS` characters of rows, whole Components together where they fit and a
Component that does not fit alone over several pages of its own (plan T14: "paged by Component").
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import sql

from serenedb_log_mcp import fts
from serenedb_log_mcp.corpus.datasets import Relation
from serenedb_log_mcp.db import QueryFailed
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.tools._aggregate import Q, bound, joined, q

__all__ = [
    "CHARS_PER_TOKEN",
    "CHOOSE_NOTE",
    "COLUMNS",
    "ELLIPSIS",
    "NO_MESSAGE",
    "PAGE_TOKENS",
    "ROW_BUDGET_CHARS",
    "SYNONYM_WEIGHT",
    "TEXT_CHARS",
    "WHOLE_DATASET_NOTE",
    "Page",
    "catalogue_row",
    "catalogue_statement",
    "component_index",
    "missing_catalogue",
    "paginate",
    "row_chars",
    "search_statement",
    "shape_text",
]

COLUMNS = (
    "template_id",
    "component",
    "level",
    "entries",
    "first_seen",
    "last_seen",
    "family_id",
    "template_text",
)
NO_MESSAGE = "(no message)"
"""The template text shown for the Pattern of NULL and blank messages (template_source 'empty')."""
NO_COMPONENT = "(no component)"
TEXT_CHARS = 240
"""Longest template text a row carries, the same cap as a Log Entry's message (plan section 2.3)."""
TEXT_TAIL = 70
"""Characters kept from the end of a longer template text, where BGL's Java traces name the cause."""
ELLIPSIS = "…"

PAGE_TOKENS = 8_000
"""Target size of one list_patterns result (plan T14: page when a page would exceed about 8k tokens)."""
ENVELOPE_TOKENS = 1_500
"""Tokens left for everything but the rows: SQL, Data Scope, summary, notes and field names (3,000
to 3,800 characters of JSON on the Base Corpus pages)."""
CHARS_PER_TOKEN = 2.0
"""Characters of list_patterns JSON per token. Measured on 2026-10-17 with `claude -p` and a fixed
system prompt (the Opus, Sonnet and Haiku 5.5 models gave the same counts) on whole Base Corpus
pages: 1.93 for a KERNEL page of hex ids and numbers (15,484 characters, 8,042 tokens) and 2.02
for an OpenStack page. A page therefore lands at about 8k tokens, below Claude Code's 10k warning."""
ROW_BUDGET_CHARS = math.floor((PAGE_TOKENS - ENVELOPE_TOKENS) * CHARS_PER_TOKEN)

SYNONYM_WEIGHT = 0.5
"""BM25 weight of a synonym match relative to a match of the asker's own word."""

CHOOSE_NOTE = (
    "Choose by meaning: keep only the Patterns whose template text is direct evidence for the "
    "question (<*> marks a masked variable part such as a number or a node) and pass their "
    "template_id values as pattern_ids to count_by, trend and search_logs. If no Pattern is direct "
    "evidence, choose none and state that as an Unknown."
)
WHOLE_DATASET_NOTE = (
    "entries, first_seen and last_seen cover the whole Dataset and count Log Entries, not Incidents. "
    "For a Data Scope (a time range, Components), pass the chosen ids to count_by or trend."
)


# Rows ------------------------------------------------------------------------------------------------


def shape_text(text: str | None) -> str | None:
    """A template text of at most TEXT_CHARS characters: start, an ellipsis and the last TEXT_TAIL."""
    if text is None or len(text) <= TEXT_CHARS:
        return text
    head = TEXT_CHARS - TEXT_TAIL - len(ELLIPSIS)
    return f"{text[:head]}{ELLIPSIS}{text[-TEXT_TAIL:]}"


def _second(value: Any) -> Any:
    return value.replace(microsecond=0).isoformat() if isinstance(value, datetime) else value


def catalogue_row(row: Sequence[Any]) -> list[Any]:
    """One row of `catalogue_statement` or `search_statement` as the agent reads it (see COLUMNS)."""
    template_id, component, level, entries, first, last, family_id, text = row[:8]
    return [
        template_id,
        component,
        level,
        int(entries),
        _second(first),
        _second(last),
        family_id,
        shape_text(text),
        *row[8:],
    ]


def row_chars(row: Sequence[Any]) -> int:
    """Characters the row adds to the result's JSON, separator included."""
    return len(json.dumps(list(row), ensure_ascii=False)) + 2


# Pages -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """One page of the catalogue: its 1-based number, rows and the Components they belong to."""

    number: int
    rows: tuple[tuple[Any, ...], ...]

    @property
    def components(self) -> list[str]:
        return list(dict.fromkeys(_component(row) for row in self.rows))


def _component(row: Sequence[Any]) -> str:
    return NO_COMPONENT if row[1] is None else str(row[1])


def paginate(rows: Sequence[Sequence[Any]], budget: int | None = None) -> list[Page]:
    """Cut catalogue rows, grouped by Component in order, into pages of at most `budget` characters
    (default `ROW_BUDGET_CHARS`, read at call time).

    Whole Components share a page while they fit; a Component larger than `budget` gets pages of its
    own, cut between rows (largest Patterns first, as the rows come). Every row is on exactly one
    page, in the given order. A single row larger than `budget` still gets a page. An empty
    catalogue is one empty page, so page 1 always exists.
    """
    budget = ROW_BUDGET_CHARS if budget is None else budget
    groups: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        groups.setdefault(_component(row), []).append(tuple(row))
    pages: list[list[tuple[Any, ...]]] = []
    current: list[tuple[Any, ...]] = []
    size = 0

    def close() -> None:
        nonlocal current, size
        if current:
            pages.append(current)
        current, size = [], 0

    for members in groups.values():
        need = sum(row_chars(row) for row in members)
        if need <= budget:
            if size + need > budget:
                close()
            current.extend(members)
            size += need
            continue
        close()
        for row in members:
            chars = row_chars(row)
            if current and size + chars > budget:
                close()
            current.append(row)
            size += chars
        close()
    close()
    return [Page(number, tuple(found)) for number, found in enumerate(pages or [[]], start=1)]


def component_index(pages: Sequence[Page]) -> dict[str, dict[str, Any]]:
    """Per Component: its Patterns, Log Entries and the pages it is on, for navigating the catalogue."""
    index: dict[str, dict[str, Any]] = {}
    for page in pages:
        for row in page.rows:
            entry = index.setdefault(_component(row), {"patterns": 0, "entries": 0, "pages": []})
            entry["patterns"] += 1
            entry["entries"] += int(row[3])
            if page.number not in entry["pages"]:
                entry["pages"].append(page.number)
    return index


# Statements ------------------------------------------------------------------------------------------


def _catalogue_cte(rel: Relation, dataset: str) -> Q:
    """The Dataset's Patterns with the size of each Pattern Family, as the CTE `catalogue`."""
    return q(
        """catalogue AS (
    SELECT template_id, component, level, entries, first_seen, last_seen, family_id,
           CASE WHEN template_source = 'empty' THEN {} ELSE template_text END AS template_text,
           count(*) OVER (PARTITION BY family_id) AS family_patterns
    FROM {} WHERE dataset = {}
)""",
        sql.Literal(NO_MESSAGE),
        rel.patterns_table,
        bound(dataset),
    )


_SHOWN = (
    "{p}template_id, {p}component, {p}level, {p}entries, {p}first_seen, {p}last_seen, "
    "CASE WHEN {p}family_patterns > 1 THEN {p}family_id END AS family_id, {p}template_text"
)


def catalogue_statement(
    rel: Relation,
    *,
    component: str | None = None,
    pattern_ids: Sequence[str] = (),
    family_id: str | None = None,
) -> Q:
    """The catalogue of `rel`'s Dataset in COLUMNS order, grouped by Component, largest first."""
    found: list[Q] = []
    if component is not None:
        found.append(q("component = {}", bound(component)))
    if pattern_ids:
        found.append(q("template_id = ANY({})", bound(list(pattern_ids), "::VARCHAR[]")))
    if family_id is not None:
        found.append(q("family_id = {}", bound(family_id)))
    where = q("\nWHERE {}", joined(" AND ", found)) if found else Q(sql.SQL(""))
    return q(
        "WITH {}\nSELECT " + _SHOWN.format(p="") + "\nFROM catalogue{}\n"
        "ORDER BY component NULLS LAST, entries DESC, template_id",
        _catalogue_cte(rel, rel.dataset.name),
        where,
    )


def search_statement(rel: Relation, words: Sequence[str], synonyms: Sequence[str] = ()) -> Q:
    """The Patterns of `rel`'s Dataset whose template text holds any of `words` or `synonyms`, in
    COLUMNS order plus the BM25 `score`, unsorted.

    The match runs on the catalogue's inverted index, whose dictionary stems English words. The
    asker's words score in full and the synonyms at SYNONYM_WEIGHT; the Dataset filter is
    score-neutral. There is no ORDER BY: a catalogue has a few hundred Patterns, so the caller sorts
    all matches itself, with a tie-break, which a BM25 sort in SQL must not have (#2 round 2).
    """
    if not words:
        raise ValueError("search_statement needs at least one word")
    text = sql.SQL("p.{}").format(sql.Identifier("template_text"))
    match = q("{} @@ {}", text, bound(" ".join(words)))
    if synonyms:
        weight = sql.Literal(SYNONYM_WEIGHT)
        match = q("({} OR {} @@ ({} ^ {}))", match, text, bound(" ".join(synonyms), "::TSQUERY"), weight)
    dataset, params = fts.filter_any(sql.SQL("p.{}").format(sql.Identifier("dataset")), [rel.dataset.name])
    return q(
        "WITH hits AS (\n    SELECT p.template_id, BM25(p.tableoid) AS score\n    FROM {} AS p\n"
        "    WHERE {} AND {}\n),\n{}\nSELECT " + _SHOWN.format(p="c.") + ", h.score\n"
        "FROM hits AS h JOIN catalogue AS c ON c.template_id = h.template_id",
        rel.patterns_index,
        Q(dataset, tuple(params)),
        match,
        _catalogue_cte(rel, rel.dataset.name),
    )


def missing_catalogue(exc: QueryFailed, rel: Relation) -> ScopeError | None:
    """The actionable error for a schema without the Pattern catalogue or its index, else None."""
    if exc.sqlstate != "42P01":  # undefined_table
        return None
    return ScopeError(
        f"The Pattern catalogue or its search index is missing in schema {rel.schema!r}: build it with "
        "`make derive` (the Pattern step, sql/derive/20_patterns.sql). Until then, "
        "count_by(group_by='pattern') lists the Pattern ids in a Data Scope."
    )
