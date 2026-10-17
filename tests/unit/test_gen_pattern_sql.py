"""The pure parts of scripts/gen_pattern_sql.py: template files, regexes, order and the SQL."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import gen_pattern_sql as gen
from gen_pattern_sql import Template

HEADER = "EventId,EventTemplate,Occurrences\n"


def write_templates(data_dir: Path, dataset: str, body: str) -> Path:
    path = data_dir / gen.TEMPLATE_FILES[dataset]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + body, encoding="utf-8")
    return path


# ----------------------------------------------------------------- template files


def test_template_files_are_the_loghub2_files_fetch_data_writes() -> None:
    assert gen.TEMPLATE_FILES == {
        "bgl": "lh2/BGL/BGL_full.log_templates.csv",
        "openstack": "lh2/OpenStack/OpenStack_full.log_templates.csv",
    }


def test_read_templates_keeps_event_id_and_text(tmp_path: Path) -> None:
    path = write_templates(
        tmp_path,
        "openstack",
        'E42,"<*> ""GET <*> <*>"" status: <*>",90475\nE37,[instance: <*>] Creating image,2066\n',
    )

    assert gen.read_templates(path) == [
        Template("E42", '<*> "GET <*> <*>" status: <*>'),
        Template("E37", "[instance: <*>] Creating image"),
    ]


def test_read_templates_rejects_a_file_with_another_header(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("LineId,Content\n1,x\n")

    with pytest.raises(gen.TemplateFileError, match="EventId"):
        gen.read_templates(path)


def test_read_templates_rejects_a_duplicate_event_id(tmp_path: Path) -> None:
    path = write_templates(tmp_path, "bgl", "E1,a <*>,1\nE1,b <*>,1\n")

    with pytest.raises(gen.TemplateFileError, match="E1"):
        gen.read_templates(path)


def test_load_templates_reads_both_datasets(tmp_path: Path) -> None:
    write_templates(tmp_path, "bgl", "E1,a <*>,1\n")
    write_templates(tmp_path, "openstack", "E2,b <*>,1\n")

    assert gen.load_templates(tmp_path) == {
        "bgl": [Template("E1", "a <*>")],
        "openstack": [Template("E2", "b <*>")],
    }


def test_missing_template_files_name_fetch_data_and_the_switch(tmp_path: Path) -> None:
    write_templates(tmp_path, "bgl", "E1,a <*>,1\n")

    with pytest.raises(gen.TemplatesMissingError) as excinfo:
        gen.load_templates(tmp_path)

    message = str(excinfo.value)
    assert "OpenStack_full.log_templates.csv" in message
    assert "BGL_full" not in message
    assert "make fetch-data" in message
    assert "SLM_PATTERN_TIER1=0" in message


# ----------------------------------------------------------------- regexes


@pytest.mark.parametrize(
    ("template", "wildcard", "regex"),
    [
        ("generating core.<*>", gen.STRICT, r"generating\s+core\.\S+"),
        ("generating core.<*>", gen.LOOSE, r"generating\s+core\..*"),
        ("a  b\tc", gen.STRICT, r"a\s+b\s+c"),
        (
            "(dcr <*>) [x] {y} a|b ^$ ? + * \\",
            gen.STRICT,
            r"\(dcr\s+\S+\)\s+\[x\]\s+\{y\}\s+a\|b\s+\^\$\s+\?\s+\+\s+\*\s+\\",
        ),
        ("<*>:<*>", gen.STRICT, r"\S+:\S+"),
        ("costs 5% #1 'q' \"d\" é", gen.STRICT, "costs\\s+5%\\s+#1\\s+'q'\\s+\"d\"\\s+é"),
    ],
)
def test_to_regex_escapes_literals_and_maps_wildcards_and_whitespace(
    template: str, wildcard: str, regex: str
) -> None:
    assert gen.to_regex(template, wildcard) == regex


@pytest.mark.parametrize(
    ("template", "message", "strict", "loose"),
    [
        ("generating core.<*>", "generating core.1234", True, True),
        ("generating core.<*>", "generating core.12 34", False, True),
        ("<*> L3 <*> error(s) (dcr <*>) detected", "1 L3 EDRAM error(s) (dcr 0x0157) detected", True, True),
        ("job <*> timed out. Block freed.", "Kill job 1 timed out. Block freed.", False, False),
        ("a <*>", "a ", False, True),
        ("a.b", "aXb", False, False),
    ],
)
def test_regexes_full_match_like_the_reference_matcher(
    template: str, message: str, strict: bool, loose: bool
) -> None:
    # Python's re agrees with RE2 on these constructs; the db tests run the same regexes in SereneDB.
    assert bool(re.fullmatch(gen.to_regex(template, gen.STRICT), message)) is strict
    assert bool(re.fullmatch(gen.to_regex(template, gen.LOOSE), message)) is loose


# ----------------------------------------------------------------- specificity order


def test_more_literal_characters_come_first_then_fewer_wildcards_then_event_id() -> None:
    templates = [
        Template("E3", "a <*>"),
        Template("E10", "a b <*> <*>"),
        Template("E2", "a b <*>"),
        Template("E1", "<*> a b"),
        Template("E9", "longer literal text <*>"),
    ]

    assert [t.event_id for t in gen.ordered(templates)] == ["E9", "E1", "E2", "E10", "E3"]


def test_literal_characters_ignore_wildcards_and_whitespace() -> None:
    assert gen.literal_chars("a <*> bc\t d") == 4


# ----------------------------------------------------------------- SQL


def test_sql_literal_doubles_single_quotes_and_keeps_backslashes() -> None:
    assert gen.sql_literal("it's \\S+") == "'it''s \\S+'"


def test_case_chain_tries_every_strict_pattern_before_any_loose_one() -> None:
    chain = gen.case_chain([Template("E1", "a <*>"), Template("E2", "a b <*>")], "m")

    whens = re.findall(r"WHEN regexp_full_match\(m, '([^']*)'\) THEN '(E\d+)'", chain)
    assert whens == [
        (r"a\s+b\s+\S+", "E2"),
        (r"a\s+\S+", "E1"),
        (r"a\s+b\s+.*", "E2"),
        (r"a\s+.*", "E1"),
    ]
    assert chain.startswith("CASE ")
    assert chain.endswith(" END")


def test_tier1_sql_creates_one_macro_per_dataset_and_the_template_table() -> None:
    sql = gen.tier1_sql(
        '"slm"', {"bgl": [Template("E1", "it's <*>")], "openstack": [Template("E2", "b <*>")]}
    )

    assert 'CREATE OR REPLACE MACRO "slm".pattern_tier1_bgl(m) AS CASE ' in sql
    assert 'CREATE OR REPLACE MACRO "slm".pattern_tier1_openstack(m) AS CASE ' in sql
    assert "CREATE OR REPLACE TEMPORARY TABLE pattern_tier1_template AS" in sql
    assert "('bgl', 'E1', 'it''s <*>')" in sql
    assert "('openstack', 'E2', 'b <*>')" in sql
    assert not sql.rstrip().endswith(";")


def test_tier1_sql_when_off_assigns_nothing() -> None:
    sql = gen.tier1_sql('"slm"', None)

    assert 'CREATE OR REPLACE MACRO "slm".pattern_tier1_bgl(m) AS CAST(NULL AS VARCHAR)' in sql
    assert 'CREATE OR REPLACE MACRO "slm".pattern_tier1_openstack(m) AS CAST(NULL AS VARCHAR)' in sql
    assert (
        "CREATE OR REPLACE TEMPORARY TABLE pattern_tier1_template "
        "(dataset VARCHAR, event_id VARCHAR, template_text VARCHAR)"
    ) in sql
    assert "regexp_full_match" not in sql


def test_tier1_sql_has_one_statement_per_object() -> None:
    import derive

    on = derive.split_statements(
        gen.tier1_sql('"slm"', {"bgl": [Template("E1", "a;b <*>")], "openstack": []})
    )
    off = derive.split_statements(gen.tier1_sql('"slm"', None))

    assert len(on) == len(off) == 3


# ----------------------------------------------------------------- command line


def test_main_prints_the_generated_sql(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_templates(tmp_path, "bgl", "E1,a <*>,1\n")
    write_templates(tmp_path, "openstack", "E2,b <*>,1\n")

    assert gen.main(["--data-dir", str(tmp_path), "--schema", "slm_x"]) == 0

    out = capsys.readouterr().out
    assert 'CREATE OR REPLACE MACRO "slm_x".pattern_tier1_bgl(m)' in out


def test_main_reports_missing_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert gen.main(["--data-dir", str(tmp_path)]) == 2

    assert "make fetch-data" in capsys.readouterr().err


def test_main_rejects_a_schema_that_is_not_a_plain_identifier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_templates(tmp_path, "bgl", "E1,a <*>,1\n")
    write_templates(tmp_path, "openstack", "E2,b <*>,1\n")

    assert gen.main(["--data-dir", str(tmp_path), "--schema", 'x"; DROP TABLE y; --']) == 2

    assert "identifier" in capsys.readouterr().err
