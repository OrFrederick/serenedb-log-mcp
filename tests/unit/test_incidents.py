"""Pure checks on the Incident derive step (T10, #1 Decision proposal).

The SQL itself is exercised against SereneDB in tests/db/test_incidents_db.py and against the full
Base Corpus in tests/corpus/test_incidents_corpus.py. These tests pin the properties that can be
read off the file, because each of them is a decision of the #1 Decision proposal that a later
edit must not lose silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import derive
from serenedb_log_mcp.corpus.datasets import INCIDENT_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
STEP = ROOT / "sql" / "derive" / "30_incidents.sql"

# The Shape labels of the Glossary. An Incident carries exactly one of them.
SHAPES = (
    "single-node storm",
    "single-node isolated",
    "one midplane",
    "multi-midplane",
    "machine-wide",
)
# The columns plan section 2.2 makes binding for every tool that reads the Incident table.
PLAN_COLUMNS = (
    "dataset",
    "incident_id",
    "pattern_key",
    "t0",
    "t1",
    "entries",
    "bursts",
    "nodes",
    "nodecards",
    "midplanes",
    "racks",
    "top_node",
    "top_node_share",
    "shape",
)


@pytest.fixture(scope="module")
def step_sql() -> str:
    """The executable SQL of the step, with the comments stripped.

    The comments name `alert_tag` and `template_id` to explain what the step deliberately does not
    read, so the checks below run on the statements only.
    """
    return "\n".join(derive.split_statements(STEP.read_text()))


def test_the_incident_step_runs_after_the_entries_and_before_the_indexes() -> None:
    names = [path.name for path in derive.steps("dev") if path.parent.name == "derive"]

    assert "30_incidents.sql" in names
    assert names.index("10_entries.sql") < names.index("30_incidents.sql")
    assert names.index("30_incidents.sql") < names.index("90_indexes.sql")


def test_the_step_builds_the_incident_table_and_nothing_else(step_sql: str) -> None:
    created = re.findall(r"CREATE OR REPLACE TABLE \$\{slm\}\.(\w+)", step_sql)

    assert created == ["incident"]
    assert "DROP TABLE" not in step_sql.upper()


def test_the_defaults_are_the_decided_gap_and_merge_scope(step_sql: str) -> None:
    # #1: gap_s = 300 and merge_scope = all. The table records both, so Evidence can quote them.
    assert step_sql.count("300") >= 1
    assert "'all' AS merge_scope" in step_sql
    assert "300 AS gap_s" in step_sql


def test_every_gap_is_computed_on_the_utc_column(step_sql: str) -> None:
    # The DST trap of #1: BGL `ts` is US Pacific local time, so a gap on `ts` is wrong by an hour
    # at the fall-back. Every lag() must read epoch_utc, and no arithmetic may touch `ts`.
    lagged = re.findall(r"lag\(\s*([A-Za-z_.()/\s*0-9]+?)\s*\)\s*OVER", step_sql)

    assert lagged, "the step chains Log Entries with lag()"
    assert set(lagged) == {"epoch_utc"}
    assert "epoch_us(ts)" not in step_sql
    assert not re.search(r"\bts\s*[-+]", step_sql), "no gap arithmetic on the local timestamp"


def test_the_incident_id_is_a_running_sum_cast_to_bigint(step_sql: str) -> None:
    running = re.search(
        r"CAST\(\s*sum\(.*?\)\s*OVER \(PARTITION BY pattern_key ORDER BY epoch_utc, entry_id\s*"
        r"ROWS UNBOUNDED PRECEDING\)\s*AS BIGINT\)\s*(?:AS )?\n?\s*(\w+)",
        step_sql,
        re.DOTALL,
    )

    assert running is not None, "the Incident id is a running sum() over the Pattern, cast to BIGINT"
    assert running.group(1) == "incident_id"


def test_the_step_reads_pattern_key_and_never_the_alert_category(step_sql: str) -> None:
    # Acceptance criterion 4: once T9 fills pattern_key from template_id, re-running `make derive`
    # adds the template-keyed Incidents without a code change. That only holds if the step knows
    # nothing about alert categories.
    assert "pattern_key IS NOT NULL" in step_sql
    assert "alert_tag" not in step_sql
    assert "template_id" not in step_sql


def test_the_shape_labels_are_exactly_the_glossary_list(step_sql: str) -> None:
    labelled = re.findall(r"THEN '([a-z -]+)'|ELSE\s+'([a-z -]+)'\s+END AS shape", step_sql)
    found = {a or b for a, b in labelled}

    assert found == set(SHAPES)


def test_the_glossary_names_every_shape_label() -> None:
    glossary = (ROOT / "GLOSSARY.md").read_text()

    for shape in SHAPES:
        assert shape in glossary, shape


def test_the_registry_lists_the_columns_plan_section_2_2_makes_binding() -> None:
    assert set(PLAN_COLUMNS) <= set(INCIDENT_COLUMNS)
    # The extra columns are provenance (how the table was built) and the UTC bounds, because a
    # tool must never re-derive an epoch from the local timestamp.
    assert set(INCIDENT_COLUMNS) - set(PLAN_COLUMNS) == {"gap_s", "merge_scope", "t0_utc", "t1_utc"}


def test_the_step_selects_every_registered_column(step_sql: str) -> None:
    for column in INCIDENT_COLUMNS:
        assert re.search(rf"\b{column}\b", step_sql), column
