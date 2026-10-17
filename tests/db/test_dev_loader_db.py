"""The dev loader's --sample mode against a real SereneDB, in a throwaway schema.

It never touches the raw tables in `public`, which hold the shared Base Corpus.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sample_files import samples_or_skip

import dev_loader

pytestmark = pytest.mark.db

SAMPLE_ROWS = 2000


@pytest.fixture(scope="module")
def samples_present() -> None:
    samples_or_skip()


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
def test_sample_mode_loads_both_datasets(
    database_url: str, scratch_schema: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert dev_loader.main(["--sample", "--schema", scratch_schema]) == 0

    out = capsys.readouterr().out
    assert f"{scratch_schema}.bgl: 2000 rows" in out
    assert "(unparsed 0, NULL ts 0, NULL message" in out

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
    err = capsys.readouterr().err
    assert "--schema scratch_<name>" in err
    assert "--replace" in err
    assert count(database_url, scratch_schema, "bgl") == SAMPLE_ROWS

    assert dev_loader.main(["--sample", "--schema", scratch_schema, "--replace"]) == 0
    assert count(database_url, scratch_schema, "bgl") == SAMPLE_ROWS


def execute(database_url: str, *statements: sql.Composable) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


def table_names(database_url: str, schema: str) -> list[str]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY 1", [schema]
        ).fetchall()
    return [r[0] for r in rows]


@pytest.mark.usefixtures("samples_present")
def test_sample_mode_never_replaces_a_table_with_more_than_2k_rows(
    database_url: str, scratch_schema: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Stands in for public.bgl on the shared instance: --sample --replace must not touch it.
    execute(
        database_url,
        sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(scratch_schema)),
        sql.SQL("CREATE TABLE {} AS SELECT g AS line_no FROM generate_series(1, 2001) AS s(g)").format(
            sql.Identifier(scratch_schema, "bgl")
        ),
    )

    assert dev_loader.main(["--sample", "--schema", scratch_schema, "--replace"]) == 2

    err = capsys.readouterr().err
    assert "2,001 rows" in err
    assert "--schema scratch_<name>" in err
    assert count(database_url, scratch_schema, "bgl") == 2001
    assert table_names(database_url, scratch_schema) == ["bgl"]


def test_failed_replace_keeps_the_old_table(database_url: str, scratch_schema: str) -> None:
    execute(
        database_url,
        sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(scratch_schema)),
        sql.SQL("CREATE TABLE {} AS SELECT g AS line_no FROM generate_series(1, 7) AS s(g)").format(
            sql.Identifier(scratch_schema, "bgl")
        ),
    )

    def rows_then_crash() -> Iterator[tuple[object, ...]]:
        yield dev_loader.parse_bgl_line(1, "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11")
        raise RuntimeError("input broke halfway")

    source = dev_loader.Source("bgl", (), rows_then_crash)
    with pytest.raises(RuntimeError, match="halfway"):
        dev_loader.load(database_url, scratch_schema, [source], replace=True)

    assert count(database_url, scratch_schema, "bgl") == 7


def test_leftover_staging_table_is_dropped_before_loading(database_url: str, scratch_schema: str) -> None:
    execute(
        database_url,
        sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(scratch_schema)),
        sql.SQL("CREATE TABLE {} (junk VARCHAR)").format(sql.Identifier(scratch_schema, "bgl__load")),
    )
    source = dev_loader.Source(
        "bgl", (), lambda: [dev_loader.parse_bgl_line(1, "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11")]
    )

    dev_loader.load(database_url, scratch_schema, [source], replace=False)

    assert table_names(database_url, scratch_schema) == ["bgl"]
    assert count(database_url, scratch_schema, "bgl") == 1


def test_malformed_input_exits_2_and_keeps_the_old_table(
    database_url: str,
    scratch_schema: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    execute(
        database_url,
        sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(scratch_schema)),
        sql.SQL("CREATE TABLE {} AS SELECT g AS line_id FROM generate_series(1, 3) AS s(g)").format(
            sql.Identifier(scratch_schema, "lh2_openstack")
        ),
    )
    csv_path = tmp_path / "lh2" / "OpenStack" / "OpenStack_full.log_structured.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("LineId,Content,EventId,EventTemplate\n1,a,E1,t\n2,broken\n")
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))

    code = dev_loader.main(["lh2_openstack", "--schema", scratch_schema, "--replace"])

    assert code == 2
    assert "OpenStack_full.log_structured.csv line 3" in capsys.readouterr().err
    assert count(database_url, scratch_schema, "lh2_openstack") == 3
