"""Two server processes on one LEDGER_PATH: unique, gap-free ids and shared Evidence.

This is the real thing, not two threads: each client starts its own `python tests/support/sample_tool.py`
over stdio, both with the same `LEDGER_PATH`. The calls run concurrently, so the ids are only
unique and gap-free if the last id really is re-read inside the file lock.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
import sample_tool
from mcp import Client, StdioServerParameters
from mcp_types import CallToolResult

SERVER_SCRIPT = Path(sample_tool.__file__)
CALLS_PER_PROCESS = 5


def server_parameters(ledger_path: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONUNBUFFERED": "1",
            "LEDGER_PATH": str(ledger_path),
        },
    )


def envelope(result: CallToolResult | Any) -> dict[str, Any]:
    assert isinstance(result, CallToolResult), result
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def shared_ledger(tmp_path: Path) -> Path:
    return tmp_path / "shared-ledger.jsonl"


def test_two_processes_share_one_gap_free_evidence_ledger(shared_ledger: Path) -> None:
    async def two_servers() -> tuple[list[str], list[str], dict[str, Any]]:
        parameters = server_parameters(shared_ledger)
        async with (
            Client(parameters) as first,
            Client(parameters) as second,
        ):
            by_process: dict[str, list[str]] = {"first": [], "second": []}

            async def keep_calling(label: str, client: Client, role: str) -> None:
                for _ in range(CALLS_PER_PROCESS):
                    result = await client.call_tool("sample_count", {"role": role})
                    by_process[label].append(envelope(result)["evidence_id"])

            # Both processes write at the same time, which is what the file lock is for.
            async with anyio.create_task_group() as calls:
                calls.start_soon(keep_calling, "first", first, "engineer")
                calls.start_soon(keep_calling, "second", second, "team_lead")

            # The second process fetches Evidence the first one produced.
            handed_over = by_process["first"][0]
            fetched = await second.call_tool("get_evidence", {"role": "manager", "evidence_id": handed_over})
            return by_process["first"], by_process["second"], envelope(fetched)

    first_ids, second_ids, fetched = anyio.run(two_servers)

    assigned = first_ids + second_ids
    assert len(assigned) == 2 * CALLS_PER_PROCESS
    assert len(set(assigned)) == len(assigned), f"ids were handed out twice: {sorted(assigned)}"
    assert sorted(assigned, key=lambda id_: int(id_[1:])) == [
        f"E{number}" for number in range(1, 2 * CALLS_PER_PROCESS + 1)
    ], f"the ids have gaps: {sorted(assigned)}"

    # The second process can read what the first one wrote, including its rows and its SQL.
    assert fetched["evidence_id"] == first_ids[0]
    assert fetched["role"] == "engineer"
    assert fetched["rows"] == sample_tool.SAMPLE_ROWS
    assert fetched["sql"] == [sample_tool.SAMPLE_SQL]
    assert any("manager" in note for note in fetched["notes"])

    written = [json.loads(line) for line in shared_ledger.read_text().splitlines() if line.strip()]
    assert [record["evidence_id"] for record in written] == [
        f"E{number}" for number in range(1, 2 * CALLS_PER_PROCESS + 1)
    ]
    assert {record["role"] for record in written} == {"engineer", "team_lead"}
    # Two server processes, not two threads: the records carry two different process ids.
    assert len({record["summary"]["server_pid"] for record in written}) == 2


def test_a_later_process_continues_the_numbering(shared_ledger: Path) -> None:
    async def one_call(role: str) -> str:
        async with Client(server_parameters(shared_ledger)) as client:
            result = await client.call_tool("sample_count", {"role": role})
            return str(envelope(result)["evidence_id"])

    first = anyio.run(one_call, "engineer")
    second = anyio.run(one_call, "team_lead")

    assert [first, second] == ["E1", "E2"]
