"""The pure parts of scripts/derive.py: step order, statement splitting, placeholders, DDL choice."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
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


@pytest.mark.parametrize("placeholder", ["${SLM}", "${slm2}", "${Raw_Schema}"])
def test_placeholders_with_capitals_or_digits_are_checked_too(placeholder: str) -> None:
    with pytest.raises(DeriveError, match="unknown placeholder"):
        derive.render(f"SELECT * FROM {placeholder}.t", {"slm": '"slm"'})


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
    for table, columns in derive.ADAPTER_TABLES.items():
        assert f"TEMPORARY TABLE {table}" in text
        for column, kind in columns:
            listed = rf"^--\s+{column}\s+{kind}\s"
            assert re.search(listed, text, re.MULTILINE), f"{table}.{column} {kind} is not listed"


def test_no_sql_file_reads_the_ground_truth_labels() -> None:
    for path in (ROOT / "sql").rglob("*.sql"):
        text = path.read_text().lower()
        assert "anomaly_labels" not in text, path
        assert "abnormal" not in text, path
        for reader in ("read_csv", "read_text", "read_json", "read_parquet"):
            assert reader not in text, f"{path} reads files with {reader}"


# ----------------------------------------------------------------- the adapter contract


def contract_columns(table: str) -> list[tuple[str, str]]:
    return list(derive.ADAPTER_TABLES[table])


def test_the_adapter_contract_names_both_tables_with_their_types() -> None:
    assert set(derive.ADAPTER_TABLES) == {"bgl_adapted", "os_adapted"}
    os_columns = dict(derive.ADAPTER_TABLES["os_adapted"])
    assert "source_file" not in os_columns
    assert os_columns["epoch_utc"] == "DOUBLE"
    assert os_columns["ts"] == "TIMESTAMP"
    assert os_columns["entry_id"] == "BIGINT"


def test_an_adapter_that_matches_the_contract_has_no_problems() -> None:
    found = {table: contract_columns(table) for table in derive.ADAPTER_TABLES}

    assert derive.adapter_problems(found) == []


def test_adapter_problems_name_missing_tables_and_missing_or_extra_columns() -> None:
    found = {"bgl_adapted": [*contract_columns("bgl_adapted")[:-1], ("source_file", "VARCHAR")]}

    problems = derive.adapter_problems(found)

    assert "os_adapted is missing" in problems
    assert "bgl_adapted lacks message" in problems
    assert "bgl_adapted has extra column source_file" in problems


def test_adapter_problems_name_a_column_of_the_wrong_type() -> None:
    # epoch_utc as a TIMESTAMP would silently break every gap computation (#1).
    columns = [(c, "TIMESTAMP" if c == "epoch_utc" else t) for c, t in contract_columns("bgl_adapted")]
    found = {"bgl_adapted": columns, "os_adapted": contract_columns("os_adapted")}

    assert derive.adapter_problems(found) == ["bgl_adapted.epoch_utc is TIMESTAMP, expected DOUBLE"]


def test_a_missing_table_carries_the_database_reason() -> None:
    found = {"os_adapted": contract_columns("os_adapted")}

    problems = derive.adapter_problems(found, {"bgl_adapted": "permission denied for table bgl"})

    assert problems == ["bgl_adapted is missing (permission denied for table bgl)"]


CREATE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(TEMP(?:ORARY)?\s+)?(VIEW|TABLE)\s+([\w.${}\"]+)", re.IGNORECASE
)


def test_the_dev_adapter_creates_only_temporary_tables() -> None:
    # A persistent relation that reads the raw tables (a view) blocks every reload of them with
    # DependentObjectsStillExist; a temporary view cannot be read at all on SereneDB 26.09.1.3
    # (InsufficientPrivilege). A temporary table has neither problem and dies with the session.
    created = CREATE_RE.findall((ROOT / "sql/adapters/dev.sql").read_text())

    assert sorted(name for _, _, name in created) == sorted(derive.ADAPTER_TABLES)
    assert all(temporary and kind.upper() == "TABLE" for temporary, kind, _ in created)


def test_no_sql_file_creates_a_view() -> None:
    for path in (ROOT / "sql").rglob("*.sql"):
        text = derive.render(path.read_text(), derive.variables("slm", "public", (26, 10, 1)))
        for statement in derive.split_statements(text):
            for _, kind, name in CREATE_RE.findall(statement):
                assert kind.upper() != "VIEW", f"{path.name} creates the view {name}"


def test_the_derive_steps_read_the_adapter_tables_from_the_temporary_schema() -> None:
    text = (ROOT / "sql/derive/10_entries.sql").read_text()

    for table in derive.ADAPTER_TABLES:
        assert f"FROM temp.main.{table}" in text


# ----------------------------------------------------------------- failures


class FailingConnection:
    """Runs statements until the n-th, which raises `error`."""

    def __init__(self, fail_at: int, error: Exception) -> None:
        self.fail_at = fail_at
        self.error = error
        self.executed: list[str] = []

    def execute(self, statement: str) -> None:
        if len(self.executed) + 1 == self.fail_at:
            raise self.error
        self.executed.append(statement)


def test_a_failing_statement_names_its_step_and_statement(tmp_path: Path) -> None:
    step = tmp_path / "derive" / "90_indexes.sql"
    step.parent.mkdir()
    step.write_text(
        "CREATE INDEX a_idx ON t USING inverted (x);\nCREATE INDEX b_idx ON u USING inverted (y);\n"
    )
    denied = psycopg.errors.InsufficientPrivilege("permission denied for table u")

    with pytest.raises(derive.StepFailed) as excinfo:
        derive.run(FailingConnection(2, denied), [step], {})  # type: ignore[arg-type]

    assert excinfo.value.step == "derive/90_indexes.sql"
    assert excinfo.value.statement == "CREATE INDEX b_idx ON u USING inverted (y)"
    assert excinfo.value.cause is denied


def step_failed(error: psycopg.Error) -> derive.StepFailed:
    return derive.StepFailed("derive/90_indexes.sql", "CREATE INDEX b_idx ON u", error)


def test_a_deterministic_database_error_is_not_reported_as_a_lost_connection() -> None:
    message, code = derive.failure(
        step_failed(psycopg.errors.InsufficientPrivilege("permission denied for table u")), "slm", began=True
    )

    assert code == 2
    assert "derive/90_indexes.sql" in message
    assert "InsufficientPrivilege: permission denied for table u" in message
    assert "half-built" in message
    assert "run it again" not in message
    assert "same error" in message


def test_a_lost_connection_during_a_step_says_to_run_again() -> None:
    message, code = derive.failure(
        step_failed(psycopg.OperationalError("server closed the connection")), "slm", began=True
    )

    assert code == 1
    assert "make derive" in message
    assert "half-built" in message


def test_a_failed_connect_says_to_start_the_database_and_built_nothing() -> None:
    message, code = derive.failure(psycopg.OperationalError("connection refused"), "slm", began=False)

    assert code == 1
    assert "connection refused" in message
    assert "half-built" not in message


def test_main_gives_up_quickly_on_an_unreachable_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "host=127.0.0.1 port=1 dbname=postgres user=postgres")
    # Pattern Tier 1 would otherwise stop the run earlier when the Loghub-2.0 files are absent (T9).
    monkeypatch.setenv("SLM_PATTERN_TIER1", "0")

    assert derive.main(["--schema", "slm_unreachable"]) == 1

    err = capsys.readouterr().err
    assert "derive:" in err
    assert "half-built" not in err
    assert derive.CONNECT_TIMEOUT_S <= 10


def test_a_contract_break_after_the_run_began_says_the_schema_is_half_built() -> None:
    message, code = derive.failure(derive.DeriveError("sql/adapters/official.sql breaks"), "slm", began=True)

    assert code == 2
    assert "half-built" in message
