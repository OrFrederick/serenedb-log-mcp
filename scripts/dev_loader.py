#!/usr/bin/env python3
"""Throwaway DEV loader. This is not the organisers' loader.

The organisers supply the official loader, schema and Ownership Table, and those are the source of
truth (docs/decisions.md). Until they arrive, this script loads the raw Base Corpus into SereneDB so
that everyone can work, and T25 replaces it. Nothing outside sql/adapters/dev.sql may depend on the
exact shape of these tables.

Usage (see `make help`):
    uv run python scripts/dev_loader.py [TABLE ...] [--schema S] [--replace]   # full Base Corpus
    uv run python scripts/dev_loader.py --sample [--schema S] [--replace]      # Loghub 2k samples

Environment: DATABASE_URL (target SereneDB), SLM_DATA_DIR (input files, default ./data) and
SLM_RAW_SCHEMA (default schema, default public).

Safety, because the shared dev instance holds the Base Corpus other people use:
- An existing table is only replaced with --replace.
- --sample never replaces a table with more than 2,000 rows, with or without --replace; load the
  sample into its own schema with --schema scratch_<name>.
- Each table is copied into <table>__load and swapped in with DROP and RENAME in one transaction,
  so a failed load keeps the old table. A leftover <table>__load is dropped on the next run.

Input files under SLM_DATA_DIR:
    raw/BGL.log                                    BGL (Zenodo 8196385)
    raw/openstack_normal1.log, raw/openstack_normal2.log, raw/openstack_abnormal.log
    lh2/BGL/BGL_full.log_structured.csv            Loghub-2.0 (Zenodo 8275861)
    lh2/OpenStack/OpenStack_full.log_structured.csv
    sample/BGL_2k.log, sample/OpenStack_2k.log     --sample; downloaded from Loghub on first use

Tables:
    bgl(line_no, alert_tag, epoch, ts, node, component, level, message, raw)
        alert_tag is '-' for non-alert lines, otherwise the alert category (Oliner & Stearley labels)
    openstack(line_no, source_file, log_file, ts, pid, level, component, request_id,
              instance_id, message, raw)
        line_no runs across the input files in the order listed above
    lh2_bgl(line_id, content, event_id, event_template)
    lh2_openstack(line_id, content, event_id, event_template)

Data: Loghub, https://github.com/logpai/loghub. Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu,
Michael R. Lyu. Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics.
ISSRE 2023. BGL: Adam J. Oliner, Jon Stearley. What Supercomputers Say: A Study of Five System
Logs. DSN 2007.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from serenedb_log_mcp.config import SCHEMA_RE, ConfigError, load_settings

Row = tuple[Any, ...]


class LoaderError(Exception):
    """A problem the user can fix; printed without a traceback."""


# ----------------------------------------------------------------- parsing

BGL_TS_FORMAT = "%Y-%m-%d-%H.%M.%S.%f"
OS_TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
OS_RE = re.compile(r"^(\S+) (\S+ \S+) (\d+) (\S+) (\S+) (?:\[(req-[0-9a-f-]+)[^\]]*\]|\[-\])? ?(.*)$")
INSTANCE_RE = re.compile(r"\[instance: ([0-9a-f-]{36})\]")


def _clean(line: str) -> str:
    # COPY cannot carry NUL characters, so keep them visible as the text \x00.
    return line.rstrip("\n").replace("\x00", "\\x00")


def parse_bgl_line(line_no: int, line: str) -> Row:
    """One BGL line -> (line_no, alert_tag, epoch, ts, node, component, level, message, raw)."""
    raw = _clean(line)
    p = raw.split(" ", 9)

    def field(k: int) -> str | None:
        return p[k] if len(p) > k else None

    ts = None
    if len(p) > 4:
        try:
            ts = datetime.strptime(p[4], BGL_TS_FORMAT)
        except ValueError:
            ts = None
    epoch = int(p[1]) if len(p) > 1 and p[1].isdigit() else None
    return (line_no, field(0), epoch, ts, field(3), field(7), field(8), field(9), raw)


def parse_openstack_line(line_no: int, source_file: str, line: str) -> Row:
    """One OpenStack line -> (line_no, source_file, log_file, ts, pid, level, component,
    request_id, instance_id, message, raw)."""
    raw = _clean(line)
    m = OS_RE.match(raw)
    if not m:
        return (line_no, source_file, None, None, None, None, None, None, None, raw, raw)
    log_file, stamp, pid, level, component, request_id, message = m.groups()
    try:
        ts = datetime.strptime(stamp, OS_TS_FORMAT)
    except ValueError:
        ts = None
    instance = INSTANCE_RE.search(message)
    return (
        line_no,
        source_file,
        log_file,
        ts,
        int(pid),
        level,
        component,
        request_id,
        instance.group(1) if instance else None,
        message,
        raw,
    )


def bgl_rows(path: Path) -> Iterator[Row]:
    with path.open(encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            yield parse_bgl_line(line_no, line)


def openstack_rows(paths: Sequence[Path]) -> Iterator[Row]:
    line_no = 0
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line_no += 1
                yield parse_openstack_line(line_no, path.name, line)


def lh2_rows(path: Path) -> Iterator[Row]:
    csv.field_size_limit(10**9)
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            yield (int(row[0]), row[1], row[2], row[3])


# ----------------------------------------------------------------- tables and sources

COLUMNS: dict[str, list[tuple[str, str]]] = {
    "bgl": [
        ("line_no", "BIGINT"),
        ("alert_tag", "VARCHAR"),
        ("epoch", "BIGINT"),
        ("ts", "TIMESTAMP"),
        ("node", "VARCHAR"),
        ("component", "VARCHAR"),
        ("level", "VARCHAR"),
        ("message", "VARCHAR"),
        ("raw", "VARCHAR"),
    ],
    "openstack": [
        ("line_no", "BIGINT"),
        ("source_file", "VARCHAR"),
        ("log_file", "VARCHAR"),
        ("ts", "TIMESTAMP"),
        ("pid", "BIGINT"),
        ("level", "VARCHAR"),
        ("component", "VARCHAR"),
        ("request_id", "VARCHAR"),
        ("instance_id", "VARCHAR"),
        ("message", "VARCHAR"),
        ("raw", "VARCHAR"),
    ],
    "lh2_bgl": [
        ("line_id", "BIGINT"),
        ("content", "VARCHAR"),
        ("event_id", "VARCHAR"),
        ("event_template", "VARCHAR"),
    ],
    "lh2_openstack": [
        ("line_id", "BIGINT"),
        ("content", "VARCHAR"),
        ("event_id", "VARCHAR"),
        ("event_template", "VARCHAR"),
    ],
}
TABLES = tuple(COLUMNS)
STAGING_SUFFIX = "__load"
# The Loghub 2k samples have 2,000 lines each; --sample never replaces a bigger table.
SAMPLE_MAX_ROWS = 2_000
OPENSTACK_FILES = ("openstack_normal1.log", "openstack_normal2.log", "openstack_abnormal.log")


@dataclass(frozen=True)
class Source:
    table: str
    files: tuple[Path, ...]
    rows: Callable[[], Iterable[Row]]


def full_sources(data_dir: Path, tables: Sequence[str]) -> list[Source]:
    raw, lh2 = data_dir / "raw", data_dir / "lh2"
    bgl_log = raw / "BGL.log"
    os_logs = tuple(raw / name for name in OPENSTACK_FILES)
    lh2_bgl = lh2 / "BGL" / "BGL_full.log_structured.csv"
    lh2_os = lh2 / "OpenStack" / "OpenStack_full.log_structured.csv"
    every = {
        "bgl": Source("bgl", (bgl_log,), lambda: bgl_rows(bgl_log)),
        "openstack": Source("openstack", os_logs, lambda: openstack_rows(os_logs)),
        "lh2_bgl": Source("lh2_bgl", (lh2_bgl,), lambda: lh2_rows(lh2_bgl)),
        "lh2_openstack": Source("lh2_openstack", (lh2_os,), lambda: lh2_rows(lh2_os)),
    }
    return [every[t] for t in tables]


def sample_sources(bgl_log: Path, openstack_log: Path) -> list[Source]:
    return [
        Source("bgl", (bgl_log,), lambda: bgl_rows(bgl_log)),
        Source("openstack", (openstack_log,), lambda: openstack_rows([openstack_log])),
    ]


# ----------------------------------------------------------------- Loghub 2k samples

LOGHUB_COMMIT = "dd61d0952749ee7963bde24220d1be5ede023033"
LOGHUB_RAW = f"https://raw.githubusercontent.com/logpai/loghub/{LOGHUB_COMMIT}"
LOGHUB_LICENSE = """\
The Loghub 2k samples in this directory were downloaded by scripts/dev_loader.py from
https://github.com/logpai/loghub (commit {commit}).

