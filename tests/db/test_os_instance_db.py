"""The OpenStack instance phases (T11) on the Loghub 2k sample, checked against an independent parse.

The derive step `sql/derive/60_os_instance.sql` turns the Log Entries that name an instance into one
row per instance: its phase durations and their robust z-scores (median and MAD over all instances).
This test recomputes every value in Python from the canonical OpenStack Log Entries of the same
scratch schema.
"""

from __future__ import annotations

import math
import re
import statistics
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import psycopg
import pytest
from psycopg import sql

import derive

INSTANCE_COLUMNS = [
    ("instance_id", "VARCHAR"),
    ("source_id", "VARCHAR"),
    ("entries", "BIGINT"),
    ("first_ts", "TIMESTAMP"),
    ("last_ts", "TIMESTAMP"),
    ("spawn_ts", "TIMESTAMP"),
    ("image_s", "DOUBLE"),
    ("paused_s", "DOUBLE"),
    ("spawn_s", "DOUBLE"),
    ("build_s", "DOUBLE"),
    ("image_z", "DOUBLE"),
    ("paused_z", "DOUBLE"),
    ("spawn_z", "DOUBLE"),
    ("build_z", "DOUBLE"),
]
PHASES = ("image_s", "paused_s", "spawn_s", "build_s")
SPAWN_RE = re.compile(r"\] Took ([0-9.]+) seconds to spawn the instance on the hypervisor")
BUILD_RE = re.compile(r"\] Took ([0-9.]+) seconds to build instance")
MAD_TO_SIGMA = 1.4826
OS_INSTANCE_STEP = Path(__file__).resolve().parents[2] / "sql" / "derive" / "60_os_instance.sql"


class Schemas(Protocol):
    raw: str
    slm: str


def rows(database_url: str, query: sql.Composable, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(query, params).fetchall()]


def first_epoch(lines: list[tuple[Any, ...]], suffix: str) -> float | None:
    found = [epoch for _, _, epoch, message, _ in lines if (message or "").endswith(suffix)]
    return min(found) if found else None


def duration(start: float | None, end: float | None) -> float | None:
    return None if start is None or end is None else round(end - start, 3)


def logged(lines: list[tuple[Any, ...]], pattern: re.Pattern[str]) -> float | None:
    """The value of the first matching line; `lines` are in time order."""
    values = [float(m.group(1)) for _, _, _, message, _ in lines if (m := pattern.search(message or ""))]
    return values[0] if values else None


def expected_instances(database_url: str, schema: str) -> dict[str, dict[str, Any]]:
    """Every instance's phases, recomputed in Python from the canonical OpenStack Log Entries."""
    entries = rows(
        database_url,
        sql.SQL(
            "SELECT instance_ref, source_id, ts, epoch_utc, message, entry_id FROM {} "
            "WHERE instance_ref IS NOT NULL"
        ).format(sql.Identifier(schema, "os_entry")),
    )
    by_instance: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for instance, source, ts, epoch, message, entry_id in entries:
        by_instance[instance].append((source, ts, epoch, message, entry_id))
    expected: dict[str, dict[str, Any]] = {}
    for instance, lines in by_instance.items():
        lines.sort(key=lambda line: (line[2], line[4]))
        spawn_lines = [ts for _, ts, _, message, _ in lines if SPAWN_RE.search(message or "")]
        expected[instance] = {
            "source_id": lines[0][0],
            "entries": len(lines),
            "first_ts": min(ts for _, ts, _, _, _ in lines),
            "last_ts": max(ts for _, ts, _, _, _ in lines),
            "spawn_ts": min(spawn_lines) if spawn_lines else None,
            "image_s": duration(
                first_epoch(lines, "] Creating image"), first_epoch(lines, "] VM Started (Lifecycle Event)")
            ),
            "paused_s": duration(
                first_epoch(lines, "] VM Paused (Lifecycle Event)"),
                first_epoch(lines, "] VM Resumed (Lifecycle Event)"),
            ),
            "spawn_s": logged(lines, SPAWN_RE),
            "build_s": logged(lines, BUILD_RE),
        }
    for phase in PHASES:
        values = [row[phase] for row in expected.values() if row[phase] is not None]
        median = statistics.median(values)
        scale = MAD_TO_SIGMA * statistics.median(abs(v - median) for v in values)
        for row in expected.values():
            value = row[phase]
            row[phase[:-2] + "_z"] = None if value is None or scale == 0 else (value - median) / scale
    return expected


def actual_instances(database_url: str, schema: str) -> dict[str, dict[str, Any]]:
    names = [name for name, _ in INSTANCE_COLUMNS]
    result = rows(
        database_url,
        sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(map(sql.Identifier, names)), sql.Identifier(schema, "os_instance")
        ),
    )
    return {row[0]: dict(zip(names[1:], row[1:], strict=True)) for row in result}


