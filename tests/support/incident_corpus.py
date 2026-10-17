"""A small canonical corpus for `list_incidents` (T27), with every Incident known by design.

`build(database_url, schema)` builds the aggregation corpus of tests/support/aggregation_corpus.py
with the BGL rows below and the OpenStack rows below, then runs the real Incident derive steps
(`sql/derive/30_incidents.sql` and `35_os_incidents.sql`) over them.

BGL: the aggregation corpus (`pattern_key` is the alert category, else the template id), plus a
KERNDTLB wave on two midplanes. So KERNDTLB has two Incidents at the default gap:
- STORM: 100 Log Entries on NODE_B, 30 s apart (2005-07-20 from 10:00): a single-node storm;
- WAVE: 3 Log Entries on 2005-06-15 from 12:00: NODE_A at +0 s, NODE_C at +50 s, NODE_A at +100 s:
  two nodes on two midplanes of two racks, one Burst per node.
T_OLD holds 60 Log Entries (2 a day, four hours apart, in June), so 60 Incidents of one entry each.

OpenStack (all at UTC; `pattern_key` is the template id, as T9 sets it):
- T_FAIL ERROR "Instance failed to spawn": U1 at 2017-05-16 20:00:00 and 20:00:05, U2 at 20:30:00:
  two Incidents (the second starts 1,795 s after the first ends);
- T_API responses from nova-api, no instance: 6 with status 200 and 2 with status 503 on 2017-05-15
  at 06:25:02 and 06:25:30. Only the two 503s are Request Failures: one Incident on host nova-api;
- T_CRIT CRITICAL keystone line from nova-api on 2017-05-15 07:00:00: one Incident;
- T_WARN WARNING line: not a Request Failure;
- lifecycles: U3 starts its build and never finishes it; U4 completes build and teardown; U5
  completes its build, starts its teardown and never finishes it. So U3 and U5 are lifecycle
  Request Failures, with 2 and 4 Log Entries.
- 24 hourly INFO lines "VM Started" for U1 and U2 (T_START), which are not failures.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import aggregation_corpus
import psycopg
from aggregation_corpus import NODE_A, NODE_B, NODE_C, T_OLD, U1, U2
from psycopg import sql

import derive

U3 = "33333333-3333-3333-3333-333333333333"
U4 = "44444444-4444-4444-4444-444444444444"
U5 = "55555555-5555-5555-5555-555555555555"
T_FAIL = "c0c0c0c0c0c0c001"
T_API = "c0c0c0c0c0c0c002"
T_CRIT = "c0c0c0c0c0c0c003"
T_WARN = "c0c0c0c0c0c0c004"
T_START = "c0c0c0c0c0c0c005"
T_CLAIM = "c0c0c0c0c0c0c006"
T_IMAGE = "c0c0c0c0c0c0c007"
T_BUILT = "c0c0c0c0c0c0c008"
T_TERM = "c0c0c0c0c0c0c009"
T_DELETED = "c0c0c0c0c0c0c010"

WAVE_START = datetime(2005, 6, 15, 12, 0)
STORM_ENTRIES = aggregation_corpus.STORM
WAVE_ENTRIES = 3
KERNDTLB_ENTRIES = STORM_ENTRIES + WAVE_ENTRIES
T_OLD_ENTRIES = 60
API_LOG = "nova-api.log.1.2017-05-16_13:53:08"
COMPUTE_LOG = "nova-compute.log.1.2017-05-16_13:55:31"

# The OpenStack Request Failure Incidents of the precomputed table.
OS_CHAINED_INCIDENTS = 4  # T_FAIL 2, T_API 1, T_CRIT 1
OS_CHAINED_ENTRIES = 3 + 2 + 1
OS_LIFECYCLE = {U3: 2, U5: 4}
OS_INCIDENTS = OS_CHAINED_INCIDENTS + len(OS_LIFECYCLE)
OS_ENTRIES = OS_CHAINED_ENTRIES + sum(OS_LIFECYCLE.values())


def bgl_rows() -> list[dict[str, Any]]:
    rows = [row for row in aggregation_corpus.bgl_rows()]
    for offset, node in ((0, NODE_A), (50, NODE_C), (100, NODE_A)):
        rows.append(
            aggregation_corpus._bgl(
                WAVE_START + timedelta(seconds=offset),
                node,
                "KERNEL",
                "FATAL",
                "KERNDTLB",
                "data TLB error interrupt",
                aggregation_corpus.T_TLB,
                aggregation_corpus.F_TLB,
            )
        )
    rows.sort(key=lambda row: (row["ts"], row["node"]))
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def _os(
    ts: datetime,
    template: str,
    level: str,
    message: str,
    *,
    instance: str | None = None,
    log_file: str = COMPUTE_LOG,
    component: str = "nova.compute.manager",
    status: int | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "epoch_utc": (ts - datetime(1970, 1, 1)).total_seconds(),
        "source_id": "os-1",
        "log_file": log_file,
        "pid": 2931,
        "component": component,
        "level": level,
        "request_id": None,
        "instance_id": None,
        "instance_ref": instance,
        "endpoint": "GET /v2/{tenant}/servers/detail" if status is not None else None,
        "http_status": status,
        "latency_s": 0.25 if status is not None else None,
        "message": message,
        "template_id": template,
        "family_id": None,
        "pattern_key": template,
    }


def os_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hour in range(24):
        instance = U1 if hour % 2 == 0 else U2
        rows.append(
            _os(
                datetime(2017, 5, 15, hour),
                T_START,
                "INFO",
                f"[instance: {instance}] VM Started (Lifecycle Event)",
                instance=instance,
            )
        )
    for at, instance in (
        (datetime(2017, 5, 16, 20, 0, 0), U1),
        (datetime(2017, 5, 16, 20, 0, 5), U1),
        (datetime(2017, 5, 16, 20, 30, 0), U2),
    ):
        rows.append(
            _os(at, T_FAIL, "ERROR", f"[instance: {instance}] Instance failed to spawn", instance=instance)
        )
    api = (
        '10.11.10.1 "GET /v2/54fadb412c4e40cdbaed9335e4c35a9e/servers/detail HTTP/1.1" '
        "status: {} len: 1893 time: 0.25"
    )
    for i in range(6):
        at = datetime(2017, 5, 14, 20, 0) + timedelta(minutes=10 * i)
        rows.append(
            _os(
                at,
                T_API,
                "INFO",
                api.format(200),
                log_file=API_LOG,
                component="nova.osapi_compute.wsgi.server",
                status=200,
            )
        )
    for at in (datetime(2017, 5, 15, 6, 25, 2), datetime(2017, 5, 15, 6, 25, 30)):
        rows.append(
            _os(
                at,
                T_API,
                "INFO",
                api.format(503),
                log_file=API_LOG,
                component="nova.osapi_compute.wsgi.server",
                status=503,
            )
        )
    rows.append(
        _os(
            datetime(2017, 5, 15, 7, 0),
            T_CRIT,
            "CRITICAL",
            "Unable to validate token: Failed to fetch token data",
            log_file=API_LOG,
            component="keystonemiddleware.auth_token",
        )
    )
    rows.append(
        _os(
            datetime(2017, 5, 15, 7, 30),
            T_WARN,
            "WARNING",
            "Unknown base file: /var/lib/nova/instances/_base/x",
        )
    )
    lifecycle = (
        (U3, datetime(2017, 5, 15, 9, 0), ("claim", "image")),
        (U4, datetime(2017, 5, 15, 10, 0), ("claim", "image", "built", "term", "deleted")),
        (U5, datetime(2017, 5, 15, 11, 0), ("claim", "image", "built", "term")),
    )
    messages = {
        "claim": (T_CLAIM, "Attempting claim: memory 2048 MB, disk 20 GB, vcpus 1 CPU"),
        "image": (T_IMAGE, "Creating image"),
        "built": (T_BUILT, "Took 20.12 seconds to build instance."),
        "term": (T_TERM, "Terminating instance"),
        "deleted": (T_DELETED, "Deletion of /var/lib/nova/instances/{}_del complete"),
    }
    for instance, start, steps in lifecycle:
        for i, step in enumerate(steps):
            template, text = messages[step]
            rows.append(
                _os(
                    start + timedelta(seconds=10 * i),
                    template,
                    "INFO",
                    f"[instance: {instance}] {text.format(instance)}",
                    instance=instance,
                )
            )
    rows.sort(key=lambda row: row["ts"])
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def run_step(database_url: str, schema: str, name: str) -> None:
    """Run one derive step of sql/derive/ over `schema`, the way `make derive` runs it."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute("SELECT version()").fetchone()
        values = derive.variables(schema, "public", derive.serenedb_version(str(row[0]) if row else ""))
        text = derive.render((derive.SQL_DIR / "derive" / name).read_text(), values)
        for statement in derive.split_statements(text):
            conn.execute(statement)


