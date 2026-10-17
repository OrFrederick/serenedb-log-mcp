"""The synthetic corpus for `top_patterns` (T28): the aggregation corpus with every Pattern keyed and a
Pattern catalogue, so each rank, Incident count, flag and hand-off has one obvious expected value.

BGL keeps the rows of tests/support/aggregation_corpus.py (2005-06-01 09:00 to 2005-07-31 14:00):
- T_OLD: 60 KERNEL INFO Log Entries, 10:00 and 14:00 every day of June, then the wording changes to
  T_NEW: 62 Log Entries every day of July; both in Pattern Family F_DRIFT, so T_OLD hands off to T_NEW;
  4 hours apart, each Log Entry is an Incident of its own;
- KERNDTLB: 100 Log Entries 30 s apart on one node on 2005-07-20: one Incident;
- APPSEV: 9 Log Entries, one every Wednesday at 09:00: 9 Incidents in 9 weeks (Chronic);
- MMCS and NEWCOMP lines carry no Pattern and are not ranked.
`epoch_utc` is recomputed from `ts` in US Pacific time, as the real loader does.

OpenStack (2017-05-14 20:00 to 2017-05-16 20:00:05), `ts` taken as UTC:
- T_OS_START: 24 INFO Log Entries, hourly on 2017-05-15, on instances U1 and U2: 24 Incidents;
- T_OS_API: 6 INFO API Log Entries every 10 min, no instance: 6 Incidents;
- T_OS_FAIL: 2 ERROR Log Entries 5 s apart on U1: 1 Incident, the only Request Failure Pattern.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any
from zoneinfo import ZoneInfo

import aggregation_corpus
import psycopg
from aggregation_corpus import T_NEW, T_OLD
from psycopg import sql

T_OS_START = "c0c0c0c0c0c0c001"
T_OS_API = "c0c0c0c0c0c0c002"
T_OS_FAIL = "c0c0c0c0c0c0c003"
F_OS = "f2f2f2f2f2f2f2f2"
PACIFIC = ZoneInfo("America/Los_Angeles")

BGL_PATTERNS = ("KERNDTLB", T_NEW, T_OLD, "APPSEV")
"""By Log Entries: 100, 62, 60, 9."""
ENTRIES = {"KERNDTLB": 100, T_NEW: 62, T_OLD: 60, "APPSEV": 9}
INCIDENTS = {"KERNDTLB": 1, T_NEW: 62, T_OLD: 60, "APPSEV": 9}
OS_ENTRIES = {T_OS_START: 24, T_OS_API: 6, T_OS_FAIL: 2}
OS_INCIDENTS = {T_OS_START: 24, T_OS_API: 6, T_OS_FAIL: 1}


def bgl_rows() -> list[dict[str, Any]]:
    rows = aggregation_corpus.bgl_rows()
    for row in rows:
        row["epoch_utc"] = row["ts"].replace(tzinfo=PACIFIC).timestamp()
    return rows


def os_rows() -> list[dict[str, Any]]:
    rows = aggregation_corpus.os_rows()
    for row in rows:
        row["epoch_utc"] = row["ts"].replace(tzinfo=UTC).timestamp()
        if row["level"] == "ERROR":
            template = T_OS_FAIL
        elif row["component"] == "nova.compute.manager":
            template = T_OS_START
        else:
            template = T_OS_API
        row["template_id"] = row["pattern_key"] = template
        row["family_id"] = F_OS
    return rows


def _catalogue(bgl: list[dict[str, Any]], openstack: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    found: dict[tuple[str, str], list[Any]] = {}
    for dataset, rows in (("bgl", bgl), ("openstack", openstack)):
        for row in rows:
            if row["template_id"] is None:
                continue
            key = (dataset, row["template_id"])
            text = row["message"] if row["template_id"] != T_OS_START else "[instance: <*>] VM Started"
            entry = found.setdefault(
                key, [dataset, row["template_id"], text, "sqlmask", None, row["family_id"],
                      row["component"], row["level"], 0, row["ts"], row["ts"],
                      row["epoch_utc"], row["epoch_utc"], text],
            )  # fmt: skip
            entry[8] += 1
            entry[9] = min(entry[9], row["ts"])
            entry[10] = max(entry[10], row["ts"])
            entry[11] = min(entry[11], row["epoch_utc"])
            entry[12] = max(entry[12], row["epoch_utc"])
    return [tuple(v) for v in found.values()]


PATTERN_COLUMNS = (
    ("dataset", "VARCHAR"),
    ("template_id", "VARCHAR"),
    ("template_text", "VARCHAR"),
    ("template_source", "VARCHAR"),
    ("event_id", "VARCHAR"),
    ("family_id", "VARCHAR"),
    ("component", "VARCHAR"),
    ("level", "VARCHAR"),
    ("entries", "BIGINT"),
    ("first_seen", "TIMESTAMP"),
    ("last_seen", "TIMESTAMP"),
    ("first_epoch", "DOUBLE"),
    ("last_epoch", "DOUBLE"),
    ("display_text", "VARCHAR"),
)


def build(database_url: str, schema: str) -> None:
    bgl, openstack = bgl_rows(), os_rows()
    aggregation_corpus.build(database_url, schema, bgl=bgl, openstack=openstack)
    with psycopg.connect(database_url, autocommit=True) as conn:
        target = sql.Identifier(schema, "pattern")
        conn.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                target,
                sql.SQL(", ").join(
                    sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t)) for c, t in PATTERN_COLUMNS
                ),
            )
        )
        values = sql.SQL(", ").join(
            sql.SQL("({})").format(
                sql.SQL(", ").join(
                    sql.SQL("CAST({} AS {})").format(sql.Literal(value), sql.SQL(t))
                    for value, (_, t) in zip(row, PATTERN_COLUMNS, strict=True)
                )
            )
            for row in _catalogue(bgl, openstack)
        )
        conn.execute(sql.SQL("INSERT INTO {} VALUES {}").format(target, values))


drop = aggregation_corpus.drop
