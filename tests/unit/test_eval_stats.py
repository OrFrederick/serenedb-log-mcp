"""Wilson intervals, pass@k and pass^k for the eval report (T17)."""

import pytest

from stats import pass_at_k, pass_hat_k, wilson


def test_wilson_matches_the_research_example() -> None:
    low, high = wilson(40, 45)  # #3: "40/45 gives [0.765, 0.952]"

    assert low == pytest.approx(0.765, abs=0.001)
    assert high == pytest.approx(0.952, abs=0.001)


def test_wilson_stays_inside_zero_and_one() -> None:
    assert wilson(0, 3)[0] == 0.0
    assert wilson(3, 3)[1] == 1.0
    assert wilson(3, 3)[0] == pytest.approx(0.4385, abs=0.001)


def test_wilson_of_nothing_is_the_whole_range() -> None:
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError, match="successes"):
        wilson(4, 3)


def test_pass_hat_k_is_all_k_trials_passing() -> None:
    assert pass_hat_k(3, 3, 3) == 1.0
    assert pass_hat_k(3, 2, 3) == 0.0
    assert pass_hat_k(5, 4, 2) == pytest.approx(0.6)
    assert pass_hat_k(1, 1, 3) is None  # fewer trials than k


def test_pass_at_k_is_at_least_one_of_k_passing() -> None:
    assert pass_at_k(5, 4, 1) == pytest.approx(0.8)
    assert pass_at_k(3, 1, 3) == 1.0
    assert pass_at_k(3, 0, 2) == 0.0
    assert pass_at_k(2, 1, 3) is None


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="k >= 1"):
        pass_hat_k(3, 1, 0)