def incident_rows(database_url: str, schema: str, query: str) -> list[tuple[Any, ...]]:
    """Run `query` with {inc} bound to the Incident table in `schema`."""
    statement = sql.SQL(query).format(inc=sql.Identifier(schema, "incident"))
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(statement).fetchall()]


def build(database_url: str, schema: str) -> None:
    """The canonical tables in `schema`, then the real Incident derive steps over them."""
    aggregation_corpus.build(database_url, schema, bgl=bgl_rows(), openstack=os_rows())
    for name in ("30_incidents.sql", "35_os_incidents.sql"):
        run_step(database_url, schema, name)


def drop(database_url: str, schema: str) -> None:
    aggregation_corpus.drop(database_url, schema)


__all__ = [
    "KERNDTLB_ENTRIES",
    "NODE_A",
    "NODE_B",
    "NODE_C",
    "OS_CHAINED_ENTRIES",
    "OS_CHAINED_INCIDENTS",
    "OS_ENTRIES",
    "OS_INCIDENTS",
    "OS_LIFECYCLE",
    "STORM_ENTRIES",
    "T_API",
    "T_CRIT",
    "T_FAIL",
    "T_OLD",
    "T_OLD_ENTRIES",
    "U1",
    "U2",
    "U3",
    "U5",
    "WAVE_ENTRIES",
    "build",
    "drop",
    "incident_rows",
    "run_step",
]
