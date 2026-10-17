"""What tools need to know about a Dataset before they build a Data Scope: its time range and the
values its filters accept, from one statement per Dataset, cached for the life of the process.

- `first` and `last` (the first and last Log Entry) resolve relative times such as 'last 7d' and
  give every baseline its Dataset bounds;
- `components`, `levels` and, for BGL, `alert_categories` (each with its Log Entry count) let
  `scope.validate_choices` reject a misspelt filter value with a "did you mean" hint instead of
  silently counting zero.

The cache key is the resolved table, so per-Role views (T22) or another SLM_SCHEMA get their own
entry. T5's `describe_corpus` can build on these facts instead of counting again.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import sql

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus.datasets import Relation, relation
from serenedb_log_mcp.db import QueryResult, fetch_all

__all__ = ["DatasetFacts", "Fetch", "clear_cache", "dataset_facts"]

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


_cache: dict[tuple[str, str], DatasetFacts] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Forget every cached Dataset, for example after `make derive` rebuilt the tables."""
    with _lock:
        _cache.clear()


def dataset_facts(dataset: str, role: Role | None = None, *, fetch: Fetch = fetch_all) -> DatasetFacts:
    """The facts of one Dataset as `role` sees it; one statement on first use, cached afterwards."""
    rel = relation(dataset, role)
    key = (rel.dataset.name, rel.table_name)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    found = _parse(rel.dataset.name, fetch(_statement(rel), (), role=role).rows)
    with _lock:
        return _cache.setdefault(key, found)


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


def _parse(dataset: str, rows: Sequence[Sequence[Any]]) -> DatasetFacts:
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
    )
