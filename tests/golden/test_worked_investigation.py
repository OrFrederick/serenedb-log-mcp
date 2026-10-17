"""Golden SQL for the worked investigation "Start-up path failures" (T18, #5).

Every number in the three answers of docs/worked-investigation/answers.yaml is pinned here by SQL
written in this file against the canonical tables, not by the tools' templates and not by the derived
Incident or instance tables: Incidents are chained, Spread and Shape counted, spawn and image phases
parsed from the raw messages, and percentile shares recomputed, in Python over the rows the SQL returns.

- `test_every_number_in_the_answers_is_pinned` walks every number verify_answer checks in the answers
  and requires a golden value for it, from the Evidence record the answer cites. A number that only
  restates a call parameter (a 600 s window, the 300 s gap) is a parameter, not a measurement.
- The headline tests pin #5's numbers by name: the /p/gb1 wave (26,426 Log Entries, 83 Incidents on 12
  days, 7 on all 1,024 I/O nodes), the 4 of 4 slow spawns found by timing while text search finds
  none, d7d68893 as the fifth flagged instance, the KERNDTLB trap sentence, the two Manager Data Scopes
  with the top-5 band, and the whole-Base-Corpus aggregation.
- `test_the_document_carries_the_pinned_numbers` checks docs/worked-investigation.md against them.

The numbers are pinned on the dev loader data; T25 re-pins them if the organisers' loader changes
line counts.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from aggregation_tools import independent

from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.export import Investigation, load_investigation
from serenedb_log_mcp.ledger import Ledger
from serenedb_log_mcp.verify import check_answer

pytestmark = pytest.mark.corpus

ROOT = Path(__file__).resolve().parents[2]
WI = ROOT / "docs" / "worked-investigation"
REPORT = ROOT / "docs" / "worked-investigation.md"
GAP_S = 300
WEEK_S = 7 * 86400

GB1 = "message LIKE '%Lustre mount FAILED%point /p/gb1%'"
BLOCK_ID = "message LIKE '%Lustre mount FAILED%block_id%'"
SLOW_SPAWNS = (
    "a445709b-6ad0-40ec-8860-bec60b6ca0c2",
    "544fd51c-4edc-4780-baae-ba1d80a0acfc",
    "1643649d-2f42-4303-bfcd-7798baec19f9",
    "ae651dff-c7ad-43d6-ac96-bbcd820ccca8",
)
FOUR = "(" + ", ".join(f"'{u}'" for u in SLOW_SPAWNS) + ")"
FIFTH = "d7d68893-c44f-4e7c-8d4b-0e7801d62776"
BAND_WHOLE = {"APPSEV", "KERNMC", "KERNRTSP", "KERNMNTF", "KERNSOCK"}
"""The whole-BGL top-5 band of #4 and #5 (formula C), stable under 2,000 random weightings."""
BAND_WAVE = {"KERNMNTF", "APPSEV", "KERNRTSP", "KERNMC", "KERNCON"}
KERNDTLB_SENTENCE = (
    "The largest KERNDTLB Incident has 152,182 Log Entries on 1 node (2005-06-11). "
    "The category has 152,734 Log Entries."
)
"""The wording T18 asks for, as it reads with the citations taken out."""


# The investigation as committed -----------------------------------------------------------------------


@cache
def investigation() -> Investigation:
    return load_investigation(WI / "answers.yaml")


@cache
def records() -> dict[str, dict[str, Any]]:
    return {r["evidence_id"]: r for r in Ledger(WI / "ledger.jsonl").all()}


def answer(role: Role) -> str:
    return next(a.answer for a in investigation().answers if a.role is role)


def uncited(text: str) -> str:
    return re.sub(r"\s*\[E[0-9]+\]", "", text)


# Independent computations --------------------------------------------------------------------------------


def utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def chain(rows: Iterable[Sequence[Any]], gap: float = GAP_S) -> list[list[Sequence[Any]]]:
    """Incidents: rows (epoch_utc first, then entry_id) chained where each follows within `gap`."""
    incidents: list[list[Sequence[Any]]] = []
    previous: float | None = None
    for row in sorted(rows, key=lambda r: (r[0], r[1])):
        if previous is None or row[0] - previous > gap:
            incidents.append([])
        incidents[-1].append(row)
        previous = row[0]
    return incidents


def shape(nodes: int, entries: int, midplanes: int) -> str:
    if nodes == 1:
        return "single-node storm" if entries >= 100 else "single-node isolated"
    if midplanes >= 64:
        return "machine-wide"
    return "multi-midplane" if midplanes > 1 else "one midplane"


def share(part: float, whole: float) -> float:
    return 100.0 * part / whole


@cache
def gb1_rows(schema: str) -> list[tuple[Any, ...]]:
    """Every /p/gb1 mount failure: epoch_utc, entry_id, ts as logged, node, midplane, rack."""
    return independent(
        schema, f"SELECT epoch_utc, entry_id, ts, node, midplane, rack FROM {{bgl}} WHERE {GB1}"
    )


def wave_incidents(schema: str) -> dict[str, Any]:
    incidents = chain(gb1_rows(schema))
    spread = [
        (len({r[3] for r in i}), len(i), len({r[4] for r in i}), len({r[5] for r in i})) for i in incidents
    ]
    widest = max(s[0] for s in spread)
    return {
        "entries": sum(len(i) for i in incidents),
        "incidents": len(incidents),
        "days": len({i[0][2].date() for i in incidents}),
        "widest_nodes": widest,
        "widest_incidents": sum(1 for s in spread if s[0] == widest),
        "largest": max(len(i) for i in incidents),
        "nodes": len({r[3] for r in gb1_rows(schema)}),
        "midplanes": len({r[4] for r in gb1_rows(schema)}),
        "racks": len({r[5] for r in gb1_rows(schema)}),
        "shapes": Counter(shape(n, e, m) for n, e, m, _ in spread),
    }


def spawn_phases(schema: str) -> dict[str, dict[str, float]]:
    """Per instance: spawn_s from nova's own line, image_s and paused_s from the phase lines."""
    rows = independent(
        schema,
        "SELECT instance_ref, epoch_utc, message FROM {os} WHERE instance_ref IS NOT NULL AND ("
        "message LIKE '%seconds to spawn the instance on the hypervisor%' "
        "OR message LIKE '%] Creating image' OR message LIKE '%] VM Started (Lifecycle Event)' "
        "OR message LIKE '%] VM Paused (Lifecycle Event)' OR message LIKE '%] VM Resumed (Lifecycle Event)')",
    )
    first: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    for instance, ep, message in rows:
        kind = next(
            (
                k
                for k, s in (
                    ("spawn", "to spawn the instance"),
                    ("image", "Creating image"),
                    ("started", "VM Started"),
                    ("paused", "VM Paused"),
                    ("resumed", "VM Resumed"),
                )
                if s in message
            ),
            None,
        )
        if kind and (kind not in first[instance] or ep < first[instance][kind][0]):
            first[instance][kind] = (ep, message)
    phases: dict[str, dict[str, float]] = {}
    for instance, seen in first.items():
        phase: dict[str, float] = {}
        if "spawn" in seen:
            phase["spawn_s"] = float(re.search(r"Took ([0-9.]+) seconds", seen["spawn"][1]).group(1))  # type: ignore[union-attr]
        if "image" in seen and "started" in seen:
            phase["image_s"] = round(seen["started"][0] - seen["image"][0], 3)
        if "paused" in seen and "resumed" in seen:
            phase["paused_s"] = round(seen["resumed"][0] - seen["paused"][0], 3)
        phases[instance] = phase
    return phases


def robust_z(values: Mapping[str, float]) -> dict[str, float]:
    median = statistics.median(values.values())
    mad = statistics.median(abs(v - median) for v in values.values())
    return {k: (v - median) / (1.4826 * mad) for k, v in values.items()}