LICENSE OF LOGHUB

The datasets are freely available for research or academic work, subject to
the following condition: For any usage or distribution of the loghub datasets,
please refer to the loghub repository URL (https://github.com/logpai/loghub)
and cite the following loghub paper where applicable.

Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. Loghub: A Large
Collection of System Log Datasets for AI-driven Log Analytics. In ISSRE, 2023.

The above license notice shall be included in all copies of the datasets.
"""


@dataclass(frozen=True)
class SampleFile:
    name: str
    url: str
    sha256: str


SAMPLES = (
    SampleFile(
        "BGL_2k.log",
        f"{LOGHUB_RAW}/BGL/BGL_2k.log",
        "2a819ea540909db682005c9cf948387a40729b5c2e9f19d430e29ce704825496",
    ),
    SampleFile(
        "OpenStack_2k.log",
        f"{LOGHUB_RAW}/OpenStack/OpenStack_2k.log",
        "025a1bc64ff5b2ef4a4bda6c4ad5c5c5f18478b71cd1ad2b0676e01625629f2f",
    ),
)


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        data: bytes = response.read()
    return data


def ensure_samples(
    directory: Path,
    samples: Sequence[SampleFile] = SAMPLES,
    fetch: Callable[[str], bytes] = _download,
) -> list[Path]:
    """Return the local sample paths, downloading and checksum-verifying the missing ones."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for sample in samples:
        path = directory / sample.name
        if not path.exists():
            data = fetch(sample.url)
            digest = hashlib.sha256(data).hexdigest()
            if digest != sample.sha256:
                raise LoaderError(
                    f"{sample.url} has sha256 {digest}, expected {sample.sha256}; not saving it"
                )
            path.write_bytes(data)
        paths.append(path)
    notice = directory / "LOGHUB_LICENSE.txt"
    if not notice.exists():
        notice.write_text(LOGHUB_LICENSE.format(commit=LOGHUB_COMMIT))
    return paths


# ----------------------------------------------------------------- loading


def _table_exists(conn: psycopg.Connection[Any], schema: str, table: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [schema, table],
    ).fetchone()
    return bool(row and row[0])


def copy_rows(
    conn: psycopg.Connection[Any],
    target: sql.Identifier,
    columns: Sequence[str],
    rows: Iterable[Row],
    batch: int = 200_000,
) -> int:
    """COPY `rows` into `target` in batches; return the row count."""
    column_list = sql.SQL(", ").join(sql.Identifier(name) for name in columns)
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(target, column_list)
    count = 0
    buffer: list[Row] = []

    def flush() -> None:
        with conn.cursor() as cur, cur.copy(statement) as copy:
            for row in buffer:
                copy.write_row(row)
        conn.commit()
        buffer.clear()

    for row in rows:
        buffer.append(row)
        count += 1
        if len(buffer) >= batch:
            flush()
    if buffer:
        flush()
    return count


def _row_count(conn: psycopg.Connection[Any], schema: str, table: str) -> int:
    row = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))).fetchone()
    return int(row[0]) if row else 0


