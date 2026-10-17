"""tests/support/optional_data.py: when the optional Loghub-2.0 template files count as missing."""

from __future__ import annotations

from pathlib import Path

import optional_data
import pytest

import gen_pattern_sql

TEMPLATE_HEADER = "EventId,EventTemplate,Occurrences\n"


def write_templates(data_dir: Path, *datasets: str) -> None:
    for dataset in datasets or gen_pattern_sql.DATASETS:
        path = data_dir / gen_pattern_sql.TEMPLATE_FILES[dataset]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE_HEADER)


def test_no_problem_when_both_template_files_are_there(tmp_path: Path) -> None:
    write_templates(tmp_path)

    assert optional_data.templates_problem(tmp_path) is None


def test_missing_template_files_are_named_with_the_command_that_fetches_them(tmp_path: Path) -> None:
    problem = optional_data.templates_problem(tmp_path)

    assert problem is not None
    assert f"Loghub-2.0 templates not found in {tmp_path / 'lh2'}" in problem
    assert "SLM_DATA_DIR" in problem
    assert "run `make fetch-data`" in problem
    assert "BGL/BGL_full.log_templates.csv" in problem
    assert "OpenStack/OpenStack_full.log_templates.csv" in problem


def test_one_missing_template_file_is_a_problem_that_names_only_that_file(tmp_path: Path) -> None:
    write_templates(tmp_path, "bgl")

    problem = optional_data.templates_problem(tmp_path)

    assert problem is not None
    assert "OpenStack/OpenStack_full.log_templates.csv" in problem
    assert "BGL/BGL_full.log_templates.csv" not in problem


def test_the_default_data_dir_is_slm_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))

    assert str(tmp_path / "lh2") in str(optional_data.templates_problem())
    write_templates(tmp_path)
    assert optional_data.templates_problem() is None


@pytest.mark.parametrize(
    ("flag", "templates", "expected"),
    [
        (None, True, True),
        (None, False, False),
        ("1", True, True),
        ("1", False, False),
        ("0", True, False),
        ("0", False, False),
    ],
)
def test_derives_in_the_db_tests_run_tier1_only_when_it_is_on_and_the_templates_are_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str | None, templates: bool, expected: bool
) -> None:
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    if flag is None:
        monkeypatch.delenv("SLM_PATTERN_TIER1", raising=False)
    else:
        monkeypatch.setenv("SLM_PATTERN_TIER1", flag)
    if templates:
        write_templates(tmp_path)

    assert optional_data.derive_with_tier1() is expected


def test_the_loghub2_label_tables_are_the_ones_the_dev_loader_writes() -> None:
    import dev_loader

    assert set(optional_data.LOGHUB2_TABLES) == {t for t in dev_loader.TABLES if t.startswith("lh2_")}
