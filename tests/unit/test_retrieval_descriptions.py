"""The retrieval tools' descriptions follow the T5 rule: purpose, inputs and outputs in 3 to 6
sentences, plus when to use another tool instead."""

from __future__ import annotations

import re

import anyio
import pytest
from mcp import Client

from serenedb_log_mcp.server import create_server

RETRIEVAL_TOOLS = ("describe_corpus", "search_logs", "get_context")


def descriptions() -> dict[str, str]:
    async def listing() -> dict[str, str]:
        async with Client(create_server(), mode="legacy") as client:
            return {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}

    return anyio.run(listing)


def sentences(text: str) -> int:
    """Sentence ends: '.', '!' or '?' followed by whitespace or the end of the text."""
    return len(re.findall(r"[.!?](?=\s|$)", " ".join(text.split())))


@pytest.mark.parametrize("tool", RETRIEVAL_TOOLS)
def test_the_description_has_3_to_6_sentences_with_inputs_and_outputs(tool: str) -> None:
    text = descriptions()[tool]

    assert 3 <= sentences(text) <= 6, text
    assert "Inputs:" in text
    assert "Outputs:" in text


@pytest.mark.parametrize(
    ("tool", "other"),
    [("describe_corpus", "count_by"), ("search_logs", "count_by"), ("get_context", "count_by")],
)
def test_the_description_names_the_tool_to_use_instead(tool: str, other: str) -> None:
    assert other in descriptions()[tool]


def test_search_logs_says_it_does_not_answer_how_often() -> None:
    assert 'does not answer "how often"' in descriptions()["search_logs"]


def test_describe_corpus_says_the_counts_are_log_entries_not_incidents() -> None:
    assert "Log Entries, not Incidents" in descriptions()["describe_corpus"]


@pytest.mark.parametrize("tool", RETRIEVAL_TOOLS)
def test_the_tool_is_read_only_and_always_loaded(tool: str) -> None:
    async def listing() -> dict[str, object]:
        async with Client(create_server(), mode="legacy") as client:
            found = {t.name: t for t in (await client.list_tools()).tools}[tool]
            return {
                "read_only": found.annotations.read_only_hint if found.annotations else None,
                "always_load": (found.meta or {}).get("anthropic/alwaysLoad"),
                "role_required": "role" in (found.input_schema.get("required") or []),
            }

    assert anyio.run(listing) == {"read_only": True, "always_load": True, "role_required": True}


def input_schema(tool: str) -> dict[str, object]:
    async def listing() -> dict[str, object]:
        async with Client(create_server(), mode="legacy") as client:
            return {t.name: t.input_schema for t in (await client.list_tools()).tools}[tool]

    return anyio.run(listing)


def test_the_query_description_says_and_is_not_an_operator() -> None:
    properties = input_schema("search_logs")["properties"]
    assert isinstance(properties, dict)

    assert "AND is not an operator; all words are already required." in properties["query"]["description"]


@pytest.mark.parametrize(
    "key",
    [
        "anchor_entry_id",
        "anchor_ts",
        "scope",
        "scope_value",
        "window_s",
        "entries_in_scope",
        "before_returned",
        "after_returned",
        "first_ts",
        "last_ts",
    ],
)
def test_the_get_context_description_names_every_summary_key(key: str) -> None:
    assert re.search(rf"\b{key}\b", descriptions()["get_context"])