def load(dsn: str, schema: str, sources: Sequence[Source], replace: bool, *, sample: bool = False) -> None:
    """Load every source into schema.<table>.

    Each table is copied into <table>__load first and swapped in with DROP and RENAME in one
    transaction, so a failed load leaves the old table as it was. In sample mode a table with more
    than SAMPLE_MAX_ROWS rows is never replaced, whatever `replace` says.
    """
    with psycopg.connect(dsn, autocommit=False) as conn:
        existing = [s.table for s in sources if _table_exists(conn, schema, s.table)]
        if sample:
            for table in existing:
                rows = _row_count(conn, schema, table)
                if rows > SAMPLE_MAX_ROWS:
                    raise LoaderError(
                        f"{schema}.{table} has {rows:,} rows, more than a {SAMPLE_MAX_ROWS:,}-line sample, "
                        "so --sample never replaces it, not even with --replace. "
                        "Load the sample into its own schema: use `--schema scratch_<name>`."
                    )
        if existing and not replace:
            names = ", ".join(f"{schema}.{t}" for t in existing)
            hint = (
                "use `--schema scratch_<name>` for a fresh sample, or pass --replace to reload this one"
                if sample
                else "pass --replace to drop and reload them"
            )
            raise LoaderError(f"{names} already exist(s); {hint}")
        conn.rollback()
        if schema != "public":
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            conn.commit()
        for source in sources:
            _load_table(conn, schema, source)


