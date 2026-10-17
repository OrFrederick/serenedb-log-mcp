"""Pure checks on `list_incidents` and the OpenStack Request Failure step (T27).

The SQL runs against SereneDB in tests/db/incidents/ and on the full Base Corpus in
tests/golden/test_incidents_golden.py and tests/corpus/test_incidents_corpus.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import anyio
import pytest
from mcp import Client

import derive
from serenedb_log_mcp.corpus.datasets import DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, LIFECYCLE_KEY
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools import list_incidents

ROOT = Path(__file__).resolve().parents[2]
STEP = ROOT / "sql" / "derive" / "35_os_incidents.sql"
ALERTS = ("KERNDTLB", "KERNMNTF", "APPSEV")


def _description() -> str:
    async def listed() -> str:
        async with Client(create_server(), mode="legacy") as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            return tools["list_incidents"].description or ""

    return anyio.run(listed)


# ----------------------------------------------------------------- the description


def test_the_description_documents_the_stable_band_of_the_gap() -> None:
    text = _description()

    assert "60" in text
    assert "600" in text
    assert "stable" in text


def test_the_description_relates_pattern_key_to_list_patterns_and_alert_category() -> None:
    text = _description()

    assert "list_patterns" in text
    assert "alert_category" in text
    assert "template_id" in text


def test_the_description_keeps_log_entries_and_incidents_apart() -> None:
    text = _description()

    assert "entries_total" in text
    assert "incidents_total" in text
    assert "Spread" in text


# ----------------------------------------------------------------- which path a call takes


def test_a_default_call_without_filters_reads_the_precomputed_table() -> None:
    assert list_incidents.reads_table(DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, []) is True


def test_a_default_call_with_only_a_pattern_key_reads_the_precomputed_table() -> None:
    # pattern_key selects rows of the table; it does not change what was chained.
    assert list_incidents.reads_table(DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, [], pattern_key="KERNDTLB") is True


def test_a_default_call_with_a_filter_chains_the_filtered_set() -> None:
    assert list_incidents.reads_table(DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, ["time_from"]) is False


@pytest.mark.parametrize(("gap_s", "merge_scope"), [(60, "all"), (DEFAULT_GAP_S, "source")])
def test_a_non_default_call_chains_at_query_time(gap_s: int, merge_scope: str) -> None:
    assert list_incidents.reads_table(gap_s, merge_scope, ["components"]) is False
    assert list_incidents.reads_table(gap_s, merge_scope, [], pattern_key="KERNDTLB") is False


@pytest.mark.parametrize(("gap_s", "merge_scope"), [(60, "all"), (DEFAULT_GAP_S, "source")])
def test_a_non_default_call_without_a_narrowing_filter_is_refused_with_a_suggestion(
    gap_s: int, merge_scope: str
) -> None:
    with pytest.raises(ScopeError) as raised:
        list_incidents.reads_table(gap_s, merge_scope, [])

    message = str(raised.value)
    assert "pattern_key" in message
    assert "time_from" in message


# ----------------------------------------------------------------- pattern_key


def test_an_alert_category_is_a_bgl_pattern_key_in_its_canonical_case() -> None:
    assert list_incidents.pattern_key_of("bgl", "kerndtlb", ALERTS) == "KERNDTLB"


def test_a_template_id_is_a_pattern_key_of_either_dataset() -> None:
    assert list_incidents.pattern_key_of("bgl", "A0A0A0A0A0A0A001", ALERTS) == "a0a0a0a0a0a0a001"
    assert list_incidents.pattern_key_of("openstack", " b0b0b0b0b0b0b003 ", ()) == "b0b0b0b0b0b0b003"


def test_the_lifecycle_key_is_an_openstack_pattern_key_only() -> None:
    assert list_incidents.pattern_key_of("openstack", LIFECYCLE_KEY, ()) == LIFECYCLE_KEY
    with pytest.raises(ScopeError):
        list_incidents.pattern_key_of("bgl", LIFECYCLE_KEY, ALERTS)


def test_an_unknown_pattern_key_names_the_three_kinds_of_key() -> None:
    with pytest.raises(ScopeError) as raised:
        list_incidents.pattern_key_of("bgl", "KERNDTLBX", ALERTS)

    message = str(raised.value)
    assert "KERNDTLB" in message  # the close alert category
    assert "list_patterns" in message


def test_an_alert_category_is_not_an_openstack_pattern_key() -> None:
    with pytest.raises(ScopeError) as raised:
        list_incidents.pattern_key_of("openstack", "KERNDTLB", ())

    assert "template" in str(raised.value)


def test_no_pattern_key_is_none() -> None:
    assert list_incidents.pattern_key_of("bgl", None, ALERTS) is None
    assert list_incidents.pattern_key_of("bgl", "  ", ALERTS) is None


# ----------------------------------------------------------------- the OpenStack derive step


@pytest.fixture(scope="module")
def step_sql() -> str:
    return "\n".join(derive.split_statements(STEP.read_text()))


def test_the_openstack_step_runs_after_the_incident_table_and_before_the_indexes() -> None:
    names = [path.name for path in derive.steps("dev") if path.parent.name == "derive"]

    assert names.index("30_incidents.sql") < names.index(STEP.name) < names.index("90_indexes.sql")


def test_the_openstack_step_replaces_its_own_rows_only(step_sql: str) -> None:
    statements = derive.split_statements(STEP.read_text())

    assert statements[0].strip() == "DELETE FROM ${slm}.incident WHERE dataset = 'openstack'"
    assert statements[1].lstrip().startswith("INSERT INTO ${slm}.incident")
    assert "CREATE" not in step_sql.upper()


def test_the_openstack_step_chains_request_failures_on_the_utc_column(step_sql: str) -> None:
    lagged = re.findall(r"lag\(\s*(\w+)\s*\)\s*OVER", step_sql)

    assert set(lagged) == {"epoch_utc"}
    assert "level IN ('ERROR', 'CRITICAL') OR http_status >= 500" in step_sql
    assert "300 AS gap_s" in step_sql
    assert "'all' AS merge_scope" in step_sql


def test_the_openstack_step_uses_the_registered_lifecycle_key(step_sql: str) -> None:
    assert f"'{LIFECYCLE_KEY}'" in step_sql


def test_the_openstack_step_never_reads_the_ground_truth_labels(step_sql: str) -> None:
    assert "abnormal" not in step_sql.lower()
    assert "source_file" not in step_sql
