"""A small synthetic OpenStack corpus with known latencies and spawn times, for the latency_stats tests.

`build(database_url, schema)` writes canonical OpenStack Log Entries into `<schema>.os_entry` and runs
the real derive step `sql/derive/60_os_instance.sql` over them, so the instance phases come from
the same SQL as on the Base Corpus. Everything is generated here, so the tests can compute every
expected number in Python:

- GET /v2/{tenant}/servers/detail every 30 s from 2017-05-16 00:00 to 06:00 (720 requests, latency
  0.20 to 0.29 s), three times slower in the 03:00 hour: the one hour of Latency Degradation;
- POST /v2/{tenant}/servers every 10 minutes (36 requests) and DELETE /v2/{tenant}/servers/{uuid}
  three times; the first three hours are source os-1, the rest os-2;
- 30 instances started every 10 minutes from 00:10, each with the nova start-up lines; instances 7
  and 19 spend 20 s and 12 s longer creating their image, so their spawn and build take longer;
- instance 30 only has a GET /servers/<uuid> line: it never spawns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import psycopg
from mcp import Client
from mcp_types import CallToolResult
from psycopg import sql

import derive
from serenedb_log_mcp.server import create_server

START = datetime(2017, 5, 16, 0, 0, 0)
END = datetime(2017, 5, 16, 6, 0, 0)
SPIKE_HOUR = datetime(2017, 5, 16, 3, 0, 0)
TENANT = "54fadb412c4e40cdbaed9335e4c35a9e"
GET_DETAIL = "GET /v2/{tenant}/servers/detail"
POST_SERVERS = "POST /v2/{tenant}/servers"
DELETE_SERVER = "DELETE /v2/{tenant}/servers/{uuid}"
GET_SERVER = "GET /v2/{tenant}/servers/{uuid}"
GET_PATTERN = "aaaaaaaaaaaaaaaa"
POST_PATTERN = "bbbbbbbbbbbbbbbb"
INSTANCES = 30
SLOW_IMAGE_S = {7: 20.0, 19: 12.0}
COLUMNS = (
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
DERIVE_STEP = Path(__file__).resolve().parents[2] / "sql" / "derive" / "60_os_instance.sql"
CHUNK = 150


@dataclass(frozen=True)
class Request:
    ts: datetime
    endpoint: str
    source_id: str
    latency_s: float


@dataclass(frozen=True)
class Instance:
    instance_id: str
    first_ts: datetime
    image_s: float
    paused_s: float
    spawn_s: float
    build_s: float
    spawn_ts: datetime


def uuid_of(i: int) -> str:
    return f"{i:08x}-0000-4000-8000-{i:012x}"


def source_of(ts: datetime) -> str:
    return "os-1" if ts < START + timedelta(hours=3) else "os-2"


def requests() -> list[Request]:
    """Every API request of the corpus, in time order."""
    out: list[Request] = []
    for i in range(720):
        ts = START + timedelta(seconds=30 * i)
        latency = round(0.20 + (i % 10) * 0.01, 4)
        if SPIKE_HOUR <= ts < SPIKE_HOUR + timedelta(hours=1):
            latency = round(latency * 3, 4)
        out.append(Request(ts, GET_DETAIL, source_of(ts), latency))
    for i in range(36):
        ts = START + timedelta(minutes=10 * i, seconds=5)
        out.append(Request(ts, POST_SERVERS, source_of(ts), round(0.50 + (i % 4) * 0.05, 4)))
    for i in range(3):
        ts = START + timedelta(hours=2 * i, seconds=7)
        out.append(Request(ts, DELETE_SERVER, source_of(ts), 0.26))
    # The GET of instance 30, which never spawns.
    out.append(Request(START + timedelta(hours=5, minutes=30), GET_SERVER, "os-2", 0.19))
    return sorted(out, key=lambda r: r.ts)


def instances() -> list[Instance]:
    """The 30 instances that spawn, with the phase durations the derive step must find."""
    out = []
    for i in range(INSTANCES):
        first = START + timedelta(minutes=10 + 10 * i)
        image = round(13.0 + (i % 5) * 0.1 + SLOW_IMAGE_S.get(i, 0.0), 3)
        paused = round(6.5 + (i % 3) * 0.1, 3)
        spawn = round(image + paused + 0.06, 2)
        build = round(spawn + 0.8, 2)
        spawn_ts = first + timedelta(seconds=0.6 + image + 0.05 + paused + 0.01)
        out.append(Instance(uuid_of(i), first, image, paused, spawn, build, spawn_ts))
    return out


def _epoch(ts: datetime) -> float:
    return ts.replace(tzinfo=UTC).timestamp()


def _api_message(request: Request, instance: str | None = None) -> str:
    method, path = request.endpoint.split(" ", 1)
    path = path.replace("{tenant}", TENANT).replace("{uuid}", instance or uuid_of(999))
    return f'10.11.10.1 "{method} {path} HTTP/1.1" status: 200 len: 1893 time: {request.latency_s:.7f}'


def rows() -> list[tuple[Any, ...]]:
    """The canonical OpenStack Log Entries, in the column order of COLUMNS."""
    out: list[tuple[Any, ...]] = []

    def add(
        ts: datetime,
        component: str,
        message: str,
        *,
        instance: str | None = None,
        request: Request | None = None,
        template: str | None = None,
    ) -> None:
        out.append(
            (
                len(out) + 1,
                ts,
                _epoch(ts),
                source_of(ts),
                "nova-api.log" if request else "nova-compute.log",
                25746,
                component,
                "INFO",
                f"req-{len(out):08d}",
                instance if request is None else None,
                instance,
                None if request is None else request.endpoint,
                None if request is None else 200,
                None if request is None else request.latency_s,
                message,
                template,
                template,
                template,
            )
        )

    for request in requests():
        template = {GET_DETAIL: GET_PATTERN, POST_SERVERS: POST_PATTERN}.get(request.endpoint)
        named = uuid_of(INSTANCES) if request.endpoint == GET_SERVER else None
        add(
            request.ts,
            "nova.osapi_compute.wsgi.server",
            _api_message(request, named),
            instance=named,
            request=request,
            template=template,
        )
    for spawned in instances():
        u = spawned.instance_id
        t = spawned.first_ts
        add(
            t,
            "nova.compute.claims",
            f"[instance: {u}] Attempting claim: memory 2048 MB, disk 20 GB, vcpus 1 CPU",
            instance=u,
        )
        t += timedelta(seconds=0.6)
        add(t, "nova.virt.libvirt.driver", f"[instance: {u}] Creating image", instance=u)
        t += timedelta(seconds=spawned.image_s)
        add(t, "nova.compute.manager", f"[instance: {u}] VM Started (Lifecycle Event)", instance=u)
        t += timedelta(seconds=0.05)
        add(t, "nova.compute.manager", f"[instance: {u}] VM Paused (Lifecycle Event)", instance=u)
        t += timedelta(seconds=spawned.paused_s)
        add(t, "nova.compute.manager", f"[instance: {u}] VM Resumed (Lifecycle Event)", instance=u)
        t += timedelta(seconds=0.01)
        add(
            t,
            "nova.compute.manager",
            f"[instance: {u}] Took {spawned.spawn_s:.2f} seconds to spawn the instance on the hypervisor.",
            instance=u,
        )
        t += timedelta(seconds=0.1)
        add(
            t,
            "nova.compute.manager",
            f"[instance: {u}] Took {spawned.build_s:.2f} seconds to build instance.",
            instance=u,
        )
    return out


def build(database_url: str, schema: str) -> None:
    """Create `schema` with the synthetic os_entry and the derived os_instance."""
    table = sql.Identifier(schema, "os_entry")
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                table,
                sql.SQL(", ").join(
                    sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t)) for c, t in COLUMNS
                ),
            )
        )
        data = rows()
        one = sql.SQL("({})").format(sql.SQL(", ").join(sql.Placeholder() * len(COLUMNS)))
        for start in range(0, len(data), CHUNK):
            chunk = data[start : start + CHUNK]
            conn.execute(
                sql.SQL("INSERT INTO {} VALUES {}").format(table, sql.SQL(", ").join([one] * len(chunk))),
                [value for row in chunk for value in row],
            )
        row = conn.execute("SELECT version()").fetchone()
        version = derive.serenedb_version(str(row[0]) if row else "")
        derive.run(conn, [DERIVE_STEP], derive.variables(schema, "public", version))


def drop(database_url: str, schema: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


# ----------------------------------------------------------------- expected values


def quantile_cont(values: Sequence[float], p: float) -> float:
    """The interpolated quantile SereneDB's quantile_cont computes: position p * (n - 1)."""
    ordered = sorted(values)
    position = p * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def stats(values: Sequence[float]) -> dict[str, float]:
    """n, p50_s, p95_s, p99_s and max_s, rounded as latency_stats rounds them."""
    return {
        "n": len(values),
        "p50_s": round(quantile_cont(values, 0.50), 4),
        "p95_s": round(quantile_cont(values, 0.95), 4),
        "p99_s": round(quantile_cont(values, 0.99), 4),
        "max_s": round(max(values), 4),
    }


# ----------------------------------------------------------------- calling the tool


def _call(arguments: dict[str, Any]) -> CallToolResult:
    async def one_call() -> CallToolResult:
        async with Client(create_server(), mode="legacy") as client:
            result = await client.call_tool("latency_stats", arguments)
            assert isinstance(result, CallToolResult)
            return result

    return anyio.run(one_call)


def call(**arguments: Any) -> dict[str, Any]:
    """The structuredContent of a successful latency_stats call."""
    result = _call(arguments)
    if result.is_error:
        text = result.content[0].text if result.content else "(no text)"
        raise AssertionError(f"latency_stats failed: {text}")
    assert result.structured_content is not None
    return result.structured_content


def call_error(**arguments: Any) -> str:
    """The error text of a latency_stats call that must fail."""
    result = _call(arguments)
    assert result.is_error, f"latency_stats did not fail: {result.structured_content}"
    return result.content[0].text


def row_dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]]
