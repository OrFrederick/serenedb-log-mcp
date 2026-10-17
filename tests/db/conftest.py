"""Fixtures for the db tests: the Loghub 2k samples derived into throwaway schemas.

`derived_sample` loads the samples with the dev loader into `scratch_t4_<hex>_raw`, adds a few
synthetic raw rows that the samples lack, runs the derive step into `scratch_t4_<hex>` and drops
both schemas at the end of the session. It never touches the shared raw tables in `public`.

Pattern Tier 1 needs the optional Loghub-2.0 template files (`make fetch-data`). Without them every
derive in the db tests runs with SLM_PATTERN_TIER1=0, so the db tests run on a fresh checkout and
only the Tier 1 tests skip, with the reason (#66).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import psycopg
import pytest
from optional_data import derive_with_tier1
from psycopg import sql
from sample_files import samples_or_skip

import derive
import dev_loader

# Synthetic BGL rows (line_no, alert_tag, epoch, ts, node, component, level, message). The epoch is
# UTC; ts is US Pacific local time. The two 2005-10-30 01:30:00.5 rows have the same local time,
# one in PDT and one an hour later in PST, after the fall-back.
SYNTHETIC_BGL: tuple[tuple[int, str, int, datetime, str, str, str, str], ...] = (
    (900_001, "-", 1117838570, datetime(2005, 6, 3, 15, 42, 50, 363779), "R27-M1-L3-U18-C", "KERNEL", "INFO",
     "link card message"),
    (900_002, "-", 1117838570, datetime(2005, 6, 3, 15, 42, 50, 363779), "R63-M0", "MMCS", "INFO",
     "midplane message"),
    (900_003, "-", 1117838570, datetime(2005, 6, 3, 15, 42, 50, 363779), "R63-M0-N0", "KERNEL", "INFO",
     "node card message"),
    (900_004, "-", 1117838570, datetime(2005, 6, 3, 15, 42, 50, 363779), "NULL", "BGLMASTER", "INFO",
     "BGLMaster has been started"),
    (900_005, "-", 1117838570, datetime(2005, 6, 3, 15, 42, 50, 363779), "UNKNOWN_LOCATION", "CMCS", "INFO",
     "Starting SystemController"),
    (900_006, "KERNDTLB", 1118536327, datetime(2005, 6, 11, 17, 32, 7, 250000), "R30-M0-N9-C:J16-U01",
     "KERNEL", "FATAL", "data TLB error interrupt"),
    (900_007, "-", 1130661000, datetime(2005, 10, 30, 1, 30, 0, 500000), "R02-M1-N0-C:J12-U11", "KERNEL",
     "INFO", "before the fall-back"),
    (900_008, "-", 1130664600, datetime(2005, 10, 30, 1, 30, 0, 500000), "R02-M1-N0-C:J12-U11", "KERNEL",
     "INFO", "after the fall-back"),
    (900_009, "-", 1136390405, datetime(2006, 1, 4, 8, 0, 5, 233639), "R71-M0-NF-C:J02-U01", "KERNEL",
     "INFO", "winter message"),
    (900_010, "MASABNORM", 1121213733, datetime(2005, 7, 12, 17, 15, 33, 769604), "NULL", "BGLMASTER",
     "FAILURE", "mmcs_server exited abnormally due to signal: Aborted"),
    (900_011, "-", 1123189879, datetime(2005, 8, 4, 14, 11, 19, 32342), "R76", "HARDWARE", "WARNING",
     "PrepareForService is being done on this rack (R76) by root"),
)  # fmt: skip

# The 2k OpenStack sample is one file; the full corpus has three. Split it the same way, so the
# neutral source ids and the label hygiene are tested on the real file names.
OPENSTACK_SPLIT = (
    (700, "openstack_normal1.log"),
    (1400, "openstack_normal2.log"),
    (2000, "openstack_abnormal.log"),
)


@dataclass(frozen=True)
class Schemas:
    raw: str
    slm: str


def _drop(database_url: str, *schemas: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        for schema in schemas:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _add_synthetic_rows(database_url: str, raw: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        bgl = sql.Identifier(raw, "bgl")
        for line_no, tag, epoch, ts, node, component, level, message in SYNTHETIC_BGL:
            stamp = ts.strftime("%Y-%m-%d-%H.%M.%S.%f")
            raw_line = f"{tag} {epoch} {ts:%Y.%m.%d} {node} {stamp} {node} RAS {component} {level} {message}"
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (line_no, alert_tag, epoch, ts, node, component, level, message, raw) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                ).format(bgl),
                [line_no, tag, epoch, ts, node, component, level, message, raw_line],
            )
        lower = 0
        for upper, name in OPENSTACK_SPLIT:
            conn.execute(
                sql.SQL("UPDATE {} SET source_file = %s WHERE line_no > %s AND line_no <= %s").format(
                    sql.Identifier(raw, "openstack")
                ),
                [name, lower, upper],
            )
            lower = upper


@pytest.fixture(autouse=True)
def _tier1_off_without_the_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every derive a db test runs itself uses Pattern Tier 1 only when the template files are there."""
    if not derive_with_tier1():
        monkeypatch.setenv("SLM_PATTERN_TIER1", "0")


@pytest.fixture(scope="session")
def derived_sample(database_url: str) -> Iterator[Schemas]:
    """The samples (plus synthetic rows) derived into a scratch schema; dropped afterwards."""
    samples_or_skip()
    suffix = uuid.uuid4().hex[:8]
    schemas = Schemas(raw=f"scratch_t4_{suffix}_raw", slm=f"scratch_t4_{suffix}")
    try:
        assert dev_loader.main(["--sample", "--schema", schemas.raw]) == 0
        _add_synthetic_rows(database_url, schemas.raw)
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("SLM_PATTERN_TIER1", "1" if derive_with_tier1() else "0")
            assert derive.main(["--schema", schemas.slm, "--raw-schema", schemas.raw]) == 0
        yield schemas
    finally:
        _drop(database_url, schemas.slm, schemas.raw)
