"""The catalogue-first chain of #7 (plan section 2.3, T14), shared by the db and corpus tests.

Claude reads list_patterns, picks Pattern ids and passes them on: count_by(pattern_ids=[...]),
search_logs(pattern_ids=[...]) and list_incidents(pattern_key=...). Every step goes through the
server's tool registry, so the list_incidents step runs as soon as T27 registers that tool and
skips with the reason until then.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from aggregation_tools import call, row_dicts
from mcp import Client

from serenedb_log_mcp.server import create_server

LIST_INCIDENTS_MISSING = "list_incidents not registered yet: T27 #39"


def tool_names() -> set[str]:
    """The tools the server registers."""

    async def listing() -> set[str]:
        async with Client(create_server(), mode="legacy") as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    return anyio.run(listing)


def catalogue_pages(dataset: str, role: str = "engineer", **arguments: Any) -> list[dict[str, Any]]:
    """Every page of list_patterns for `dataset`, in order."""
    first = call("list_patterns", role=role, dataset=dataset, **arguments)
    pages = [first]
    for page in range(2, first["summary"]["pages_total"] + 1):
        pages.append(call("list_patterns", role=role, dataset=dataset, page=page, **arguments))
    return pages


def whole_catalogue(dataset: str, role: str = "engineer", **arguments: Any) -> list[dict[str, Any]]:
    """The rows of every list_patterns page for `dataset`, in order, as dicts."""
    return [row for page in catalogue_pages(dataset, role, **arguments) for row in row_dicts(page)]


@dataclass(frozen=True)
class ChainTotals:
    """The Log Entry total each step of the chain reports for one Pattern."""

    catalogue: int
    count_by: int
    search_logs: int


def chain_totals(dataset: str, template_id: str, role: str = "team_lead") -> ChainTotals:
    """list_patterns -> count_by -> search_logs for one Pattern id, as Claude would chain them."""
    looked_up = row_dicts(call("list_patterns", role=role, dataset=dataset, pattern_ids=[template_id]))
    assert [row["template_id"] for row in looked_up] == [template_id]
    counted = call("count_by", role=role, group_by="pattern", dataset=dataset, pattern_ids=[template_id])
    groups = row_dicts(counted)
    assert [(row["pattern"], row["count"]) for row in groups] == [(template_id, counted["summary"]["total"])]
    found = call("search_logs", role=role, dataset=dataset, pattern_ids=[template_id], limit=5)
    assert found["returned"] == min(5, found["summary"]["entries_total"])
    assert {row[found["columns"].index("template_id")] for row in found["rows"]} == {template_id}
    return ChainTotals(
        catalogue=looked_up[0]["entries"],
        count_by=counted["summary"]["total"],
        search_logs=found["summary"]["entries_total"],
    )


def incidents_entries_total(dataset: str, template_id: str, role: str = "team_lead") -> int:
    """The list_incidents step: entries_total of the Incidents of one Pattern; skips before T27."""
    if "list_incidents" not in tool_names():
        pytest.skip(LIST_INCIDENTS_MISSING)
    result = call("list_incidents", role=role, dataset=dataset, pattern_key=template_id)
    total: int = result["summary"]["entries_total"]
    return total
