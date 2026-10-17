"""scripts/huge_setup.py and sql/remote/text_bench.sql (T20), without a database."""

from __future__ import annotations

import pytest

import huge_setup
from derive import render, split_statements
from serenedb_log_mcp.corpus.datasets import TB_COLUMNS


def statements(url: str = "https://h/text_bench/part_000.parquet") -> list[str]:
    return split_statements(render(huge_setup.SETUP_SQL.read_text(), huge_setup.variables("huge", url)))


def test_the_view_reads_exactly_one_file_with_canonical_column_names() -> None:
    view = statements()[0]

    assert view.startswith('CREATE OR REPLACE VIEW "huge".tb_entry AS')
    assert "read_parquet('https://h/text_bench/part_000.parquet')" in view
    for column in TB_COLUMNS:
        assert f" AS {column}" in view
    # The cast keeps Parquet row-group pruning on ts; timezone('UTC', ...) defeats it.
    assert 'CAST("Timestamp" AS TIMESTAMP) AS ts' in view
    assert "timezone(" not in view


def test_the_url_is_a_quoted_literal_never_raw_sql() -> None:
    view = statements("https://h/it's.parquet")[0]

    assert "read_parquet('https://h/it''s.parquet')" in view


def test_the_facts_take_the_range_from_the_footer_and_the_groups_from_one_pass() -> None:
    facts = next(s for s in statements() if "tb_facts" in s and s.startswith("CREATE OR REPLACE TABLE"))

    assert "parquet_metadata('https://h/text_bench/part_000.parquet')" in facts
    assert "'range' AS kind" in facts
    assert "'component'" in facts
    assert "'level'" in facts
    groups = [s for s in statements() if "tb_groups AS" in s]
    assert len(groups) == 1
    assert "GROUP BY component, level" in groups[0]


@pytest.mark.parametrize(
    ("zone", "utc"), [("Etc/UTC", True), ("UTC", True), ("GMT", True), ("Europe/Berlin", False)]
)
def test_the_setup_requires_a_utc_session(zone: str, utc: bool) -> None:
    assert huge_setup.is_utc(zone) is utc


def test_a_glob_url_is_refused_before_connecting(capsys: pytest.CaptureFixture[str]) -> None:
    assert huge_setup.main(["--url", "https://h/text_bench/*.parquet"]) == 2
    assert "no glob" in capsys.readouterr().err


def test_the_remote_sql_reads_only_the_configured_file_and_no_labels() -> None:
    text = huge_setup.SETUP_SQL.read_text().lower()

    assert "abnormal" not in text
    assert "anomaly_labels" not in text
    assert text.count("read_parquet(${url})") == 1
    for reader in ("read_csv", "read_text", "read_json"):
        assert reader not in text
