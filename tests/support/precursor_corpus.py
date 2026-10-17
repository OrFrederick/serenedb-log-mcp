"""A small canonical corpus for `find_precursors` and `list_incidents(similar_to=...)` (T16).

Every precursor, delay and look-alike is known by design. `build(database_url, schema)` builds the
canonical tables with the rows below (tests/support/aggregation_corpus.py) and runs the real
Incident derive steps over them, so `list_incidents(similar_to=...)` reads a real Incident table.

BGL, precursors (2005-08-01 to 2005-08-10):
- TARGET: one KERNREC Log Entry a day at 12:00:00 on TARGET_NODES[i] (i = 0..9): ten Incidents.
- KERNTERM on the same node, SAME_NODE_DELAYS[i] seconds before targets 0..5 (20 to 50 s,
  median 30 s): the same-source precursor;
- KERNTERM on OTHER_NODE 10 s before targets 6 and 7: counts only when same_source is false;
- KERNMC at exactly the target's time on the target's node: a same-timestamp artefact that the
  minimum lag of 1 s never counts;
- T_WARN on the target's node 45 s before targets 0 and 1: support 2, below min_support 3;
- T_BEAT on BEAT_NODE at 30 s before every full hour over the whole Dataset: it precedes every
  target when same_source is false, but every hourly probe too, so its lift is 1.

BGL, look-alikes (2005-09-01 to 2005-09-12), KERNPAN (Component KERNEL) with T_A and T_B
(Component APP) on the same nodes:
- E1 (2005-09-01, one node): KERNPAN, T_A, T_B. Signature {KERNPAN, T_A, T_B}.
- E2 (2005-09-03, one node): KERNPAN, T_A. Signature {KERNPAN, T_A}: Jaccard 2/3.
- E4 (2005-09-07, three nodes of one midplane): KERNPAN, T_A, T_B. Jaccard 1, another Shape.
- REF (2005-09-10, one node): KERNPAN, T_A, T_B. KERNPAN incident_id REF_ID.
- LATER (2005-09-12, one node): KERNPAN, T_A, T_B, after REF, so never a look-alike of it.

OpenStack (2017-05-15): T_SPAWN INFO 20 s before T_FAIL ERROR on instances U1, U2 and U3, an
hour apart: three Incidents of T_FAIL, each preceded on its instance.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import aggregation_corpus
import incident_corpus

TARGET = "KERNREC"
TARGET_NODES = tuple(f"R1{i}-M0-N0-C:J02-U01" for i in range(10))
OTHER_NODE = "R20-M0-N0-C:J02-U01"
BEAT_NODE = "R21-M0-N0-C:J02-U01"
SAME_NODE_DELAYS = (20, 30, 30, 40, 50, 30)
OTHER_NODE_TARGETS = (6, 7)
OTHER_NODE_DELAY = 10
WARN_TARGETS = (0, 1)
WARN_DELAY = 45
FIRST_DAY = datetime(2005, 8, 1)
T_WARN = "d0d0d0d0d0d0d001"
T_BEAT = "d0d0d0d0d0d0d002"
T_A = "d0d0d0d0d0d0d003"
T_B = "d0d0d0d0d0d0d004"
T_SPAWN = "d0d0d0d0d0d0d005"
T_FAIL = "d0d0d0d0d0d0d006"
U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"

LOOKALIKE = "KERNPAN"
P_NODES = {
    "E1": ("R30-M0-N0-C:J02-U01",),
    "E2": ("R31-M0-N0-C:J02-U01",),
    "E4": ("R32-M0-N0-C:J02-U01", "R32-M0-N0-C:J03-U01", "R32-M0-N1-C:J02-U01"),
    "REF": ("R33-M0-N0-C:J02-U01",),
    "LATER": ("R34-M0-N0-C:J02-U01",),
}
P_DAYS = {
    "E1": datetime(2005, 9, 1, 10),
    "E2": datetime(2005, 9, 3, 10),
    "E4": datetime(2005, 9, 7, 10),
    "REF": datetime(2005, 9, 10, 10),
    "LATER": datetime(2005, 9, 12, 10),
}
# KERNPAN Incidents in time order, so their incident_id is their rank.
P_IDS = {name: rank for rank, name in enumerate(("E1", "E2", "E4", "REF", "LATER"), start=1)}
REF_ID = P_IDS["REF"]
LAST_TS = datetime(2005, 9, 12, 23, 59, 30)


def _row(
    ts: datetime, node: str, component: str, level: str, alert: str | None, template: str | None, message: str
) -> dict[str, Any]:
    return aggregation_corpus._bgl(ts, node, component, level, alert, message, template, None)


def target_times() -> list[datetime]:
    return [FIRST_DAY + timedelta(days=i, hours=12) for i in range(len(TARGET_NODES))]


def _precursor_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, (at, node) in enumerate(zip(target_times(), TARGET_NODES, strict=True)):
        rows.append(_row(at, node, "KERNEL", "INFO", TARGET, None, "ido chip status changed"))
        rows.append(_row(at, node, "KERNEL", "FATAL", "KERNMC", None, "machine check interrupt"))
        if i < len(SAME_NODE_DELAYS):
            rows.append(
                _row(
                    at - timedelta(seconds=SAME_NODE_DELAYS[i]),
                    node,
                    "KERNEL",
                    "FATAL",
                    "KERNTERM",
                    None,
                    "rts: kernel terminated for reason 1004",
                )
            )
        if i in OTHER_NODE_TARGETS:
            rows.append(
                _row(
                    at - timedelta(seconds=OTHER_NODE_DELAY),
                    OTHER_NODE,
                    "KERNEL",
                    "FATAL",
                    "KERNTERM",
                    None,
                    "rts: kernel terminated for reason 1004",
                )
            )
        if i in WARN_TARGETS:
            rows.append(
                _row(
                    at - timedelta(seconds=WARN_DELAY),
                    node,
                    "KERNEL",
                    "WARNING",
                    None,
                    T_WARN,
                    "ddr: excessive soft failures",
                )
            )
    beat = FIRST_DAY + timedelta(minutes=59, seconds=30)
    while beat <= LAST_TS:
        rows.append(_row(beat, BEAT_NODE, "MMCS", "INFO", None, T_BEAT, "heartbeat"))
        beat += timedelta(hours=1)
    return rows


def _lookalike_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def pan(at: datetime, node: str) -> None:
        rows.append(_row(at, node, "KERNEL", "FATAL", LOOKALIKE, None, "kernel panic"))

    def other(at: datetime, node: str, template: str) -> None:
        rows.append(_row(at, node, "APP", "INFO", None, template, f"app message {template[-1]}"))

    for name in ("E1", "REF", "LATER"):
        node, start = P_NODES[name][0], P_DAYS[name]
        for seconds in (0, 60, 120):
            pan(start + timedelta(seconds=seconds), node)
        other(start + timedelta(seconds=30), node, T_A)
        other(start + timedelta(seconds=90), node, T_B)
    node, start = P_NODES["E2"][0], P_DAYS["E2"]
    pan(start, node)
    pan(start + timedelta(seconds=30), node)
    other(start + timedelta(seconds=10), node, T_A)
    start = P_DAYS["E4"]
    for i, node in enumerate(P_NODES["E4"]):
        pan(start + timedelta(seconds=10 * i), node)
    other(start + timedelta(seconds=5), P_NODES["E4"][0], T_A)
    other(start + timedelta(seconds=15), P_NODES["E4"][1], T_B)
    return rows


def bgl_rows() -> list[dict[str, Any]]:
    rows = _precursor_rows() + _lookalike_rows()
    rows.sort(key=lambda row: (row["ts"], row["node"], row["pattern_key"]))
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def os_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hour, instance in enumerate((U1, U2, U3)):
        at = datetime(2017, 5, 15, 10 + hour)
        rows.append(
            incident_corpus._os(
                at - timedelta(seconds=20),
                T_SPAWN,
                "INFO",
                f"[instance: {instance}] Spawning instance",
                instance=instance,
            )
        )
        rows.append(
            incident_corpus._os(
                at, T_FAIL, "ERROR", f"[instance: {instance}] Instance failed to spawn", instance=instance
            )
        )
    rows.sort(key=lambda row: row["ts"])
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def build(database_url: str, schema: str) -> None:
    """The canonical tables in `schema`, then the real Incident derive steps over them."""
    aggregation_corpus.build(database_url, schema, bgl=bgl_rows(), openstack=os_rows())
    for name in ("30_incidents.sql", "35_os_incidents.sql"):
        incident_corpus.run_step(database_url, schema, name)


def drop(database_url: str, schema: str) -> None:
    aggregation_corpus.drop(database_url, schema)


def expected_baseline(
    key: str,
    window_s: int,
    *,
    same_source: bool,
    sources: set[str] | None = None,
    step_s: int = 3600,
    min_lag_s: int = 1,
) -> tuple[int, int]:
    """(probes hit, probes total) for Pattern `key` over the BGL rows, by brute force.

    The probe grid is the tool's, written out naively: one probe every `step_s` seconds on the UTC
    clock, from the first full step that leaves a whole window after the first Log Entry, to the
    last Log Entry. A probe (per source in `sources` when `same_source`) is hit when a Log Entry of
    `key` falls `min_lag_s` to `window_s` seconds before it.
    """
    rows = bgl_rows()
    lo = min(row["epoch_utc"] for row in rows)
    hi = max(row["epoch_utc"] for row in rows)
    first = -(-(lo + window_s) // step_s) * step_s
    probes = []
    probe = first
    while probe <= hi:
        probes.append(probe)
        probe += step_s
    units = sorted(sources or ()) if same_source else [""]
    hits = 0
    for source in units:
        times = [
            row["epoch_utc"]
            for row in rows
            if row["pattern_key"] == key and (not same_source or row["node"] == source)
        ]
        hits += sum(1 for p in probes if any(p - window_s <= t <= p - min_lag_s for t in times))
    return hits, len(probes) * len(units)
