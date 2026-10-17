"""The pure parts of scripts/derive.py: step order, statement splitting, placeholders, DDL choice."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import derive
from derive import DeriveError

ROOT = Path(__file__).parents[2]


# ----------------------------------------------------------------- statements


def test_statements_split_on_semicolons_outside_strings_and_comments() -> None:
    text = """
    -- a comment; with a semicolon
    SELECT 'a;b' AS x;  /* block; comment */
    SELECT "odd;name" FROM t;
    SELECT regexp_replace(s, '[0-9]{2}', '{n}') -- trailing; comment
    FROM t
    """

    assert derive.split_statements(text) == [
        "SELECT 'a;b' AS x",
        'SELECT "odd;name" FROM t',
        "SELECT regexp_replace(s, '[0-9]{2}', '{n}') \n    FROM t",
    ]


def test_doubled_quotes_stay_inside_the_string() -> None:
    assert derive.split_statements("SELECT 'it''s; fine'; SELECT 2;") == [
        "SELECT 'it''s; fine'",
        "SELECT 2",
    ]


def test_a_file_of_comments_has_no_statements() -> None:
    assert derive.split_statements("-- TODO(organiser)\n/* nothing\n yet */\n") == []


def test_an_unterminated_string_is_an_error() -> None:
    with pytest.raises(DeriveError, match="unterminated"):
        derive.split_statements("SELECT 'oops;")


# ----------------------------------------------------------------- placeholders


def test_placeholders_are_replaced() -> None:
    assert derive.render("SELECT * FROM ${raw}.bgl INTO ${slm}.x", {"raw": '"public"', "slm": '"slm"'}) == (
        'SELECT * FROM "public".bgl INTO "slm".x'
    )


def test_an_unknown_placeholder_is_an_error() -> None:
    with pytest.raises(DeriveError, match=r"\$\{nope\}"):
        derive.render("SELECT ${nope}", {"slm": '"slm"'})


def test_variables_quote_the_schemas_and_pick_the_dictionary_ddl() -> None:
    values = derive.variables("slm_t4", "public", (26, 10, 1))

    assert values["slm"] == '"slm_t4"'
    assert values["raw"] == '"public"'
    ddl = values["create_log_text_dictionary"]
    assert ddl.startswith('CREATE TEXT SEARCH DICTIONARY "slm_t4".log_text AS')


@pytest.mark.parametrize("schema", ["Slm", "slm; DROP TABLE x", "", 'a"b'])
def test_variables_reject_a_schema_that_is_not_a_plain_identifier(schema: str) -> None:
    with pytest.raises(DeriveError, match="identifier"):
        derive.variables(schema, "public", (26, 10, 1))


# ----------------------------------------------------------------- version and dictionary DDL


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("PostgreSQL 18.3 (SereneDB 26.10.1)", (26, 10, 1)),
        ("PostgreSQL 18.1 (SereneDB 26.09.1.3)", (26, 9, 1, 3)),
        ("PostgreSQL 18.3 (SereneDB v27.1)", (27, 1)),
    ],
)
def test_the_serenedb_version_is_read_from_version(version: str, expected: tuple[int, ...]) -> None:
    assert derive.serenedb_version(version) == expected


def test_a_server_that_is_not_serenedb_is_an_error() -> None:
    with pytest.raises(DeriveError, match="SereneDB"):
        derive.serenedb_version("PostgreSQL 16.2 on x86_64-pc-linux-gnu")


@pytest.mark.parametrize("version", [(26, 10, 1), (26, 10, 2), (26, 11), (27, 0, 0)])
def test_26_10_1_and_later_use_the_split_text_expression(version: tuple[int, ...]) -> None:
    ddl = derive.dictionary_ddl('"slm".log_text', version)

    assert ddl == (
        "CREATE TEXT SEARCH DICTIONARY \"slm\".log_text AS split_text(case := 'lower') "
        "WITH (frequency, position, offset, norm)"
    )


@pytest.mark.parametrize("version", [(26, 9, 1, 3), (26, 10, 0), (25, 12)])
def test_older_servers_use_the_legacy_template_form(version: tuple[int, ...]) -> None:
    ddl = derive.dictionary_ddl('"slm".log_text', version)

    assert ddl.startswith("CREATE TEXT SEARCH DICTIONARY \"slm\".log_text (TEMPLATE = 'text'")
    for flag in ("frequency = true", "position = true", '"offset" = true', "norm = true"):
        assert flag in ddl
    assert "stemming = false" in ddl
    assert " AS " not in ddl


# ----------------------------------------------------------------- steps


def test_steps_run_the_adapter_then_the_seeds_then_the_derive_steps_in_name_order(tmp_path: Path) -> None:
    for name in ("adapters/dev.sql", "adapters/official.sql", "seed/b.sql", "seed/a.sql"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("SELECT 1;")
    for name in ("90_indexes.sql", "10_entries.sql", "20_patterns.sql"):
        (tmp_path / "derive").mkdir(exist_ok=True)
        (tmp_path / "derive" / name).write_text("SELECT 1;")

    steps = derive.steps("dev", tmp_path)

    assert [str(p.relative_to(tmp_path)) for p in steps] == [
        "adapters/dev.sql",
        "seed/a.sql",
        "seed/b.sql",
        "derive/10_entries.sql",
        "derive/20_patterns.sql",
        "derive/90_indexes.sql",
    ]


def test_an_unknown_adapter_names_the_available_ones(tmp_path: Path) -> None:
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "dev.sql").write_text("SELECT 1;")

    with pytest.raises(DeriveError, match=r"unknown adapter 'nope'.*dev"):
        derive.steps("nope", tmp_path)


def test_the_repository_steps_start_with_the_dev_adapter_and_end_with_the_indexes() -> None:
    steps = derive.steps("dev", ROOT / "sql")

    assert steps[0] == ROOT / "sql/adapters/dev.sql"
    assert ROOT / "sql/seed/ownership_stub.sql" in steps
    assert steps[-1].name.endswith("_indexes.sql")


def test_every_repository_sql_file_renders_and_splits() -> None:
    values = derive.variables("slm", "public", (26, 10, 1))
    for path in sorted((ROOT / "sql").rglob("*.sql")):
        statements = derive.split_statements(derive.render(path.read_text(), values))
        if path.name == "official.sql":
            assert statements == [], "official.sql stays a comment-only stub until T25"
        else:
            assert statements, f"{path} has no statements"


def test_main_refuses_to_derive_into_the_raw_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert derive.main(["--schema", "public", "--raw-schema", "public"]) == 2

    assert "SLM_SCHEMA" in capsys.readouterr().err


def test_main_refuses_the_official_stub_before_connecting(capsys: pytest.CaptureFixture[str]) -> None:
    assert derive.main(["--adapter", "official", "--schema", "slm_never_created"]) == 2

    err = capsys.readouterr().err
    assert "official.sql" in err
    assert "TODO(organiser)" in err


# ----------------------------------------------------------------- the official adapter stub


def test_the_official_adapter_is_a_todo_stub_that_lists_every_required_column() -> None:
    text = (ROOT / "sql/adapters/official.sql").read_text()

    assert "TODO(organiser)" in text
    for view, columns in derive.ADAPTER_VIEWS.items():
        assert f"${{slm}}.{view}" in text
        for column in columns:
            assert re.search(rf"^--\s+{column}\s", text, re.MULTILINE), f"{view}.{column} is not listed"


def test_no_sql_file_reads_the_ground_truth_labels() -> None:
    for path in (ROOT / "sql").rglob("*.sql"):
        text = path.read_text().lower()
        assert "anomaly_labels" not in text, path
        assert "abnormal" not in text, path
        for reader in ("read_csv", "read_text", "read_json", "read_parquet"):
            assert reader not in text, f"{path} reads files with {reader}"


# ----------------------------------------------------------------- the adapter contract


def test_the_adapter_contract_names_both_views() -> None:
    assert set(derive.ADAPTER_VIEWS) == {"bgl_adapted", "os_adapted"}
    assert "source_file" not in derive.ADAPTER_VIEWS["os_adapted"]


def test_an_adapter_that_matches_the_contract_has_no_problems() -> None:
    assert derive.adapter_problems({v: list(c) for v, c in derive.ADAPTER_VIEWS.items()}) == []


def test_adapter_problems_name_missing_views_and_missing_or_extra_columns() -> None:
    found = {"bgl_adapted": [*derive.ADAPTER_VIEWS["bgl_adapted"][:-1], "source_file"]}

    problems = derive.adapter_problems(found)

    assert "os_adapted is missing" in problems
    assert "bgl_adapted lacks message" in problems
    assert "bgl_adapted has extra column source_file" in problems
