"""The Pattern dictionary step (T9) in scripts/derive.py and sql/derive/20_patterns.sql, no database."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import derive
from gen_pattern_sql import TEMPLATE_FILES, Template

ROOT = Path(__file__).parents[2]
PATTERNS_SQL = ROOT / "sql/derive/20_patterns.sql"

# The generic g10 mask chain of #6 (research/6-NoNaim95 in_db/mask_ga.py, G10), in order. The
# label-tuned v10 adds keyword rules on top; none of them may appear (#6 Decision proposal).
G10_RULES = (
    (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<*>"),
    (r"\d+\.\d+\.\d+\.\d+(:\d+)?", "<*>"),
    (r"\b([0-9A-Fa-f]{2}:){2,}[0-9A-Fa-f]{2}\b", "<*>"),
    (r"\b[\w-]+(\.[\w-]+)+\.(com|net|org|edu|us|local|io)\b", "<*>"),
    (r"(/[^\s/:,()\[\]\"']+)+/?", "<*>"),
    (r"[^\s(),=:\[\].\"']*\d[^\s(),=:\[\].\"']*", "<*>"),
    (r"<\*>([.,:]<\*>)+", "<*>"),
    (r"(?i)\b(true|false|null|root|admin)\b", "<*>"),
    (r"\.*(<\*>)+", "<*>"),
    (r"<\*>([.,:]<\*>)+", "<*>"),
    (r"<\*>( <\*>)+", "<*>"),
)
V10_ONLY = ("chdir", "EDRAM|directory", "node map file", "coordinate", "ASSERT condition")


def write_templates(data_dir: Path) -> None:
    for dataset, name in TEMPLATE_FILES.items():
        path = data_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"EventId,EventTemplate,Occurrences\nE1,{dataset} <*>,1\n")


# ----------------------------------------------------------------- the step file


def test_the_pattern_step_runs_after_the_entries_and_before_the_indexes() -> None:
    names = [p.name for p in derive.steps("dev", ROOT / "sql")]

    assert names.index("10_entries.sql") < names.index("20_patterns.sql") < names.index("90_indexes.sql")


def sql_strings(text: str) -> list[str]:
    return [s.replace("''", "'") for s in re.findall(r"'((?:[^']|'')*)'", text)]


def test_tier2_is_the_generic_g10_mask_chain_in_order() -> None:
    text = PATTERNS_SQL.read_text()
    macro = next(s for s in derive.split_statements(text) if "pattern_mask_g10(" in s and "MACRO" in s)
    strings = sql_strings(macro)

    assert [(strings[i], strings[i + 1]) for i in range(0, len(strings) - 1, 3)] == list(G10_RULES)
    assert strings[2::3] == ["g"] * len(G10_RULES)
    for rule in V10_ONLY:
        assert rule not in macro


def test_the_family_rule_drops_a_trailing_over_n_seconds() -> None:
    text = PATTERNS_SQL.read_text()

    assert r"' over <\*> seconds$'" in text


def test_the_step_puts_the_tier1_sql_where_the_placeholder_is() -> None:
    assert "${pattern_tier1}" in PATTERNS_SQL.read_text()


# ----------------------------------------------------------------- placeholders


def test_variables_default_to_tier1_off() -> None:
    values = derive.variables("slm", "public", (26, 10, 1))

    off = 'CREATE OR REPLACE MACRO "slm".pattern_tier1_bgl(m) AS CAST(NULL AS VARCHAR)'
    assert off in values["pattern_tier1"]


def test_variables_carry_the_generated_chain_when_tier1_is_on() -> None:
    values = derive.variables(
        "slm_t9", "public", (26, 10, 1), tier1={"bgl": [Template("E9", "a <*>")], "openstack": []}
    )

    assert 'CREATE OR REPLACE MACRO "slm_t9".pattern_tier1_bgl(m) AS CASE ' in values["pattern_tier1"]
    assert "THEN 'E9'" in values["pattern_tier1"]


def test_the_pattern_step_renders_with_tier1_on_and_off() -> None:
    tier1 = {"bgl": [Template("E1", "it's a;b <*>")], "openstack": [Template("E2", "b <*>")]}
    for templates in (None, tier1):
        values = derive.variables("slm", "public", (26, 10, 1), tier1=templates)
        statements = derive.split_statements(derive.render(PATTERNS_SQL.read_text(), values))
        assert any('CREATE OR REPLACE TABLE "slm".pattern_dict' in s for s in statements)
        assert any('CREATE OR REPLACE TABLE "slm".pattern AS' in s for s in statements)


# ----------------------------------------------------------------- Tier 1 switch


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in ("SLM_PATTERN_TIER1", "SLM_SCHEMA", "SLM_RAW_SCHEMA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    # Nothing listens here, so a test that got past the checks fails fast instead of deriving.
    monkeypatch.setenv("DATABASE_URL", "host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1")
    return tmp_path


def test_tier1_on_with_missing_files_stops_before_connecting(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert derive.main(["--schema", "slm_never_created"]) == 2

    err = capsys.readouterr().err
    assert "make fetch-data" in err
    assert "SLM_PATTERN_TIER1=0" in err
    assert str(env / TEMPLATE_FILES["bgl"]) in err


def test_tier1_off_does_not_need_the_files(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SLM_PATTERN_TIER1", "0")

    # It gets past the template check and fails only on the missing database.
    assert derive.main(["--schema", "slm_never_created"]) == 1

    assert "fetch-data" not in capsys.readouterr().err


def test_tier1_reads_the_template_files_before_connecting(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_templates(env)

    assert derive.main(["--schema", "slm_never_created"]) == 1

    out = capsys.readouterr().out
    assert "Pattern Tier 1 on: Loghub-2.0 templates, 1 for bgl, 1 for openstack" in out


# ----------------------------------------------------------------- --only


def test_only_selects_derive_steps_by_name_without_the_adapter_and_seeds() -> None:
    files = derive.steps("dev", ROOT / "sql")

    selected = derive.select_steps(files, ["20_patterns", "90_indexes"])

    assert [p.name for p in selected] == ["20_patterns.sql", "90_indexes.sql"]


def test_only_keeps_the_run_order_whatever_the_argument_order() -> None:
    files = derive.steps("dev", ROOT / "sql")

    assert [p.name for p in derive.select_steps(files, ["90_indexes", "10_entries"])] == [
        "10_entries.sql",
        "90_indexes.sql",
    ]


def test_only_with_an_unknown_step_names_the_derive_steps() -> None:
    files = derive.steps("dev", ROOT / "sql")

    with pytest.raises(derive.DeriveError, match=r"unknown derive step 'nope'.*20_patterns"):
        derive.select_steps(files, ["nope"])


def test_only_refuses_the_adapter_and_the_seeds() -> None:
    files = derive.steps("dev", ROOT / "sql")

    with pytest.raises(derive.DeriveError, match="unknown derive step 'dev'"):
        derive.select_steps(files, ["dev"])
    with pytest.raises(derive.DeriveError, match="unknown derive step 'ownership_stub'"):
        derive.select_steps(files, ["ownership_stub"])


def test_the_rendered_pattern_step_contains_the_tier1_macros_and_no_placeholder() -> None:
    values = derive.variables("slm", "public", (26, 10, 1))
    statements = derive.split_statements(derive.render(PATTERNS_SQL.read_text(), values))

    assert not [s for s in statements if "${" in s]
    assert any(s.startswith('CREATE OR REPLACE MACRO "slm".pattern_tier1_bgl(m)') for s in statements)


def test_placeholder_names_may_contain_digits() -> None:
    assert derive.render("SELECT ${tier1}", {"tier1": "1"}) == "SELECT 1"
    with pytest.raises(derive.DeriveError, match=r"\$\{x2\}"):
        derive.render("SELECT ${x2}", {"tier1": "1"})


def test_the_updates_touch_only_log_entries_whose_pattern_changes() -> None:
    # An UPDATE through an existing inverted index costs about 120 times more (205 s against 1.7 s
    # for BGL) and stalls the shared server, so a re-run must leave unchanged rows alone.
    updates = [s for s in derive.split_statements(PATTERNS_SQL.read_text()) if s.startswith("UPDATE")]

    assert len(updates) == 4
    for update in updates:
        assert "e.template_id IS DISTINCT FROM d.template_id" in update
        assert "e.family_id IS DISTINCT FROM d.family_id" in update
        assert "e.pattern_key IS NULL" in update


# ----------------------------------------------------------------- the Pattern search index (T14)


@pytest.mark.parametrize("version", [(26, 10, 1), (26, 11), (27, 0, 0)])
def test_the_pattern_text_dictionary_lowercases_and_stems_english_on_26_10_1(
    version: tuple[int, ...],
) -> None:
    ddl = derive.pattern_dictionary_ddl('"slm".pattern_text', version)

    assert ddl == (
        "CREATE TEXT SEARCH DICTIONARY \"slm\".pattern_text AS split_text(case := 'lower') "
        "| stem_words('en_US.UTF-8') WITH (frequency, norm)"
    )


@pytest.mark.parametrize("version", [(26, 9, 1, 3), (26, 10, 0)])
def test_older_servers_get_the_legacy_stemming_dictionary(version: tuple[int, ...]) -> None:
    ddl = derive.pattern_dictionary_ddl('"slm".pattern_text', version)

    assert ddl.startswith("CREATE TEXT SEARCH DICTIONARY \"slm\".pattern_text (TEMPLATE = 'text'")
    for option in ("locale = 'en_US.UTF-8'", "\"case\" = 'lower'", "stemming = true", "frequency = true",
                   "norm = true"):  # fmt: skip
        assert option in ddl
    assert " AS " not in ddl


def test_variables_carry_the_pattern_text_dictionary() -> None:
    values = derive.variables("slm_t14", "public", (26, 10, 1))

    assert values["create_pattern_text_dictionary"].startswith(
        'CREATE TEXT SEARCH DICTIONARY "slm_t14".pattern_text AS'
    )


def test_the_pattern_step_indexes_the_catalogue_after_building_it() -> None:
    # In 20_patterns, not 90_indexes: `derive.py --only 20_patterns` rebuilds the pattern table with
    # CREATE OR REPLACE, which drops its index, so the step must build the index again itself.
    values = derive.variables("slm", "public", (26, 10, 1))
    statements = derive.split_statements(derive.render(PATTERNS_SQL.read_text(), values))

    def position(prefix: str) -> int:
        found = [i for i, s in enumerate(statements) if s.startswith(prefix)]
        assert len(found) == 1, prefix
        return found[0]

    table = position('CREATE OR REPLACE TABLE "slm".pattern AS')
    drop_index = position('DROP INDEX IF EXISTS "slm".pattern_idx')
    drop_dictionary = position('DROP TEXT SEARCH DICTIONARY IF EXISTS "slm".pattern_text')
    dictionary = position('CREATE TEXT SEARCH DICTIONARY "slm".pattern_text')
    index = position('CREATE INDEX pattern_idx ON "slm".pattern USING inverted')
    refresh = position('VACUUM (REFRESH_TABLE) "slm".pattern')
    # An index that uses the dictionary blocks DROP TEXT SEARCH DICTIONARY.
    assert table < drop_index < drop_dictionary < dictionary < index < refresh
    assert 'template_text "slm".pattern_text' in statements[index]
    assert "dataset" in statements[index]


def test_a_derived_schema_without_the_pattern_index_is_not_usable() -> None:
    assert "pattern_idx" in derive.REQUIRED_INDEXES