def _load_table(conn: psycopg.Connection[Any], schema: str, source: Source) -> None:
    target = sql.Identifier(schema, source.table)
    staging_name = f"{source.table}{STAGING_SUFFIX}"
    staging = sql.Identifier(schema, staging_name)
    columns = sql.SQL(", ").join(
        sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(kind)) for name, kind in COLUMNS[source.table]
    )
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(staging))
    conn.execute(sql.SQL("CREATE TABLE {} ({})").format(staging, columns))
    conn.commit()
    started = time.monotonic()
    count = copy_rows(conn, staging, [name for name, _ in COLUMNS[source.table]], source.rows())
    # Schema changes are transactional on SereneDB 26.10.1 and 26.09.1.3 (checked in the PR #12
    # review): the DROP and RENAME commit as one unit, and a rollback restores the old table.
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(target))
    conn.execute(sql.SQL("ALTER TABLE {} RENAME TO {}").format(staging, sql.Identifier(source.table)))
    conn.commit()
    print(f"{schema}.{source.table}: {count} rows in {time.monotonic() - started:.1f}s", flush=True)


# ----------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev_loader.py",
        description="Throwaway dev loader (not the organisers' loader): raw BGL and OpenStack into SereneDB.",
    )
    parser.add_argument(
        "tables", nargs="*", choices=TABLES, help=f"tables to load (default: all of {', '.join(TABLES)})"
    )
    parser.add_argument("--sample", action="store_true", help="load the Loghub 2k BGL and OpenStack samples")
    parser.add_argument("--schema", help="target schema (default: SLM_RAW_SCHEMA, else public)")
    parser.add_argument("--replace", action="store_true", help="drop and reload tables that already exist")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.sample and args.tables:
        parser.error("--sample loads bgl and openstack only; do not name tables with --sample")
    if args.schema is not None and not SCHEMA_RE.fullmatch(args.schema):
        parser.error(f"--schema {args.schema!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
    try:
        settings = load_settings()
        schema = args.schema or settings.raw_schema
        if args.sample:
            bgl_log, openstack_log = ensure_samples(settings.data_dir / "sample")
            sources = sample_sources(bgl_log, openstack_log)
        else:
            sources = full_sources(settings.data_dir, args.tables or TABLES)
            missing = [str(f) for s in sources for f in s.files if not f.exists()]
            if missing:
                raise LoaderError(
                    "missing input files (set SLM_DATA_DIR or fetch the data first):\n  "
                    + "\n  ".join(missing)
                )
        load(settings.database_url, schema, sources, args.replace, sample=args.sample)
    except (LoaderError, ConfigError) as exc:
        print(f"dev_loader: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
