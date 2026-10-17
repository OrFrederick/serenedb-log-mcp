"""Pure checks on `find_precursors` and `list_incidents(similar_to=...)` (T16).

The SQL runs against SereneDB in tests/db/precursors/ and on the full Base Corpus in
tests/golden/test_precursors_golden.py.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp import Client

from serenedb_log_mcp.corpus.datasets import DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, LIFECYCLE_KEY
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools import find_precursors, list_incidents

ALERTS = ("APPSEV", "KERNMC", "KERNREC")


def _tools() -> dict[str, Any]:
    async def listed() -> dict[str, Any]:
        async with Client(create_server(), mode="legacy") as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}

    return anyio.run(listed)


@pytest.fixture(scope="module")
def tools() -> dict[str, Any]:
    return _tools()


# ----------------------------------------------------------------- find_precursors: the description


def test_the_description_states_the_cascade_defaults(tools: dict[str, Any]) -> None:
    text = tools["find_precursors"].description

    assert "default 60" in text
    assert "1 s" in text
    assert "same-timestamp" in text


def test_the_description_labels_every_row_a_hypothesis_and_names_the_empty_answer(
    tools: dict[str, Any],
) -> None:
    text = tools["find_precursors"].description

    assert "Hypothesis" in text
    assert "no consistent precursor found" in text
    assert "min_support" in text
    assert "min_lift" in text
    assert "probe" in text


def test_the_defaults_follow_the_cascade_rule() -> None:
    assert find_precursors.DEFAULT_WINDOW_S == 60
    assert find_precursors.MIN_LAG_S == 1


def test_the_window_cannot_exceed_the_probe_step(tools: dict[str, Any]) -> None:
    """Each Log Entry then falls into the window of at most one probe, which the SQL relies on."""
    window = tools["find_precursors"].input_schema["properties"]["window_s"]

    assert window["maximum"] == find_precursors.PROBE_STEP_S
    assert window["minimum"] == find_precursors.MIN_LAG_S + 1
    assert window["default"] == 60


def test_the_tool_is_read_only_and_requires_a_role(tools: dict[str, Any]) -> None:
    tool = tools["find_precursors"]

    assert tool.annotations.read_only_hint is True
    assert {"role", "dataset", "target_pattern"} <= set(tool.input_schema["required"])


# ----------------------------------------------------------------- find_precursors: the target


def test_an_alert_category_target_is_canonical() -> None:
    assert find_precursors.target_key("bgl", "appsev", ALERTS) == "APPSEV"


def test_a_template_id_target_is_lower_case() -> None:
    assert find_precursors.target_key("openstack", "D0D0D0D0D0D0D006", ()) == "d0d0d0d0d0d0d006"


def test_the_lifecycle_key_is_not_a_target() -> None:
    with pytest.raises(ScopeError) as raised:
        find_precursors.target_key("openstack", LIFECYCLE_KEY, ())

    assert "template_id" in str(raised.value)


def test_an_empty_target_is_refused_with_a_suggestion() -> None:
    with pytest.raises(ScopeError) as raised:
        find_precursors.target_key("bgl", "  ", ALERTS)

    assert "list_patterns" in str(raised.value)


# ----------------------------------------------------------------- list_incidents(similar_to=...)


def test_the_list_incidents_description_documents_similar_to(tools: dict[str, Any]) -> None:
    text = tools["list_incidents"].description

    assert "similar_to" in text
    assert "Jaccard" in text
    assert "BM25" in text
    assert "similar_to" in tools["list_incidents"].input_schema["properties"]


def test_similar_to_without_it_checks_nothing() -> None:
    list_incidents.check_similar_to(None, None, 60, "source", ["time_from"])


def test_similar_to_needs_a_pattern_key() -> None:
    with pytest.raises(ScopeError) as raised:
        list_incidents.check_similar_to(3, None, DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, [])

    assert "pattern_key" in str(raised.value)


def test_similar_to_refuses_the_lifecycle_request_failures() -> None:
    with pytest.raises(ScopeError):
        list_incidents.check_similar_to(3, LIFECYCLE_KEY, DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, [])


@pytest.mark.parametrize(
    ("gap_s", "merge_scope", "filters"),
    [
        (60, DEFAULT_MERGE_SCOPE, []),
        (DEFAULT_GAP_S, "source", []),
        (DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, ["time_from"]),
    ],
)
def test_similar_to_reads_the_precomputed_table_only(
    gap_s: int, merge_scope: str, filters: list[str]
) -> None:
    with pytest.raises(ScopeError) as raised:
        list_incidents.check_similar_to(3, "KERNTERM", gap_s, merge_scope, filters)

    assert "precomputed table" in str(raised.value)


def test_similar_to_with_a_pattern_key_alone_is_accepted() -> None:
    list_incidents.check_similar_to(98, "KERNTERM", DEFAULT_GAP_S, DEFAULT_MERGE_SCOPE, [])
