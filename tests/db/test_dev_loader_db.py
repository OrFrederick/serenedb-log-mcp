"""The dev loader's --sample mode against a real SereneDB, in a throwaway schema.

It never touches the raw tables in `public`, which hold the shared Base Corpus.
"""

import uuid
from collections.abc import Iterator
from urllib.error import URLError

import psycopg
import pytest
from psycopg import sql

import dev_loader
from serenedb_log_mcp.config import load_settings

pytestmark = pytest.mark.db

SAMPLE_ROWS = 2000


@pytest.fixture(scope="module")
def samples_present() -> None:
    try:
        dev_loader.ensure_samples(load_settings().data_dir / "sample")
    except (URLError, OSError) as exc:
        pytest.skip(f"cannot download the Loghub 2k samples: {exc}")


@pytest.fixture
def scratch_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t1_{uuid.uuid4().hex[:8]}"
    yield schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def count(database_url: str, schema: str, table: str) -> int:
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
        ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.usefixtures("samples_present")
def test_sample_mode_loads_both_datasets(database_url: str, scratch_schema: str) -> None:
    assert dev_loader.main(["--sample", "--schema", scratch_schema]) == 0

    assert count(database_url, scratch_schema, "bgl") == SAMPLE_ROWS
    assert count(database_url, scratch_schema, "openstack") == SAMPLE_ROWS
    with psycopg.connect(database_url, autocommit=True) as conn:
        bgl = conn.execute(
            sql.SQL("SELECT count(ts), count(node), count(*) FILTER (WHERE alert_tag <> '-') FROM {}").format(
                sql.Identifier(scratch_schema, "bgl")
            )
        ).fetchone()
        sources = conn.execute(
            sql.SQL("SELECT DISTINCT source_file FROM {}").format(sql.Identifier(scratch_schema, "openstack"))
        ).fetchall()
    assert bgl is not None
    assert bgl[0] == SAMPLE_ROWS
    assert bgl[1] == SAMPLE_ROWS
    assert bgl[2] > 0
    assert sources == [("OpenStack_2k.log",)]


@pytest.mark.usefixtures("samples_present")
def test_existing_tables_are_kept_unless_replace_is_given(
    database_url: str, scratch_schema: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert dev_loader.main(["--sample", "--schema", scratch_schema]) == 0

    assert dev_loader.main(["--sample", "--schema", scratch_schema]) == 2
    assert "--replace" in capsys.readouterr().err
    assert count(database_url, scratch_schema, "bgl") == SAMPLE_ROWS

    assert dev_loader.main(["--sample", "--schema", scratch_schema, "--replace"]) == 0
    assert count(database_url, scratch_schema, "bgl") == SAMPLE_ROWS
