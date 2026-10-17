"""Golden checks: compare a tool result with independent SQL on the same canonical tables (T5).

Independent means the SQL is written in the test, from the canonical tables, without the tool's
templates. Where the tool reads the inverted index, the golden SQL reads the base table: a
full-text query becomes a whole-word regex on lower(message) (`words_regex`), which matches the
`log_text` dictionary's lower-case whole words.

Usage:
    golden = Golden(schema)
    golden.total(result, "SELECT count(*) FROM {bgl} WHERE alert_tag = %s", ["KERNDTLB"])
    golden.rows(result, ["entry_id", "ts"], "SELECT entry_id, ts FROM {bgl} WHERE ... LIMIT 20")

`{bgl}`, `{os}`, `{bgl_idx}`, `{os_idx}` and `{owners}` are bound to the tables in `schema` through
the resolver, as in tests/support/aggregation_tools.py.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aggregation_tools import independent, row_dicts


def words_regex(word: str) -> str:
    """A regex for `word` as a whole lower-case word, as the log_text dictionary splits words."""
    return rf"(^|[^a-z0-9_]){re.escape(word.lower())}([^a-z0-9_]|$)"


def as_json(value: Any) -> Any:
    """A SQL value as the Evidence envelope carries it."""
    return value.isoformat() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class Golden:
    """Independent SQL against the canonical tables in `schema`."""

    schema: str

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
        return independent(self.schema, sql, list(params) if params is not None else None)

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        rows = self.query(sql, params)
        assert [len(row) for row in rows] == [1], f"golden SQL must return one value: {sql}"
        return rows[0][0]

    def total(self, result: dict[str, Any], sql: str, params: Sequence[Any] | None = None) -> int:
        """Assert the tool's total_matches equals the golden count; return it."""
        expected = int(self.scalar(sql, params))
        assert result["total_matches"] == expected, (result["total_matches"], expected, sql)
        return expected

    def rows(
        self, result: dict[str, Any], columns: Sequence[str], sql: str, params: Sequence[Any] | None = None
    ) -> list[tuple[Any, ...]]:
        """Assert the tool's rows, read in `columns`, equal the golden rows in order; return them."""
        got = [tuple(row[c] for c in columns) for row in row_dicts(result)]
        expected = [tuple(as_json(v) for v in row) for row in self.query(sql, params)]
        assert got == expected, sql
        return expected

    def value(self, got: Any, sql: str, params: Sequence[Any] | None = None) -> Any:
        """Assert one value of a tool result equals the golden scalar; return it."""
        expected = as_json(self.scalar(sql, params))
        assert got == expected, (got, expected, sql)
        return expected
