"""Golden facts about the Patterns that `make derive` (T9) builds from the full Base Corpus.

They read SLM_SCHEMA (default slm) and the Loghub-2.0 labels in SLM_RAW_SCHEMA (lh2_bgl,
lh2_openstack), and need a derive with Pattern Tier 1 on. T25 re-pins the numbers if the
organisers' loader differs.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest
from psycopg import sql

import score_patterns
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import relation

OVER_N_SECONDS_PAIRS = (
    ("E218", "E112"),
    ("E63", "E34"),
    ("E70", "E40"),
    ("E78", "E43"),
    ("E119", "E67"),
    ("E191", "E92"),
)


@pytest.fixture(scope="session")
def pattern_schema(database_url: str, slm_schema: str) -> str:
    """SLM_SCHEMA once the Pattern step has built pattern_dict and pattern there; skips otherwise."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name IN ('pattern_dict', 'pattern')",
            [slm_schema],
        ).fetchone()
    if not row or row[0] < 2:
        problem = f"the Pattern tables are missing in schema {slm_schema!r}; run `make derive`"
        if os.environ.get("SLM_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}:
            pytest.fail(f"SLM_REQUIRE_DB is set, so this does not skip: {problem}", pytrace=False)
        pytest.skip(problem)
    return slm_schema


def fetch(
    database_url: str, schema: str, query: str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    """Run `query` with {bgl}, {os}, {dict} and {pattern} bound to the canonical names."""
    settings = load_settings(slm_schema=schema)
    bgl, os_ = relation("bgl", settings=settings), relation("openstack", settings=settings)
    statement = sql.SQL(query).format(
        bgl=bgl.table, os=os_.table, dict=bgl.pattern_dict_table, pattern=bgl.patterns_table
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def test_every_log_entry_of_the_base_corpus_has_a_pattern(database_url: str, pattern_schema: str) -> None:
    result = fetch(
        database_url,
        pattern_schema,
        "SELECT (SELECT count(template_id) FROM {bgl}), (SELECT count(family_id) FROM {bgl}), "
        "(SELECT count(template_id) FROM {os}), (SELECT count(family_id) FROM {os})",
    )

    assert result == [(4_747_963, 4_747_963, 207_820, 207_820)]


def test_alert_lines_keep_their_category_and_every_other_line_its_template_id(
    database_url: str, pattern_schema: str
) -> None:
    result = fetch(
        database_url,
        pattern_schema,
        "SELECT count(*) FILTER (WHERE alert_tag IS NOT NULL AND pattern_key = alert_tag), "
        "count(*) FILTER (WHERE alert_tag IS NULL AND pattern_key = template_id), "
        "(SELECT count(*) FILTER (WHERE pattern_key = template_id) FROM {os}) FROM {bgl}",
    )

    assert result == [(348_460, 4_747_963 - 348_460, 207_820)]


def test_coverage_per_template_source(database_url: str, pattern_schema: str) -> None:
    result = fetch(
        database_url,
        pattern_schema,
        "SELECT dataset, template_source, count(*), sum(entries) FROM {pattern} GROUP BY 1, 2 ORDER BY 1, 2",
    )

    assert result == [
        ("bgl", "empty", 1, 34_470),
        ("bgl", "loghub2", 316, 4_630_955),
        ("bgl", "sqlmask", 148, 82_538),
        ("openstack", "empty", 1, 8),
        ("openstack", "loghub2", 48, 207_632),
        ("openstack", "sqlmask", 24, 180),
    ]


def test_the_dictionary_has_one_row_per_distinct_message(database_url: str, pattern_schema: str) -> None:
    result = fetch(
        database_url,
        pattern_schema,
        "SELECT dataset, count(*) FROM {dict} GROUP BY 1 ORDER BY 1",
    )

    assert result == [("bgl", 358_354), ("openstack", 139_782)]


def test_the_six_over_n_seconds_template_pairs_are_one_pattern_family_each(
    database_url: str, pattern_schema: str
) -> None:
    found = {
        event_id: (template_id, family_id)
        for event_id, template_id, family_id in fetch(
            database_url,
            pattern_schema,
            "SELECT event_id, template_id, family_id FROM {pattern} "
            "WHERE dataset = 'bgl' AND event_id IS NOT NULL",
        )
    }

    for short, long in OVER_N_SECONDS_PAIRS:
        assert found[short][1] == found[long][1], (short, long)
        assert found[short][0] != found[long][0], (short, long)
    families = {found[e][1] for pair in OVER_N_SECONDS_PAIRS for e in pair}
    assert len(families) == len(OVER_N_SECONDS_PAIRS)


def test_tier1_meets_the_merge_gate_and_only_the_truncated_e7_lines_disagree(
    database_url: str, pattern_schema: str
) -> None:
    labels = load_settings().raw_schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        scores = {
            d: score_patterns.score(d, score_patterns.outcomes(conn, pattern_schema, labels, d))
            for d in score_patterns.DATASETS
        }

    assert (scores["bgl"].lines, scores["bgl"].agreeing) == (4_631_261, 4_631_257)
    assert (scores["openstack"].lines, scores["openstack"].agreeing) == (207_632, 207_632)
    assert [(d.gold, d.chosen, d.lines) for d in scores["bgl"].disagreements] == [("E7", None, 4)]
    assert score_patterns.gate_problems(list(scores.values()), score_patterns.DEFAULT_MIN_AGREEMENT) == []


# ----------------------------------------------------------------- hand-offs for rankings (#64)


def test_the_empty_pattern_ranks_18th_by_entries_and_shows_as_no_message(
    database_url: str, pattern_schema: str
) -> None:
    ranked = fetch(
        database_url,
        pattern_schema,
        "SELECT template_source, display_text, entries FROM {pattern} WHERE dataset = 'bgl' "
        "ORDER BY entries DESC, template_id",
    )

    rank = next(i for i, row in enumerate(ranked, start=1) if row[0] == "empty")
    assert (rank, ranked[rank - 1][1:]) == (18, ("(no message)", 34_470))
    assert all(row[1] for row in ranked)


def test_the_register_dump_patterns_have_uninformative_texts(database_url: str, pattern_schema: str) -> None:
    result = fetch(
        database_url,
        pattern_schema,
        "SELECT p.template_text, count(d.message), min(p.entries) FROM {pattern} p "
        "JOIN {dict} d ON d.dataset = p.dataset AND d.template_id = p.template_id "
        "WHERE p.dataset = 'bgl' AND p.template_text IN ('<*>', '<*>:ffffffff <*>') "
        "GROUP BY 1 ORDER BY 1",
    )

    assert result == [("<*>", 1_140, 15_193), ("<*>:ffffffff <*>", 535, 4_149)]


def test_first_and_last_epoch_put_both_datasets_on_one_clock(database_url: str, pattern_schema: str) -> None:
    # BGL ts is US Pacific local time: its epochs are 7 or 8 hours after ts read as UTC.
    result = fetch(
        database_url,
        pattern_schema,
        "SELECT dataset, count(*) FILTER (WHERE first_epoch IS NULL OR last_epoch < first_epoch), "
        "min(first_epoch - epoch(first_seen)), max(first_epoch - epoch(first_seen)) "
        "FROM {pattern} GROUP BY 1 ORDER BY 1",
    )

    assert [(d, bad) for d, bad, _, _ in result] == [("bgl", 0), ("openstack", 0)]
    (_, _, bgl_low, bgl_high), (_, _, os_low, os_high) = result
    assert 7 * 3600 <= round(bgl_low) <= round(bgl_high) <= 8 * 3600
    assert (round(os_low), round(os_high)) == (0, 0)