@cache
def categories(schema: str, start: float | None, end: float | None) -> dict[str, dict[str, Any]]:
    """BGL alert categories in a Data Scope: entries, Incidents, midplanes, active weeks."""
    rows = independent(
        schema,
        "SELECT epoch_utc, entry_id, alert_tag, midplane, node FROM {bgl} WHERE alert_tag IS NOT NULL "
        "AND alert_tag <> '-'",
    )
    first = min(r[0] for r in rows) if start is None else start
    scoped = [r for r in rows if (start is None or r[0] >= start) and (end is None or r[0] < end)]
    by_tag: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for row in scoped:
        by_tag[row[2]].append(row)
    return {
        tag: {
            "entries": len(rows_),
            "incidents": len(chain(rows_)),
            "midplanes": len({r[3] for r in rows_ if r[3] is not None}),
            "active_weeks": len({math.floor((r[0] - first) / WEEK_S) for r in rows_}),
            "largest": max(chain(rows_), key=len),
            "first": first,
        }
        for tag, rows_ in by_tag.items()
    }


def daily_utc(rows: Iterable[Sequence[Any]]) -> Counter[date]:
    return Counter(datetime.fromtimestamp(r[0], UTC).date() for r in rows)


def weekly_utc(rows: Iterable[Sequence[Any]]) -> Counter[date]:
    days = daily_utc(rows)
    weeks: Counter[date] = Counter()
    for day, n in days.items():
        weeks[day - timedelta(days=day.weekday())] += n
    return weeks


# The golden values behind each Evidence record ---------------------------------------------------------


