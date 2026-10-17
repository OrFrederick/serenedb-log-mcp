"""What tools need to know about a Dataset before they build a Data Scope: its time range and the
values its filters accept, from one statement per Dataset, cached for the life of the process.

- `first` and `last` (the first and last Log Entry) resolve relative times such as 'last 7d' and
  give every baseline its Dataset bounds;
- `components`, `levels` and, for BGL, `alert_categories` (each with its Log Entry count) let
  `scope.validate_choices` reject a misspelt filter value with a "did you mean" hint instead of
  silently counting zero.

The cache key is the resolved table, so per-Role views (T22) or another SLM_SCHEMA get their own
entry. T5's `describe_corpus` builds on these facts instead of counting again, plus a
`DatasetProfile` (the Pattern count and the Team of each Component), cached the same way. Both keep
the `QueryResult` that produced them, so Evidence served from the cache still names the statement
behind every number.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg import sql

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus.datasets import Relation, relation
from serenedb_log_mcp.db import QueryResult, fetch_all

__all__ = ["DatasetFacts", "DatasetProfile", "Fetch", "clear_cache", "dataset_facts", "dataset_profile"]

Fetch = Callable[..., QueryResult]
"""`db.fetch_all`, or a stand-in with the same signature in tests."""


@dataclass(frozen=True)
class DatasetFacts:
    """One Dataset's size, time range and filter values with their Log Entry counts."""

    dataset: str
    entries: int
    first: datetime | None
    last: datetime | None
    components: Mapping[str, int]
    levels: Mapping[str, int]
    alert_categories: Mapping[str, int]
    result: QueryResult | None = field(default=None, compare=False, repr=False)
    """The statement that produced these facts, with its SQL, parameters and query time."""


@dataclass(frozen=True)
class DatasetProfile:
    """What describe_corpus adds to the facts: the Pattern count and the Team of each Component."""

    dataset: str
    patterns: int
    """Distinct Pattern ids (template_id) in the Dataset; 0 before the Pattern dictionary (T9)."""
    entries_with_pattern: int
    """Log Entries that carry a Pattern id."""
    teams: Mapping[str, str]
    """Component -> Team, from the Ownership Table (a stub until T25)."""
    result: QueryResult | None = field(default=None, compare=False, repr=False)


_cache: dict[tuple[str, str], DatasetFacts] = {}
_profiles: dict[tuple[str, str], DatasetProfile] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Forget every cached Dataset, for example after `make derive` rebuilt the tables."""
    with _lock:
        _cache.clear()
        _profiles.clear()


def dataset_facts(dataset: str, role: Role | None = None, *, fetch: Fetch = fetch_all) -> DatasetFacts:
    """The facts of one Dataset as `role` sees it; one statement on first use, cached afterwards."""
    rel = relation(dataset, role)
    key = (rel.dataset.name, rel.table_name)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    result = fetch(_statement(rel), (), role=role)
    found = _parse(rel.dataset.name, result.rows, result)
    with _lock:
        return _cache.setdefault(key, found)


def dataset_profile(dataset: str, role: Role | None = None, *, fetch: Fetch = fetch_all) -> DatasetProfile:
    """The Pattern count and Teams of one Dataset; one statement on first use, cached afterwards."""
    rel = relation(dataset, role)
    key = (rel.dataset.name, rel.table_name)
    with _lock:
        cached = _profiles.get(key)
    if cached is not None:
        return cached
    result = fetch(_profile_statement(rel), (rel.dataset.name,), role=role)
    patterns, covered, teams = 0, 0, {}
    for kind, component, team, distinct, assigned in result.rows:
        if kind == "patterns":
            patterns, covered = int(distinct or 0), int(assigned or 0)
        elif component is not None and team is not None:
            teams[str(component)] = str(team)
    found = DatasetProfile(rel.dataset.name, patterns, covered, teams, result)
    with _lock:
        return _profiles.setdefault(key, found)


def _profile_statement(rel: Relation) -> str:
    pattern = rel.column("pattern")
    return (
        sql.SQL(
            "SELECT 'patterns' AS kind, CAST(NULL AS VARCHAR) AS component, CAST(NULL AS VARCHAR) AS team, "
            "count(DISTINCT {pattern}) AS patterns, count({pattern}) AS with_pattern FROM {table}\n"
            "UNION ALL\n"
            "SELECT 'team', {component}, team, CAST(NULL AS BIGINT), CAST(NULL AS BIGINT) "
            "FROM {owners} WHERE dataset = %s"
        )
        .format(
            pattern=pattern,
            table=rel.table,
            component=sql.Identifier("component"),
            owners=rel.ownership_table,
        )
        .as_string(None)
    )


def _statement(rel: Relation) -> str:
    table = rel.table
    parts = [
        sql.SQL(
            "SELECT 'range' AS kind, CAST(NULL AS VARCHAR) AS value, count(*) AS n, min({ts}) AS first, "
            "max({ts}) AS last FROM {table}"
        ).format(ts=rel.column("time"), table=table)
    ]
    concepts = [("component", "component"), ("level", "level")]
    if "alert_category" in rel.columns:
        concepts.append(("alert_category", "alert_category"))
    for kind, concept in concepts:
        parts.append(
            sql.SQL(
                "SELECT {kind}, {column}, count(*), CAST(NULL AS TIMESTAMP), CAST(NULL AS TIMESTAMP) "
                "FROM {table} WHERE {column} IS NOT NULL GROUP BY {column}"
            ).format(kind=sql.Literal(kind), column=rel.column(concept), table=table)
        )
    return sql.SQL("\nUNION ALL\n").join(parts).as_string(None)


def _parse(dataset: str, rows: Sequence[Sequence[Any]], result: QueryResult | None = None) -> DatasetFacts:
    entries, first, last = 0, None, None
    values: dict[str, dict[str, int]] = {"component": {}, "level": {}, "alert_category": {}}
    for kind, value, count, low, high in rows:
        if kind == "range":
            entries, first, last = int(count), low, high
        elif value is not None and kind in values:
            values[kind][str(value)] = int(count)
    return DatasetFacts(
        dataset=dataset,
        entries=entries,
        first=first,
        last=last,
        components=values["component"],
        levels=values["level"],
        alert_categories=values["alert_category"],
        result=result,
    )
