"""`latency_stats` against SereneDB, on the synthetic corpus of tests/support/latency_corpus.py.

Every expected number is computed in Python from the generated rows, independently of the SQL.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import pytest
from latency_corpus import (
    DELETE_SERVER,
    END,
    GET_DETAIL,
    GET_PATTERN,
    LONE_DELETE,
    POST_SERVERS,
    SPIKE_HOUR,
    START,
    Instance,
    call,
    call_error,
    instances,
    requests,
    row_dicts,
    stats,
    uuid_of,
)


def by_key(key: str) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for request in requests():
        groups[getattr(request, key)].append(request.latency_s)
    return groups


def hour_buckets() -> dict[datetime, list[float]]:
    groups: dict[datetime, list[float]] = defaultdict(list)
    for request in requests():
        groups[request.ts.replace(minute=0, second=0, microsecond=0)].append(request.latency_s)
    return groups


def iso(ts: datetime) -> str:
    return ts.isoformat()


# ----------------------------------------------------------------- API latency per group


def test_api_latency_per_endpoint_matches_the_generated_requests() -> None:
    result = call(role="engineer", metric="api")

    rows = row_dicts(result)
    expected = {endpoint: stats(values) for endpoint, values in by_key("endpoint").items()}
    assert {
        row["endpoint"]: {k: row[k] for k in ("n", "p50_s", "p95_s", "p99_s", "max_s")} for row in rows
    } == expected
    assert [row["endpoint"] for row in rows] == [
        GET_DETAIL,
        POST_SERVERS,
        DELETE_SERVER,
        "GET /v2/{tenant}/servers/{uuid}",
    ]
    assert result["total_matches"] == 4
    assert result["truncated"] is False
    assert result["data_scope"]["datasets"] == ["openstack"]
    assert result["summary"]["requests_total"] == len(requests())
    assert result["summary"]["overall"] == stats([r.latency_s for r in requests()])


def test_api_latency_per_source_and_overall() -> None:
    per_source = call(role="team_lead", metric="api", group_by="source")
    overall = call(role="manager", metric="api", group_by="none")

    expected = {source: stats(values) for source, values in by_key("source_id").items()}
    assert {
        r["source_id"]: {k: r[k] for k in ("n", "p50_s", "p95_s", "p99_s", "max_s")}
        for r in row_dicts(per_source)
    } == expected
    assert row_dicts(overall) == [{"scope": "all", **stats([r.latency_s for r in requests()])}]


def test_limit_truncates_groups_and_says_the_overall_numbers_cover_all_of_them() -> None:
    result = call(role="engineer", metric="api", limit=2)

    assert [row[0] for row in result["rows"]] == [GET_DETAIL, POST_SERVERS]
    assert result["total_matches"] == 4
    assert result["truncated"] is True
    assert any("overall" in note for note in result["notes"])


def test_endpoint_filter_accepts_a_raw_path_and_records_the_canonical_endpoint() -> None:
    result = call(
        role="engineer",
        metric="api",
        endpoint="post /v2/54fadb412c4e40cdbaed9335e4c35a9e/servers",
        group_by="none",
    )

    assert row_dicts(result) == [{"scope": "all", **stats(by_key("endpoint")[POST_SERVERS])}]
    assert result["data_scope"]["filters"]["endpoint"] == POST_SERVERS


def test_an_unknown_endpoint_gets_a_did_you_mean() -> None:
    message = call_error(role="engineer", metric="api", endpoint="GET /v2/{tenant}/server/detail")

    assert GET_DETAIL in message


def test_time_range_narrows_the_requests() -> None:
    result = call(
        role="engineer",
        metric="api",
        group_by="none",
        time_from="2017-05-16T01:00:00",
        time_to="2017-05-16T02:00:00",
    )

    window = [r.latency_s for r in requests() if datetime(2017, 5, 16, 1) <= r.ts < datetime(2017, 5, 16, 2)]
    assert row_dicts(result) == [{"scope": "all", **stats(window)}]
    assert result["data_scope"]["time_from"] == "2017-05-16T01:00:00"


def test_a_relative_time_resolves_against_the_last_log_entry() -> None:
    result = call(role="engineer", metric="api", group_by="none", time_from="last 1h")

    last = max(r.ts for r in requests())
    end = last.replace(microsecond=0) + timedelta(seconds=1)
    window = [r.latency_s for r in requests() if end - timedelta(hours=1) <= r.ts < end]
    assert row_dicts(result)[0]["n"] == len(window)
    assert result["data_scope"]["time_input"] == "last 1h"
    assert result["data_scope"]["time_to"] == iso(end)


def test_pattern_ids_filter_the_api_requests() -> None:
    result = call(role="engineer", metric="api", pattern_ids=[GET_PATTERN])

    assert [row[0] for row in result["rows"]] == [GET_DETAIL]
    assert result["data_scope"]["filters"]["pattern_ids"] == [GET_PATTERN]


def test_an_empty_time_range_returns_no_groups_and_says_so() -> None:
    result = call(
        role="engineer", metric="api", time_from="2017-05-15T00:00:00", time_to="2017-05-15T01:00:00"
    )

    assert result["rows"] == []
    assert result["total_matches"] == 0
    assert any("No " in note for note in result["notes"])


# ----------------------------------------------------------------- Latency Degradation


def test_hourly_buckets_flag_the_one_hour_that_rose() -> None:
    result = call(role="engineer", metric="api", group_by="bucket", bucket="hour")

    rows = row_dicts(result)
    buckets = hour_buckets()
    assert [row["bucket"] for row in rows] == [iso(b) for b in sorted(buckets)]
    for row in rows:
        start = datetime.fromisoformat(row["bucket"])
        assert {k: row[k] for k in ("n", "p50_s", "p95_s", "p99_s", "max_s")} == stats(buckets[start])
    base_p95 = statistics.median(stats(v)["p95_s"] for v in buckets.values() if len(v) >= 20)
    summary = result["summary"]
    assert summary["baseline_p95_s"] == pytest.approx(base_p95, abs=1e-4)
    assert summary["rising_total"] == 1
    assert [row["bucket"] for row in rows if row["rise"]] == [iso(SPIKE_HOUR)]
    spike = next(row for row in rows if row["rise"])
    assert spike["p95_ratio"] == pytest.approx(
        round(stats(buckets[SPIKE_HOUR])["p95_s"] / base_p95, 2), abs=0.011
    )
    assert result["data_scope"]["filters"]["bucket"] == "hour"


def test_a_baseline_period_replaces_the_scope_median() -> None:
    result = call(
        role="team_lead",
        metric="api",
        endpoint=GET_DETAIL,
        group_by="bucket",
        bucket="hour",
        time_from="2017-05-16T03:00:00",
        time_to="2017-05-16T04:00:00",
        baseline_from="2017-05-16T00:00:00",
        baseline_to="2017-05-16T03:00:00",
    )

    rows = row_dicts(result)
    assert [row["bucket"] for row in rows] == [iso(SPIKE_HOUR)]
    assert rows[0]["rise"] is True
    assert result["summary"]["baseline_p95_s"] == pytest.approx(0.29, abs=1e-4)
    assert result["summary"]["baseline_buckets"] == 3
    assert result["data_scope"]["filters"]["baseline_from"] == "2017-05-16T00:00:00"


def test_small_buckets_never_rise() -> None:
    # Ten-minute GET windows: the 5-minute buckets hold ten requests each, below the minimum.
    result = call(
        role="engineer",
        metric="api",
        endpoint=GET_DETAIL,
        group_by="bucket",
        bucket="5min",
        time_from="2017-05-16T02:50:00",
        time_to="2017-05-16T03:10:00",
    )

    assert {row["n"] for row in row_dicts(result)} == {10}
    assert not any(row["rise"] for row in row_dicts(result))
    assert any("20" in note for note in result["notes"])


def test_more_buckets_than_the_limit_keep_the_steepest_in_time_order() -> None:
    result = call(
        role="engineer", metric="api", endpoint=GET_DETAIL, group_by="bucket", bucket="15min", limit=5
    )

    rows = row_dicts(result)
    assert result["total_matches"] == 24
    assert result["truncated"] is True
    assert [row["bucket"] for row in rows] == sorted(row["bucket"] for row in rows)
    # The four 15-minute buckets of the spike hour rise, and all four are kept.
    assert sum(row["rise"] for row in rows) == 4 == result["summary"]["rising_total"]
    assert any("steepest" in note for note in result["notes"])


def test_rising_buckets_are_kept_before_steeper_buckets_too_small_to_rise() -> None:
    result = call(role="engineer", metric="api", group_by="bucket", bucket="15min", limit=4)

    rows = row_dicts(result)
    # The four 15-minute buckets of the spike hour rise. The lone 5 s DELETE at 07:00 is steeper
    # (p95 ratio about 17) but holds one request, so it must not push a rising bucket out.
    assert [row["bucket"] for row in rows] == [iso(SPIKE_HOUR + timedelta(minutes=15 * q)) for q in range(4)]
    assert all(row["rise"] for row in rows)
    assert iso(LONE_DELETE) not in [row["bucket"] for row in rows]


# ----------------------------------------------------------------- Phase-Duration Anomalies


def spawn_reference() -> tuple[float, float]:
    values = [i.spawn_s for i in instances()]
    median = statistics.median(values)
    return median, statistics.median(abs(v - median) for v in values)


def test_instances_rank_by_spawn_time_with_robust_z_scores() -> None:
    result = call(role="engineer", metric="spawn")

    rows = row_dicts(result)
    median, mad = spawn_reference()
    ranked: list[Instance] = sorted(instances(), key=lambda i: (-i.spawn_s, i.instance_id))
    assert [row["instance_id"] for row in rows] == [i.instance_id for i in ranked[:25]]
    first = rows[0]
    slowest = ranked[0]
    assert first["instance_id"] == uuid_of(7)
    assert first["spawn_s"] == slowest.spawn_s
    assert first["spawn_z"] == pytest.approx(round((slowest.spawn_s - median) / (1.4826 * mad), 1), abs=0.051)
    assert first["image_s"] == pytest.approx(slowest.image_s, abs=1e-3)
    assert first["spawn_ts"] == iso(slowest.spawn_ts)
    assert [row["instance_id"] for row in rows if row["flagged"]] == [uuid_of(7), uuid_of(19)]
    summary = result["summary"]
    assert summary["instances_total"] == 30
    assert summary["flagged_total"] == 2
    assert summary["median_s"] == pytest.approx(median)
    assert summary["mad_s"] == pytest.approx(mad)
    assert summary["min_z"] == 5.0
    assert any("Hypothesis" in note for note in result["notes"])


def test_build_time_ranks_the_same_slow_instances() -> None:
    result = call(role="engineer", metric="build", limit=3)

    assert [row[0] for row in result["rows"]][:2] == [uuid_of(7), uuid_of(19)]
    assert result["total_matches"] == 30
    assert result["truncated"] is True


def test_a_lower_threshold_flags_more_instances() -> None:
    # Instance 7 has z of about 135, instance 19 about 82; the ordinary jitter reaches about 2.
    strict = call(role="engineer", metric="spawn", min_z=100)
    loose = call(role="engineer", metric="spawn", min_z=1.5)

    assert strict["summary"]["flagged_total"] == 1
    assert loose["summary"]["flagged_total"] > 2


def test_spawn_percentiles_per_bucket_and_per_source() -> None:
    hourly = call(role="team_lead", metric="spawn", group_by="bucket", bucket="hour")
    per_source = call(role="team_lead", metric="spawn", group_by="none")

    assert row_dicts(per_source) == [{"scope": "all", **stats([i.spawn_s for i in instances()])}]
    counts = defaultdict(int)
    for i in instances():
        counts[iso(i.first_ts.replace(minute=0, second=0, microsecond=0))] += 1
    assert {row["bucket"]: row["n"] for row in row_dicts(hourly)} == counts


def test_the_instance_that_never_spawned_is_not_ranked() -> None:
    result = call(role="engineer", metric="spawn", limit=50)

    assert uuid_of(30) not in [row[0] for row in result["rows"]]
    assert result["total_matches"] == 30


def test_instance_metrics_reject_pattern_filters_and_endpoints() -> None:
    assert "metric='api'" in call_error(role="engineer", metric="spawn", endpoint=GET_DETAIL)
    assert "metric='api'" in call_error(role="engineer", metric="build", family_id=GET_PATTERN)


def test_time_range_selects_instances_by_their_first_log_entry() -> None:
    result = call(
        role="engineer", metric="spawn", time_from=iso(START), time_to=iso(START + timedelta(hours=1))
    )

    expected = [i for i in instances() if i.first_ts < START + timedelta(hours=1)]
    assert result["total_matches"] == len(expected) == 5
    assert END > START