@cache
def golden(schema: str) -> dict[str, list[float]]:
    """Per Evidence id, the values the answers may quote from it, each computed independently."""
    g: dict[str, list[float]] = defaultdict(list)
    gb1 = gb1_rows(schema)

    # E2, E3: the /p/gb1 failures on the logged day 2005-09-02 and in the burst from 12:39 to 12:45.
    day = [r for r in gb1 if r[2].date() == date(2005, 9, 2)]
    burst = [r for r in day if datetime(2005, 9, 2, 12, 39) <= r[2] < datetime(2005, 9, 2, 12, 45)]
    for eid, rows in (("E2", day), ("E3", burst), ("E9", burst)):
        g[eid] += [len(rows), len({r[3] for r in rows}), len({r[5] for r in rows})]
    # E4: hourly UTC counts of the logged day.
    g["E4"] += Counter(math.floor(r[0] / 3600) for r in day).values()
    # E5: every Log Entry of the anchor node.
    g["E5"].append(independent(schema, "SELECT count(*) FROM {bgl} WHERE node = 'R23-M0-NC-I:J18-U11'")[0][0])
    # E6, E7, E10: the silence before the burst and after its last failure.
    anchor = independent(schema, "SELECT epoch_utc FROM {bgl} WHERE entry_id = 3321167")[0][0]
    last = independent(schema, "SELECT epoch_utc FROM {bgl} WHERE entry_id = 3322190")[0][0]
    before = independent(schema, "SELECT max(epoch_utc) FROM {bgl} WHERE epoch_utc < %s", [anchor])[0][0]
    after = independent(schema, "SELECT min(epoch_utc) FROM {bgl} WHERE epoch_utc > %s", [last])[0][0]
    g["E6"].append(
        independent(
            schema,
            "SELECT count(*) FROM {bgl} WHERE epoch_utc BETWEEN %s AND %s",
            [anchor - 600, anchor + 600],
        )[0][0]
    )
    g["E7"].append(round(anchor - before, 3))
    g["E10"].append(round(after - last, 3))
    # E8: the /p/gb1 Pattern among every Log Entry from 18:39 to 20:39 UTC.
    lo, hi = utc("2005-09-02T18:39:00"), utc("2005-09-02T20:39:00")
    window = independent(
        schema, "SELECT count(*) FROM {bgl} WHERE epoch_utc >= %s AND epoch_utc < %s", [lo, hi]
    )
    in_window = [r for r in gb1 if lo <= r[0] < hi]
    g["E8"] += [
        len(in_window),
        window[0][0],
        share(len(in_window), window[0][0]),
        len({r[3] for r in in_window}),
    ]

    # E13: the wave per UTC day, 2005-08-31 to 2005-09-18 (18 days).
    days = daily_utc(gb1)
    g["E13"] += [days.get(date(2005, 8, 31) + timedelta(d), 0) for d in range(18)] + [18]
    # E14: weekly counts (Monday-start, UTC) of both KERNMNTF Patterns.
    block = independent(schema, f"SELECT epoch_utc, entry_id, node FROM {{bgl}} WHERE {BLOCK_ID}")
    g["E14"] += [*weekly_utc(gb1).values(), *weekly_utc(block).values()]
    # E15: the wave's Incidents with Spread and Shape.
    wave = wave_incidents(schema)
    g["E15"] += [
        wave["entries"],
        wave["incidents"],
        wave["days"],
        wave["widest_nodes"],
        wave["widest_incidents"],
        wave["largest"],
        wave["nodes"],
        wave["midplanes"],
        wave["racks"],
        *wave["shapes"].values(),
    ]
    # E16, E24: KERNMNTF by Pattern over the whole Dataset; E17: the block_id Pattern from 2005-09-18.
    kernmntf = independent(schema, "SELECT count(*) FROM {bgl} WHERE alert_tag = 'KERNMNTF'")[0][0]
    g["E16"] += [
        kernmntf,
        len(gb1),
        len(block),
        share(len(gb1), kernmntf),
        share(len(block), kernmntf),
        len({r[3] for r in gb1}),
        len({r[2] for r in block}),
    ]
    g["E24"] += [len(gb1), len({r[3] for r in gb1})]
    later = [r for r in block if r[0] >= utc("2005-09-18T00:00:00")]
    g["E17"] += [len(later), len({r[2] for r in later})]

    # E18: spawn timing of every instance, with robust z-scores, from the raw nova lines.
    phases = spawn_phases(schema)
    spawn = {k: v["spawn_s"] for k, v in phases.items() if "spawn_s" in v}
    image = {k: v["image_s"] for k, v in phases.items() if "image_s" in v}
    paused = {k: v["paused_s"] for k, v in phases.items() if "paused_s" in v}
    spawn_z, image_z, paused_z = robust_z(spawn), robust_z(image), robust_z(paused)
    median = statistics.median(spawn.values())
    g["E18"] += [len(spawn), median, statistics.median(abs(v - median) for v in spawn.values())]
    g["E18"].append(sum(1 for z in spawn_z.values() if z >= 5))
    for instance in (*SLOW_SPAWNS, FIFTH):
        g["E18"] += [spawn[instance], spawn_z[instance], image_z[instance], paused_z[instance]]
    # E19, E21: full-text searches for errors on the four instances; E22: their levels.
    four = independent(schema, f"SELECT level, instance_ref FROM {{os}} WHERE instance_ref IN {FOUR}")
    g["E22"] += [len(four), len({r[1] for r in four})]

    # E20: Log Entries by Team over the whole Base Corpus.
    teams = independent(
        schema,
        "SELECT o.team, count(*) FROM (SELECT 'bgl' AS dataset, component FROM {bgl} "
        "UNION ALL SELECT 'openstack', component FROM {os}) e "
        "JOIN {owners} o ON o.dataset = e.dataset AND o.component = e.component GROUP BY o.team",
    )
    total = sum(n for _, n in teams)
    g["E20"] += [total, *(n for _, n in teams), *(share(n, total) for _, n in teams)]

    # E26, E27: alert categories over the whole BGL Dataset and the wave's window.
    for eid, cats in (
        ("E26", categories(schema, None, None)),
        ("E27", categories(schema, utc("2005-08-31T00:00:00"), utc("2005-09-18T00:00:00"))),
    ):
        g[eid] += [
            len(cats),
            sum(c["entries"] for c in cats.values()),
            sum(c["incidents"] for c in cats.values()),
        ]
        for c in cats.values():
            g[eid] += [c["entries"], c["incidents"], c["midplanes"], c["active_weeks"]]
    span = independent(schema, "SELECT max(epoch_utc) - min(epoch_utc) FROM {bgl}")[0][0]
    g["E26"].append(math.ceil(span / 86400 / 7))
    # E28: KERNDTLB, the trap.
    dtlb = categories(schema, None, None)["KERNDTLB"]
    largest = dtlb["largest"]
    storms = [
        i
        for i in chain(
            independent(schema, "SELECT epoch_utc, entry_id, node FROM {bgl} WHERE alert_tag = 'KERNDTLB'")
        )
        if len({r[2] for r in i}) == 1 and len(i) >= 100
    ]
    g["E28"] += [len(largest), len({r[4] for r in largest}), dtlb["entries"], dtlb["incidents"], len(storms)]
    return dict(g)


