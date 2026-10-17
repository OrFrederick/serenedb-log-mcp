"""The Evidence contract over MCP, through an in-process client.

What a client actually receives: the published input and output schemas, `structuredContent` that
validates against the published output schema, a text block that mirrors it, and an error result
that carries the actionable text (on an error only `content[0].text` reaches the model).
"""

import json
from typing import Any

import anyio
import jsonschema
import pytest
from mcp import Client
from mcp_types import CallToolResult, ListToolsResult
from sample_tool import SAMPLE_ROWS, build_sample_server

from serenedb_log_mcp.evidence import Evidence


def tools() -> dict[str, Any]:
    async def listing() -> ListToolsResult:
        async with Client(build_sample_server(), mode="legacy") as client:
            return await client.list_tools()

    return {tool.name: tool for tool in anyio.run(listing).tools}


def call(name: str, arguments: dict[str, Any]) -> tuple[CallToolResult, dict[str, Any]]:
    """Call one tool through a client and return the result with the tool's published schema."""

    async def one_call() -> tuple[CallToolResult, dict[str, Any]]:
        async with Client(build_sample_server(), mode="legacy") as client:
            listing = await client.list_tools()
            schema = next(tool.output_schema for tool in listing.tools if tool.name == name)
            result = await client.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            assert schema is not None
            return result, schema

    return anyio.run(one_call)


@pytest.mark.parametrize("name", ["sample_count", "get_evidence"])
def test_every_tool_publishes_the_evidence_envelope_as_its_output_schema(name: str) -> None:
    tool = tools()[name]

    assert tool.output_schema is not None
    published = set(tool.output_schema["properties"])
    assert published == set(Evidence.model_fields)
    assert "Cite it as [E3]" in tool.output_schema["properties"]["evidence_id"]["description"]
    # The agent never sees the output schema, so the description must name the output fields.
    assert tool.description is not None
    assert "Outputs:" in tool.description


def test_every_tool_requires_a_role_from_the_three_role_enum() -> None:
    for name, tool in tools().items():
        schema = tool.input_schema
        assert "role" in schema["required"], name
        reference = schema["properties"]["role"]["$ref"].rsplit("/", 1)[-1]
        assert schema["$defs"][reference]["enum"] == ["engineer", "team_lead", "manager"], name


def test_structured_content_validates_against_the_published_output_schema() -> None:
    result, schema = call("sample_count", {"role": "team_lead"})

    jsonschema.validate(result.structured_content, schema)
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["evidence_id"] == "E1"
    assert result.structured_content["rows"] == SAMPLE_ROWS


def test_the_single_text_block_mirrors_the_structured_content() -> None:
    result, _ = call("sample_count", {"role": "engineer"})

    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert json.loads(result.content[0].text) == result.structured_content


def test_an_expected_failure_arrives_as_an_error_result_with_the_actionable_text() -> None:
    result, _ = call("sample_count", {"role": "engineer", "fail": True})

    assert result.is_error is True
    assert result.structured_content is None
    assert len(result.content) == 1
    text = result.content[0].text
    assert "Widen the time range" in text
    assert "describe_corpus" in text


def test_an_unknown_evidence_id_arrives_as_an_error_result_that_names_the_range() -> None:
    result, _ = call("get_evidence", {"role": "manager", "evidence_id": "E42"})

    assert result.is_error is True
    assert "Evidence Ledger is empty" in result.content[0].text


def test_a_role_outside_the_enum_is_rejected_by_the_input_schema() -> None:
    result, _ = call("sample_count", {"role": "ceo"})

    assert result.is_error is True
    assert "'engineer', 'team_lead' or 'manager'" in result.content[0].text


def test_an_investigation_can_be_replayed_from_the_ledger_in_one_session() -> None:
    async def investigation() -> list[dict[str, Any]]:
        async with Client(build_sample_server(), mode="legacy") as client:
            first = await client.call_tool("sample_count", {"role": "engineer", "groups": 1})
            second = await client.call_tool("sample_count", {"role": "team_lead"})
            assert isinstance(first, CallToolResult)
            assert isinstance(second, CallToolResult)
            again = await client.call_tool("get_evidence", {"role": "manager", "evidence_id": "[E1]"})
            assert isinstance(again, CallToolResult)
            return [
                result.structured_content
                for result in (first, second, again)
                if result.structured_content is not None
            ]

    first, second, again = anyio.run(investigation)

    assert [first["evidence_id"], second["evidence_id"]] == ["E1", "E2"]
    assert first["truncated"] is True
    assert any("1 of 3" in note for note in first["notes"])
    assert again["evidence_id"] == "E1"
    assert again["rows"] == first["rows"]
