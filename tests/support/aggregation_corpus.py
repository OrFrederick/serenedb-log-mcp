"""A small synthetic canonical corpus for the aggregation tools (T6), with numbers known by design.

`build(database_url, schema)` creates the canonical tables `bgl_entry`, `os_entry` and `ownership`
in `schema` with exactly the canonical columns, fills them with the rows below, and builds the
inverted indexes with the real derive step (`sql/derive/90_indexes.sql`), so `query` filters work.
Unlike the Loghub 2k samples, these rows carry Pattern ids and Pattern Families (as T9 will fill
them), span seven whole weeks, and include a Component the Ownership Table does not map.

BGL, 2005-06-01 (Wednesday) to 2005-07-31 12:00 (Sunday); whole Monday weeks 2005-06-06 to
2005-07-18 (7 weeks):
- STEADY: 2 KERNEL INFO lines every day at 10:00 and 14:00 on NODE_A; Pattern T_OLD before
  2005-07-01 and T_NEW from then on, both in Pattern Family F_DRIFT (a wording change);
- STORM: 100 KERNEL FATAL KERNDTLB lines on NODE_B on 2005-07-20 from 10:00, every 30 s, Pattern T_TLB;
- APPSEV: 1 APP FATAL APPSEV line every Wednesday at 09:00 on NODE_C (9 lines), no Pattern;
- MMCS: 3 MMCS INFO lines on node 'NULL' (no rack) on 2005-06-02, no Pattern;
- NEWCOMP: 2 NEWCOMP INFO lines on NODE_A on 2005-07-30, a Component the Ownership Table lacks.

The Ownership Table repeats APP's row and gives MMCS two Teams, so a join that does not guard
against duplicate (dataset, component) rows would count those Log Entries twice.

OpenStack, 2017-05-14 20:00 to 2017-05-16 20:00:05:
- 24 nova.compute.manager INFO lines, hourly on 2017-05-15, instances U1 and U2 alternating;
- 6 nova.osapi_compute.wsgi.server INFO lines from 2017-05-14 20:00, every 10 min, no instance;
- 2 nova.compute.manager ERROR lines for U1 on 2017-05-16 at 20:00:00 and 20:00:05.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg import sql

import derive

NODE_A = "R00-M0-N0-C:J02-U01"
NODE_B = "R00-M1-N1-C:J03-U11"
NODE_C = "R01-M0-N2-C:J04-U01"
T_OLD = "a0a0a0a0a0a0a001"
T_NEW = "a0a0a0a0a0a0a002"
T_TLB = "b0b0b0b0b0b0b003"
F_DRIFT = "f0f0f0f0f0f0f0f0"
F_TLB = "f1f1f1f1f1f1f1f1"
U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"

BGL_FIRST = datetime(2005, 6, 1, 9, 0)
BGL_LAST = datetime(2005, 7, 31, 14, 0)
OS_FIRST = datetime(2017, 5, 14, 20, 0)
OS_LAST = datetime(2017, 5, 16, 20, 0, 5)
DRIFT_DAY = datetime(2005, 7, 1)
STORM_START = datetime(2005, 7, 20, 10, 0)
WHOLE_WEEKS = 7

STEADY = 122
STORM = 100
APPSEV = 9
MMCS = 3
NEWCOMP = 2
BGL_TOTAL = STEADY + STORM + APPSEV + MMCS + NEWCOMP
OS_COMPUTE_INFO = 24
OS_API = 6
OS_ERRORS = 2
OS_TOTAL = OS_COMPUTE_INFO + OS_API + OS_ERRORS

OWNERSHIP = (
    ("bgl", "KERNEL", "Kernel Team"),
    ("bgl", "APP", "App Team"),
    ("bgl", "APP", "App Team"),  # an exact duplicate row
    ("bgl", "MMCS", "Control Team"),
    ("bgl", "MMCS", "Ops Team"),  # a second Team for one Component
    ("openstack", "nova.compute.manager", "Compute Team"),
    ("openstack", "nova.osapi_compute.wsgi.server", "API Team"),
)

BGL_COLUMNS = (
    ("entry_id", "BIGINT"),
    ("ts", "TIMESTAMP"),
    ("epoch_utc", "DOUBLE"),
    ("node", "VARCHAR"),
    ("rack", "VARCHAR"),
    ("midplane", "VARCHAR"),
    ("nodecard", "VARCHAR"),
    ("component", "VARCHAR"),
    ("level", "VARCHAR"),
    ("alert_tag", "VARCHAR"),
    ("message", "VARCHAR"),
    ("template_id", "VARCHAR"),
    ("family_id", "VARCHAR"),
    ("pattern_key", "VARCHAR"),
)
OS_COLUMNS = (
    ("entry_id", "BIGINT"),
    ("ts", "TIMESTAMP"),
    ("epoch_utc", "DOUBLE"),
    ("source_id", "VARCHAR"),
    ("log_file", "VARCHAR"),
    ("pid", "BIGINT"),
    ("component", "VARCHAR"),
    ("level", "VARCHAR"),
    ("request_id", "VARCHAR"),
    ("instance_id", "VARCHAR"),
    ("instance_ref", "VARCHAR"),
    ("endpoint", "VARCHAR"),
    ("http_status", "INTEGER"),
    ("latency_s", "DOUBLE"),
    ("message", "VARCHAR"),
    ("template_id", "VARCHAR"),
    ("family_id", "VARCHAR"),
    ("pattern_key", "VARCHAR"),
)


def _topology(node: str) -> tuple[str | None, str | None, str | None]:
    parts = node.split("-")
    if not node.startswith("R") or len(parts) < 3:
        return None, None, None
    return parts[0], f"{parts[0]}-{parts[1]}", f"{parts[0]}-{parts[1]}-{parts[2]}"


def _bgl(
    ts: datetime,
    node: str,
    component: str,
    level: str,
    alert: str | None,
    message: str,
    template: str | None,
    family: str | None,
) -> dict[str, Any]:
    rack, midplane, nodecard = _topology(node)
    return {
        "ts": ts,
        "epoch_utc": ts.timestamp(),
        "node": node,
        "rack": rack,
        "midplane": midplane,
        "nodecard": nodecard,
        "component": component,
        "level": level,
        "alert_tag": alert,
        "message": message,
        "template_id": template,
        "family_id": family,
        "pattern_key": alert or template,
    }


def bgl_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    day = datetime(2005, 6, 1)
    while day <= datetime(2005, 7, 31):
        drifted = day >= DRIFT_DAY
        template = T_NEW if drifted else T_OLD
        message = "steady kernel message" if drifted else "steady kernel message over 5 seconds"
        for hour in (10, 14):
            rows.append(
                _bgl(day + timedelta(hours=hour), NODE_A, "KERNEL", "INFO", None, message, template, F_DRIFT)
            )
        day += timedelta(days=1)
    for i in range(STORM):
        rows.append(
            _bgl(
                STORM_START + timedelta(seconds=30 * i),
                NODE_B,
                "KERNEL",
                "FATAL",
                "KERNDTLB",
                "data TLB error interrupt",
                T_TLB,
                F_TLB,
            )
        )
    wednesday = datetime(2005, 6, 1, 9, 0)
    while wednesday <= datetime(2005, 7, 31):
        rows.append(
            _bgl(
                wednesday, NODE_C, "APP", "FATAL", "APPSEV", "ciod: failed to read message prefix", None, None
            )
        )
        wednesday += timedelta(days=7)
    for minute in range(MMCS):
        rows.append(
            _bgl(
                datetime(2005, 6, 2, 8, minute), "NULL", "MMCS", "INFO", None, "idoproxy starting", None, None
            )
        )
    for minute in range(NEWCOMP):
        rows.append(
            _bgl(
                datetime(2005, 7, 30, 12, minute),
                NODE_A,
                "NEWCOMP",
                "INFO",
                None,
                "new component hello",
                None,
                None,
            )
        )
    rows.sort(key=lambda row: row["ts"])
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def _os(ts: datetime, component: str, level: str, instance: str | None, message: str) -> dict[str, Any]:
    return {
        "ts": ts,
        "epoch_utc": ts.timestamp(),
        "source_id": "src1",
        "log_file": "nova-compute.log",
        "pid": 2931,
        "component": component,
        "level": level,
        "request_id": None,
        "instance_id": None,
        "instance_ref": instance,
        "endpoint": None,
        "http_status": None,
        "latency_s": None,
        "message": message,
        "template_id": None,
        "family_id": None,
        "pattern_key": None,
    }


def os_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hour in range(OS_COMPUTE_INFO):
        instance = U1 if hour % 2 == 0 else U2
        rows.append(
            _os(
                datetime(2017, 5, 15, hour),
                "nova.compute.manager",
                "INFO",
                instance,
                f"[instance: {instance}] VM Started (Lifecycle Event)",
            )
        )
    for i in range(OS_API):
        rows.append(
            _os(
                OS_FIRST + timedelta(minutes=10 * i),
                "nova.osapi_compute.wsgi.server",
                "INFO",
                None,
                '10.11.10.1 "GET /v2/servers/detail HTTP/1.1" status: 200 len: 1893 time: 0.2477829',
            )
        )
    for seconds in (0, 5):
        rows.append(
            _os(
                datetime(2017, 5, 16, 20, 0, seconds),
                "nova.compute.manager",
                "ERROR",
                U1,
                f"[instance: {U1}] Instance failed to spawn",
            )
        )
    rows.sort(key=lambda row: row["ts"])
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def _create(conn: psycopg.Connection[Any], schema: str, table: str, columns: tuple[tuple[str, str], ...],
            rows: list[dict[str, Any]]) -> None:  # fmt: skip
    target = sql.Identifier(schema, table)
    conn.execute(
        sql.SQL("CREATE TABLE {} ({})").format(
            target,
            sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t)) for c, t in columns),
        )
    )
    # Literal values, not bound parameters: a bound TIMESTAMP next to bound strings in one VALUES
    # list fails on SereneDB 26.10.1 ("Invalid unicode (byte sequence mismatch)").
    values = sql.SQL(", ").join(
        sql.SQL("({})").format(
            sql.SQL(", ").join(
                sql.SQL("CAST({} AS {})").format(sql.Literal(row[c]), sql.SQL(t)) for c, t in columns
            )
        )
        for row in rows
    )
    conn.execute(sql.SQL("INSERT INTO {} VALUES {}").format(target, values))


def build(database_url: str, schema: str) -> None:
    """Create the synthetic canonical tables, the Ownership Table and the indexes in `schema`."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        _create(conn, schema, "bgl_entry", BGL_COLUMNS, bgl_rows())
        _create(conn, schema, "os_entry", OS_COLUMNS, os_rows())
        owners = [{"dataset": d, "component": c, "team": t} for d, c, t in OWNERSHIP]
        _create(
            conn,
            schema,
            "ownership",
            (("dataset", "VARCHAR"), ("component", "VARCHAR"), ("team", "VARCHAR")),
            owners,
        )
        row = conn.execute("SELECT version()").fetchone()
        version = derive.serenedb_version(str(row[0]) if row else "")
        values = derive.variables(schema, "public", version)
        text = derive.render((derive.SQL_DIR / "derive" / "90_indexes.sql").read_text(), values)
        for statement in derive.split_statements(text):
            conn.execute(statement)


def drop(database_url: str, schema: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