def pinned(text: str, value: float, candidates: Iterable[float]) -> bool:
    """Whether `value`, printed as `text`, is one of `candidates` at the precision it is printed with."""
    decimals = len(text.rstrip("%").split(".")[1]) if "." in text else 0
    return any(round(c, decimals) == round(value, decimals) for c in candidates)


def parameters(record: Mapping[str, Any]) -> set[float]:
    args = record.get("arguments") or {}
    return {float(v) for v in args.values() if isinstance(v, int | float) and not isinstance(v, bool)}


# Tests ------------------------------------------------------------------------------------------------


def test_every_number_in_the_answers_is_pinned(golden_schema: str) -> None:
    values = golden(golden_schema)
    ledger = list(records().values())
    unpinned = []
    for item in investigation().answers:
        report = check_answer(item.role, item.answer, ledger)
        assert report.ok, (item.role, [p.text for p in report.problems])
        for number in report.checked_numbers:
            ids = number.found_in or number.cited
            if any(pinned(number.text, number.value, values.get(i, [])) for i in ids):
                continue
            if not any(number.value in parameters(records()[i]) for i in ids):
                # Only a call parameter restated (window_s, gap_s) needs no golden value.
                unpinned.append(f"{item.role.value}: {number.text} from {ids}")
    assert unpinned == []


def test_the_p_gb1_wave(golden_schema: str) -> None:
    wave = wave_incidents(golden_schema)
    assert (wave["entries"], wave["incidents"], wave["days"]) == (26_426, 83, 12)
    assert (wave["widest_incidents"], wave["widest_nodes"], wave["nodes"]) == (7, 1_024, 1_024)
    text = uncited(answer(Role.TEAM_LEAD))
    assert "26,426 Log Entries in 83 Incidents" in text
    assert "starting on 12 distinct days" in text
    assert "7 Incidents reached the widest Spread, all 1,024 I/O nodes" in text


def test_the_slow_spawns_are_found_by_timing_and_not_by_text(golden_schema: str) -> None:
    spawn = {k: v["spawn_s"] for k, v in spawn_phases(golden_schema).items() if "spawn_s" in v}
    ranked = sorted(spawn, key=spawn.__getitem__, reverse=True)
    assert set(ranked[:4]) == set(SLOW_SPAWNS)  # 4 of 4, by timing alone
    assert ranked[4] == FIFTH
    words = "regexp_matches(lower(message), '(^|[^a-z])(error|failed|exception|timeout|traceback)([^a-z]|$)')"
    failing = f"(level IN ('ERROR', 'CRITICAL') OR {words})"
    errors = independent(
        golden_schema, f"SELECT count(*) FROM {{os}} WHERE instance_ref IN {FOUR} AND {failing}"
    )
    assert errors == [(0,)]  # text search finds none on the four
    anywhere = independent(golden_schema, f"SELECT count(*) FROM {{os}} WHERE {failing}")
    assert anywhere[0][0] > 0  # while the same predicate does match elsewhere in OpenStack
    text = answer(Role.TEAM_LEAD)
    assert "nova's spawn timing finds all four" in text
    assert "d7d68893" in text
    assert "Hypothesis" in text


