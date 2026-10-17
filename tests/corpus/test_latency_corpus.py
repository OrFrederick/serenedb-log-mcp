"""Golden tests for `latency_stats` over the full Base Corpus in SLM_SCHEMA (default slm).

The four slow spawns of #5 are pinned by instance id, spawn time and the time of their spawn Log
Entry: timing ranks them first, and the fifth, d7d68893, is flagged too but is not one of them (it is
reported as a Hypothesis). The ids are pinned here as expected values; the label file itself is read
only by scripts/ground_truth.py. Every other number is compared with independent SQL on os_entry,
which does not go through the os_instance table.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from latency_corpus import call, row_dicts
from psycopg import sql

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import get_settings, load_settings
from serenedb_log_mcp.corpus.datasets import relation

pytestmark = pytest.mark.corpus

SLOW_SPAWNS = [
    ("a445709b-6ad0-40ec-8860-bec60b6ca0c2", 50.72, "2017-05-14T21:43:33.762000"),
    ("544fd51c-4edc-4780-baae-ba1d80a0acfc", 41.81, "2017-05-14T21:08:19.736000"),
    ("1643649d-2f42-4303-bfcd-7798baec19f9", 31.27, "2017-05-14T21:47:20.593000"),
    ("ae651dff-c7ad-43d6-ac96-bbcd820ccca8", 29.46, "2017-05-14T21:09:11.081000"),
]
HYPOTHESIS = ("d7d68893-c44f-4e7c-8d4b-0e7801d62776", 26.69, "2017-05-14T21:45:04.509000")
API_REQUESTS = 98_733
INSTANCES_WITH_SPAWN = 2_066


@pytest.fixture(autouse=True)
def corpus_settings(slm_schema: str, database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [slm_schema, "os_instance"],
        ).fetchone()
    if not row or row[0] == 0:
        pytest.fail(
            f"{slm_schema}.os_instance is missing; run `make derive` (T11 adds the step)", pytrace=False
        )
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=slm_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)


def independent(query: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    """Run `query` with {os} bound to the canonical OpenStack table."""
    statement = sql.SQL(query).format(os=relation("openstack").table)
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def within_timeout(result: dict[str, Any]) -> None:
    assert result["query_ms"] < get_settings().query_timeout_s * 1000


# ----------------------------------------------------------------- Phase-Duration Anomalies (#5)


def test_the_four_slow_spawns_rank_first_and_d7d68893_is_flagged_fifth() -> None:
    result = call(role="engineer", metric="spawn", limit=6)

    rows = row_dicts(result)
    assert [(r["instance_id"], r["spawn_s"], r["spawn_ts"]) for r in rows[:4]] == SLOW_SPAWNS
    assert (rows[4]["instance_id"], rows[4]["spawn_s"], rows[4]["spawn_ts"]) == HYPOTHESIS
    assert [r["flagged"] for r in rows] == [True, True, True, True, True, False]
    assert all(r["source_id"] == "os-3" for r in rows[:5])
    summary = result["summary"]
    assert summary["flagged_total"] == 5
    assert summary["instances_total"] == INSTANCES_WITH_SPAWN
    assert (summary["median_s"], summary["mad_s"]) == (19.74, 0.42)
    assert [r["spawn_z"] for r in rows[:5]] == [49.8, 35.4, 18.5, 15.6, 11.2]
    within_timeout(result)


def test_the_ranking_matches_an_independent_parse_of_the_spawn_log_entries() -> None:
    expected = independent(
        "SELECT instance_ref, "
        "CAST(regexp_extract(message, 'Took ([0-9.]+) seconds to spawn', 1) AS DOUBLE) AS s, ts "
        "FROM {os} WHERE message LIKE '%%seconds to spawn the instance on the hypervisor%%' "
        "ORDER BY s DESC LIMIT 5"
    )

    rows = row_dicts(call(role="engineer", metric="spawn", limit=5))
    assert [(r["instance_id"], r["spawn_s"], r["spawn_ts"]) for r in rows] == [
        (instance, seconds, ts.isoformat()) for instance, seconds, ts in expected
    ]


def test_build_time_ranks_the_same_five_instances() -> None:
    rows = row_dicts(call(role="team_lead", metric="build", limit=5))

    assert [r["instance_id"] for r in rows] == [i for i, _, _ in SLOW_SPAWNS] + [HYPOTHESIS[0]]
    assert all(r["flagged"] for r in rows)


def test_the_slow_spawns_spent_the_time_creating_their_image() -> None:
    rows = row_dicts(call(role="engineer", metric="spawn", limit=4))

    assert [r["image_s"] for r in rows] == [44.454, 34.647, 25.217, 22.444]
    assert all(r["image_z"] > 30 for r in rows)
    assert all(abs(r["paused_z"]) < 2 for r in rows)


def test_text_search_finds_nothing_on_the_slow_spawns() -> None:
    # The #5 claim: timing finds them, text does not. Every Log Entry that names them is INFO.
    ids = [i for i, _, _ in SLOW_SPAWNS]
    result = independent(
        "SELECT instance_ref, count(*), count(*) FILTER (WHERE level <> 'INFO') FROM {os} "
        "WHERE instance_ref = ANY(%s::VARCHAR[]) GROUP BY 1 ORDER BY 1",
        [ids],
    )

    assert result == [(instance, 28, 0) for instance in sorted(ids)]


# ----------------------------------------------------------------- API latency and Latency Degradation


def test_api_latency_per_endpoint_matches_independent_sql() -> None:
    result = call(role="team_lead", metric="api", limit=50)

    expected = independent(
        "SELECT endpoint, count(*), round(quantile_cont(latency_s, 0.5), 4), "
        "round(quantile_cont(latency_s, 0.95), 4), "
        "round(quantile_cont(latency_s, 0.99), 4), round(max(latency_s), 4) FROM {os} "
        "WHERE latency_s IS NOT NULL GROUP BY 1"
    )
    assert {tuple(row) for row in result["rows"]} == set(expected)
    assert result["total_matches"] == len(expected) == 33
    assert result["summary"]["requests_total"] == API_REQUESTS
    assert result["rows"][0][:2] == ["GET /v2/{tenant}/servers/detail", 67_716]
    within_timeout(result)


def test_five_minute_buckets_flag_one_latency_degradation() -> None:
    result = call(role="engineer", metric="api", group_by="bucket", bucket="5min", limit=10)

    summary = result["summary"]
    assert summary["buckets_total"] == 355
    assert summary["rising_total"] == 1
    assert summary["first_rising_bucket"] == summary["last_rising_bucket"] == "2017-05-17T06:25:00"
    assert (summary["baseline_p95_s"], summary["baseline_p99_s"]) == (0.3778, 0.507)
    rising = [r for r in row_dicts(result) if r["rise"]]
    assert [(r["bucket"], r["n"], r["p95_s"], r["max_s"]) for r in rising] == [
        ("2017-05-17T06:25:00", 35, 2.2404, 4.1645)
    ]
    within_timeout(result)


def test_hourly_buckets_show_no_latency_degradation() -> None:
    result = call(role="manager", metric="api", group_by="bucket", bucket="hour", limit=50)

    assert result["summary"]["rising_total"] == 0
    assert result["total_matches"] == 31
    assert result["truncated"] is False


# ----------------------------------------------------------------- the ground-truth score (#5)


def test_ground_truth_scores_four_of_four_at_the_default_threshold() -> None:
    import ground_truth

    try:
        labels = ground_truth.read_labels(get_settings())
    except ground_truth.GroundTruthError as exc:
        pytest.skip(str(exc))
    spawn = ground_truth.measure_metric("spawn", set(labels))

    default = next(s for s in spawn.scores if s.min_z == ground_truth.DEFAULT_MIN_Z)
    assert default.recall == (4, 4)
    assert default.precision == (4, 5)
    assert default.unlabelled_flagged == [HYPOTHESIS[0]]
    assert list(default.label_ranks.values()) == [1, 2, 3, 4]
