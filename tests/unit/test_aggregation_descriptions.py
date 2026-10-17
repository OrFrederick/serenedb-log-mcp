"""The aggregation tools' descriptions carry the rules the agent must follow (T6 acceptance)."""

from __future__ import annotations

import anyio
import pytest
from mcp import Client

from serenedb_log_mcp.server import create_server


def descriptions() -> dict[str, str]:
    async def listing() -> dict[str, str]:
        async with Client(create_server(), mode="legacy") as client:
            return {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}

    return anyio.run(listing)


@pytest.mark.parametrize("tool", ["count_by", "trend"])
def test_the_description_says_quote_exact_numbers_and_log_entries_are_not_incidents(tool: str) -> None:
    text = descriptions()[tool]

    assert "Quote exact numbers, never round" in text
    assert "Counts are Log Entries, not Incidents" in text
    assert "Outputs:" in text


def test_the_trend_description_says_when_no_handoff_note_appears() -> None:
    text = " ".join(descriptions()["trend"].split())

    assert "until Pattern Families are assigned in the database, no such note appears" in text
