"""Values the aggregation tools compute in Python from exact SQL counts."""

from __future__ import annotations

import pytest

from serenedb_log_mcp.tools._aggregate import number, share


@pytest.mark.parametrize(
    ("count", "total", "expected"),
    [
        (1, 3, 33.33),
        (4_324_967, 4_955_783, 87.27),
        (1_234, 100_000, 1.23),
        # Below 1 %, four significant digits, so a small group never reads as 0.0.
        (3, 1_000, 0.3),
        (987, 100_000, 0.987),
        (1, 4_955_783, 2.018e-05),
        (0, 10, 0.0),
        (5, 0, 0.0),
    ],
)
def test_share_keeps_small_shares_visible(count: int, total: int, expected: float) -> None:
    assert share(count, total) == expected


def test_a_nonzero_count_never_has_a_zero_share() -> None:
    assert share(1, 10**12) > 0


@pytest.mark.parametrize(("value", "expected"), [(14.0, 14), (0.0, 0), (5655.5, 5655.5), (None, None)])
def test_integral_medians_read_as_counts(value: float | None, expected: int | float | None) -> None:
    assert number(value) == expected
    assert type(number(value)) is type(expected)
