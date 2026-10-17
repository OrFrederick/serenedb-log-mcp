"""`latency_stats`: the parts that need no database (argument resolution, endpoints, registration)."""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META
from serenedb_log_mcp.tools.latency_stats import (
    MAX_GROUPS,
    View,
    canonical_endpoint,
    resolve_view,
)


def tool_listing() -> Any:
    tools = {tool.name: tool for tool in anyio.run(create_server().list_tools)}
    return tools["latency_stats"]


def call_error(**arguments: Any) -> str:
    """The ToolError text of a call that must be refused before it reaches the database."""
    with pytest.raises(ToolError) as raised:
        anyio.run(lambda: create_server().call_tool("latency_stats", arguments))
    return str(raised.value)


# ----------------------------------------------------------------- the view a call asks for


@pytest.mark.parametrize(
    ("metric", "group_by", "bucket", "expected"),
    [
        ("api", None, None, View("api", "endpoint", None)),
        ("spawn", None, None, View("spawn", "instance", None)),
        ("build", None, None, View("build", "instance", None)),
        ("api", None, "5min", View("api", "bucket", "5min")),
        ("spawn", "bucket", None, View("spawn", "bucket", "hour")),
        ("api", "source", None, View("api", "source", None)),
        ("spawn", "none", None, View("spawn", "none", None)),
        ("build", "source", None, View("build", "source", None)),
    ],
)
def test_defaults_follow_the_metric(
    metric: str, group_by: str | None, bucket: str | None, expected: View
) -> None:
    assert resolve_view(metric, group_by, bucket) == expected


@pytest.mark.parametrize(
    ("metric", "group_by", "bucket", "message"),
    [
        ("spawn", "endpoint", None, "endpoint"),
        ("api", "instance", None, "instance"),
        ("api", "endpoint", "hour", "bucket"),
        ("spawn", "instance", "day", "bucket"),
    ],
)
def test_contradicting_arguments_are_refused_with_a_fix(
    metric: str, group_by: str, bucket: str | None, message: str
) -> None:
    with pytest.raises(ScopeError, match=message):
        resolve_view(metric, group_by, bucket)


# ----------------------------------------------------------------- endpoints


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("GET /v2/{tenant}/servers/detail", "GET /v2/{tenant}/servers/detail"),
        ("GET /v2/54fadb412c4e40cdbaed9335e4c35a9e/servers/detail", "GET /v2/{tenant}/servers/detail"),
        (
            "delete /v2/54fadb412c4e40cdbaed9335e4c35a9e/servers/a445709b-6ad0-40ec-8860-bec60b6ca0c2",
            "DELETE /v2/{tenant}/servers/{uuid}",
        ),
        ("GET /v2/54fadb412c4e40cdbaed9335e4c35a9e/flavors/2", "GET /v2/{tenant}/flavors/{n}"),
        ("  POST /v2/{tenant}/servers?x=1  ", "POST /v2/{tenant}/servers"),
        ('"GET /latest/meta-data/ HTTP/1.1"', "GET /latest/meta-data/"),
        ("GET /a/1/2", "GET /a/{n}/{n}"),
    ],
)
def test_endpoints_are_masked_the_way_the_derive_step_masks_them(given: str, expected: str) -> None:
    assert canonical_endpoint(given) == expected


def test_a_path_without_a_method_is_kept_for_the_did_you_mean_check() -> None:
    assert canonical_endpoint("/v2/{tenant}/servers/detail") == "/v2/{tenant}/servers/detail"


# ----------------------------------------------------------------- registration


def test_latency_stats_is_registered_as_a_read_only_evidence_tool() -> None:
    tool = tool_listing()

    assert tool.title
    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.input_schema["required"] == ["role"]
    assert tool.input_schema["properties"]["limit"]["maximum"] == MAX_GROUPS == 50
    assert tool.input_schema["properties"]["metric"]["enum"] == ["api", "spawn", "build"]


def test_the_description_names_every_output_field_and_the_unit_of_each_view() -> None:
    description = tool_listing().description

    for field in ("n", "p50_s", "p95_s", "p99_s", "max_s", "p95_ratio", "p99_ratio", "rise", "flagged"):
        assert f"{field}" in description, field
    for term in ("Latency Degradation", "Phase-Duration Anomaly", "Hypothesis", "median", "MAD"):
        assert term in description, term
    assert "abnormal" not in description.lower()


def test_pattern_filters_on_instance_metrics_are_refused_before_any_query() -> None:
    message = call_error(role="engineer", metric="spawn", pattern_ids=["0123456789abcdef"])

    assert "metric='api'" in message


def test_baseline_period_needs_bucketed_latency() -> None:
    message = call_error(
        role="engineer", metric="api", baseline_from="2017-05-16T00:00:00", baseline_to="2017-05-16T06:00:00"
    )

    assert "group_by='bucket'" in message


def test_a_relative_baseline_is_refused() -> None:
    message = call_error(role="engineer", metric="api", group_by="bucket", baseline_from="last 2h")

    assert "baseline_from" in message
