"""The database access layer: every tool runs its SQL through this module (plan section 2.4).

What is true for every statement is written once here:

- it runs inside `BEGIN READ ONLY`, because SereneDB does not enforce
  `default_transaction_read_only`;
- a `threading.Timer` calls `conn.cancel_safe()` after `SLM_QUERY_TIMEOUT_S`, because SereneDB
  accepts `statement_timeout` but does not enforce it;
- on a lost connection (SereneDB 26.10.1 SIGSEGVs intermittently and restarts in a few seconds) the
  layer waits until `SELECT 1` answers on a new connection, retries the statements once and says
  so in `QueryResult.notes`;
- parameters are always bound, never formatted into the SQL;
- `numeric` results (for example `sum()` and SereneDB's `HUGEINT`) come back as `int` when they are
  integral and as `float` otherwise, never as `Decimal`, so the Evidence envelope serialises them
  as JSON numbers.

The layer never builds an `Evidence`; tools hand its `QueryResult` to `toolkit.evidence_tool`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from serenedb_log_mcp.toolkit import QueryResultLike

__all__ = [
    "QueryResult",
    "int_or_float",
]


@dataclass(frozen=True)
class QueryResult:
    """What every query helper returns (plan section 2.7).

    `sql` holds each statement exactly as it ran, with `%s` where parameters were bound, and
    `params` holds the bound values, one list per statement in the same order. `query_ms` is the
    wall-clock time of those statements only. `notes` carries what the agent must know about how
    the numbers were obtained, for example a retry after a database restart.
    """

    total: int
    rows: list[tuple[Any, ...]]
    truncated: bool
    query_ms: float
    sql: list[str]
    params: list[list[Any]]
    notes: list[str] = field(default_factory=list)

    @classmethod
    def combine(
        cls,
        *results: QueryResult,
        total: int,
        rows: Sequence[Sequence[Any]],
        truncated: bool | None = None,
    ) -> QueryResult:
        """One result for a tool that ran several statements, for example a search plus its facets.

        `sql` and `params` are concatenated in the order given, `query_ms` is summed and `notes`
        are merged without repeats. The caller decides `total` and `rows`; `truncated` defaults to
        `len(rows) < total`.
        """
        if not results:
            raise ValueError("QueryResult.combine needs at least one result")
        notes: list[str] = []
        for part in results:
            notes.extend(note for note in part.notes if note not in notes)
        kept = [tuple(row) for row in rows]
        return cls(
            total=total,
            rows=kept,
            truncated=len(kept) < total if truncated is None else truncated,
            query_ms=sum(part.query_ms for part in results),
            sql=[statement for part in results for statement in part.sql],
            params=[list(values) for part in results for values in part.params],
            notes=notes,
        )


if TYPE_CHECKING:

    def _conforms_to_the_evidence_contract(result: QueryResult) -> QueryResultLike:
        """mypy proves here that `QueryResult` is what `toolkit.evidence_tool` consumes."""
        return result


def int_or_float(value: Decimal) -> int | float:
    """A `numeric` value as JSON can carry it: `int` when integral, `float` otherwise."""
    if value.is_finite() and value == value.to_integral_value():
        return int(value)
    return float(value)
