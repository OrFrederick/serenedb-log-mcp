"""`db.QueryResult`, `QueryResult.combine` and the numeric loader, without a database."""

from decimal import Decimal

import pytest

from serenedb_log_mcp.db import QueryResult, int_or_float
from serenedb_log_mcp.toolkit import QueryResultLike


def result(sql: str, params: list[object], ms: float, notes: list[str] | None = None) -> QueryResult:
    return QueryResult(
        total=1, rows=[(1,)], truncated=False, query_ms=ms, sql=[sql], params=[params], notes=notes or []
    )


def test_query_result_satisfies_the_evidence_contract() -> None:
    assert isinstance(result("SELECT 1", [], 1.0), QueryResultLike)


def test_query_result_keeps_sql_text_verbatim() -> None:
    sql = "SELECT count(*)\n  FROM t\n WHERE a = %s"

    assert result(sql, [1], 1.0).sql == [sql]


def test_combine_concatenates_statements_in_order_and_sums_time() -> None:
    first = result("SELECT count(*) FROM t WHERE a = %s", [1], 10.5, notes=["retried once"])
    second = result("SELECT a FROM t WHERE a = %s LIMIT %s", [1, 20], 4.25)
    third = result("SELECT min(ts) FROM t", [], 0.25, notes=["facet note"])

    combined = QueryResult.combine(first, second, third, total=57, rows=[(1,), (2,)])

    assert combined.sql == [first.sql[0], second.sql[0], third.sql[0]]
    assert combined.params == [[1], [1, 20], []]
    assert combined.query_ms == pytest.approx(15.0)
    assert combined.notes == ["retried once", "facet note"]


def test_combine_takes_total_and_rows_from_the_caller() -> None:
    combined = QueryResult.combine(result("SELECT 1", [], 1.0), total=57, rows=[[1, "a"], [2, "b"]])

    assert combined.total == 57
    assert combined.rows == [(1, "a"), (2, "b")]
    assert combined.truncated is True


def test_combine_lets_the_caller_override_truncated() -> None:
    combined = QueryResult.combine(result("SELECT 1", [], 1.0), total=3, rows=[(1,)], truncated=False)

    assert combined.truncated is False


def test_combine_drops_repeated_notes() -> None:
    a = result("SELECT 1", [], 1.0, notes=["SereneDB restarted"])
    b = result("SELECT 2", [], 1.0, notes=["SereneDB restarted"])

    assert QueryResult.combine(a, b, total=0, rows=[]).notes == ["SereneDB restarted"]


def test_combine_needs_at_least_one_result() -> None:
    with pytest.raises(ValueError, match="at least one"):
        QueryResult.combine(total=0, rows=[])


@pytest.mark.parametrize(
    ("value", "expected", "kind"),
    [
        (Decimal("2976951129"), 2976951129, int),
        (Decimal("2.000"), 2, int),
        (Decimal("12345678901234567890"), 12345678901234567890, int),
        (Decimal("1E+2"), 100, int),
        (Decimal("-7"), -7, int),
        (Decimal("3.75"), 3.75, float),
        (Decimal("0.1"), 0.1, float),
    ],
)
def test_int_or_float_never_returns_a_decimal(value: Decimal, expected: float, kind: type) -> None:
    converted = int_or_float(value)

    assert converted == expected
    assert type(converted) is kind


def test_int_or_float_maps_special_values_to_float() -> None:
    assert int_or_float(Decimal("Infinity")) == float("inf")
    nan = int_or_float(Decimal("NaN"))
    assert isinstance(nan, float)
    assert nan != nan