def close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def test_os_instance_has_the_documented_columns(database_url: str, derived_sample: Schemas) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        described = derive.relation_columns(conn, derived_sample.slm)

    assert described["os_instance"] == INSTANCE_COLUMNS


def test_every_instance_named_by_a_log_entry_gets_one_row(database_url: str, derived_sample: Schemas) -> None:
    expected = expected_instances(database_url, derived_sample.slm)
    actual = actual_instances(database_url, derived_sample.slm)

    assert sorted(actual) == sorted(expected)
    # The 2k sample holds 22 spawns but only 21 "Creating image" lines: the instance whose image
    # line fell before the sample stays in, with no image_s and no image_z.
    assert sum(row["spawn_s"] is not None for row in actual.values()) == 22
    missing_image = [row for row in actual.values() if row["image_s"] is None]
    assert len(missing_image) == 1
    assert missing_image[0]["image_z"] is None
    assert missing_image[0]["spawn_z"] is not None


@pytest.mark.parametrize("column", ["source_id", "entries", "first_ts", "last_ts", "spawn_ts", *PHASES])
def test_phase_columns_match_an_independent_parse(
    database_url: str, derived_sample: Schemas, column: str
) -> None:
    expected = expected_instances(database_url, derived_sample.slm)
    actual = actual_instances(database_url, derived_sample.slm)

    mismatches = {}
    for instance, row in expected.items():
        want, got = row[column], actual[instance][column]
        same = close(want, got) if column in PHASES else want == got
        if not same:
            mismatches[instance] = (want, got)
    assert mismatches == {}
    assert any(row[column] is not None for row in actual.values())


@pytest.mark.parametrize("column", ["image_z", "paused_z", "spawn_z", "build_z"])
def test_robust_z_scores_use_the_median_and_mad_of_all_instances(
    database_url: str, derived_sample: Schemas, column: str
) -> None:
    expected = expected_instances(database_url, derived_sample.slm)
    actual = actual_instances(database_url, derived_sample.slm)

    mismatches = {
        instance: (row[column], actual[instance][column])
        for instance, row in expected.items()
        if not close(row[column], actual[instance][column])
    }
    assert mismatches == {}


def test_spawn_ts_is_the_time_of_the_spawn_log_entry(database_url: str, derived_sample: Schemas) -> None:
    actual = actual_instances(database_url, derived_sample.slm)

    spawned = [row for row in actual.values() if row["spawn_s"] is not None]
    assert spawned
    for row in spawned:
        assert isinstance(row["spawn_ts"], datetime)
        assert row["first_ts"] <= row["spawn_ts"] <= row["last_ts"]


def test_a_reused_instance_takes_spawn_ts_and_spawn_s_from_the_same_line(database_url: str) -> None:
    # No instance of the corpus spawns twice, but a reused one would: both columns must come from
    # its first spawn line, and likewise build_s, not one from min(ts) and the other from max(value).
    schema = f"scratch_t11_reuse_{uuid.uuid4().hex[:8]}"
    instance = "00000001-0000-4000-8000-000000000001"
    first, second = datetime(2017, 5, 16, 1, 0, 0), datetime(2017, 5, 16, 2, 0, 0)
    lines = [
        (first, f"[instance: {instance}] Took 10.00 seconds to spawn the instance on the hypervisor."),
        (first.replace(second=1), f"[instance: {instance}] Took 11.00 seconds to build instance."),
        (second, f"[instance: {instance}] Took 30.00 seconds to spawn the instance on the hypervisor."),
        (second.replace(second=1), f"[instance: {instance}] Took 31.00 seconds to build instance."),
    ]
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {} (instance_ref VARCHAR, source_id VARCHAR, ts TIMESTAMP, "
                    "epoch_utc DOUBLE, message VARCHAR)"
                ).format(sql.Identifier(schema, "os_entry"))
            )
            for ts, message in lines:
                conn.execute(
                    sql.SQL("INSERT INTO {} VALUES (%s, 'os-1', %s, %s, %s)").format(
                        sql.Identifier(schema, "os_entry")
                    ),
                    [instance, ts, ts.replace(tzinfo=UTC).timestamp(), message],
                )
            row = conn.execute("SELECT version()").fetchone()
            version = derive.serenedb_version(str(row[0]) if row else "")
            derive.run(conn, [OS_INSTANCE_STEP], derive.variables(schema, "public", version))
            got = conn.execute(
                sql.SQL("SELECT spawn_ts, spawn_s, build_s FROM {}").format(
                    sql.Identifier(schema, "os_instance")
                )
            ).fetchall()
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))

    assert [tuple(r) for r in got] == [(first, 10.0, 11.0)]