def test_the_kerndtlb_trap(golden_schema: str) -> None:
    dtlb = categories(golden_schema, None, None)["KERNDTLB"]
    largest = dtlb["largest"]
    assert (len(largest), len({r[4] for r in largest}), dtlb["entries"]) == (152_182, 1, 152_734)
    day = independent(
        golden_schema, "SELECT CAST(ts AS DATE) FROM {bgl} WHERE entry_id = %s", [largest[0][1]]
    )
    assert day == [(date(2005, 6, 11),)]
    assert KERNDTLB_SENTENCE in uncited(answer(Role.MANAGER))
    assert "KERNDTLB" not in BAND_WHOLE


def test_the_manager_answer_has_two_data_scopes_and_the_band(golden_schema: str) -> None:
    whole = categories(golden_schema, None, None)
    window = categories(golden_schema, utc("2005-08-31T00:00:00"), utc("2005-09-18T00:00:00"))
    text = answer(Role.MANAGER)
    first, second = text.split("**Data Scope 2", 1)
    assert "**Data Scope 1: the /p/gb1 wave's window" in first
    assert {t for t in BAND_WAVE if f"- {t}" in first} == BAND_WAVE
    assert {t for t in BAND_WHOLE if f"- {t}" in second} == BAND_WHOLE
    assert set(whole) >= BAND_WHOLE
    # In the window the mount failure leads in both units.
    assert max(window, key=lambda t: window[t]["entries"]) == "KERNMNTF"
    assert max(window, key=lambda t: window[t]["incidents"]) == "KERNMNTF"
    assert (whole["APPSEV"]["entries"], whole["APPSEV"]["incidents"]) == (49_651, 136)


def test_the_manager_refuses_cost_with_the_measured_alternative() -> None:
    text = answer(Role.MANAGER)
    unknowns = text.split("## Unknowns", 1)[1].split("## Next Step", 1)[0]
    assert "cannot establish what the failures cost in downtime or money" in unknowns
    assert "The measured alternative is above" in unknowns


def test_one_answer_aggregates_over_the_whole_base_corpus(golden_schema: str) -> None:
    record = records()["E20"]
    assert record["tool"] == "count_by"
    assert record["arguments"]["group_by"] == "team"
    assert record["data_scope"]["datasets"] == ["bgl", "openstack"]
    assert (record["data_scope"]["time_from"], record["data_scope"]["time_to"]) == (None, None)
    total = independent(golden_schema, "SELECT (SELECT count(*) FROM {bgl}) + (SELECT count(*) FROM {os})")
    assert total == [(4_955_783,)]
    assert "Data Scope: both Datasets, all time, no filter, 4,955,783 Log Entries" in uncited(
        answer(Role.TEAM_LEAD)
    )


def test_the_start_up_path_link_is_thematic_not_causal() -> None:
    hypotheses = answer(Role.TEAM_LEAD).split("## Hypotheses", 1)[1].split("## Unknowns", 1)[0]
    assert "not linked causally" in hypotheses
    assert "only thematic" in hypotheses


def test_the_document_carries_the_pinned_numbers() -> None:
    document = uncited(REPORT.read_text(encoding="utf-8"))
    for text in (
        "26,426 Log Entries in 83 Incidents",
        "7 Incidents reached the widest Spread, all 1,024 I/O nodes",
        KERNDTLB_SENTENCE,
        "4,955,783 Log Entries",
        "What the data does and does not establish",
    ):
        assert text in document, text
