"""Claude can connect and get answers: the `.mcp.json` command answers from the database.

The command Claude Code runs for this server (tests/contract/test_stdio_connect.py checks its
listing) is started as its own process with this environment (DATABASE_URL, SLM_SCHEMA) and asked
what every investigation starts with: describe_corpus, then count_by over the whole Base Corpus by
Dataset. It reads the default SLM_SCHEMA, which `make load-sample` and `make derive` fill from a clean
checkout (the CI workflow runs exactly those); on a dev instance it is usually the full Base Corpus,
read-only.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

import anyio
import psycopg
import pytest
from contract_session import mcp_json_parameters
from mcp import Client
from mcp_types import CallToolResult

from serenedb_log_mcp.config import load_settings

CANONICAL = ("bgl_entry", "os_entry", "ownership")


def require_derived_schema(database_url: str) -> str:
    """The default SLM_SCHEMA once `make derive` built it; skips (fails with SLM_REQUIRE_DB) otherwise."""
    schema = load_settings().slm_schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name IN ('bgl_entry', 'os_entry', 'ownership')",
            [schema],
        ).fetchone()
    if not row or row[0] < len(CANONICAL):
        problem = f"schema {schema!r} has no canonical tables; run `make load-sample` and `make derive`"
        if os.environ.get("SLM_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}:
            pytest.fail(f"SLM_REQUIRE_DB is set, so this does not skip: {problem}", pytrace=False)
        pytest.skip(problem)
    return schema


def structured(result: Any) -> dict[str, Any]:
    assert isinstance(result, CallToolResult), result
    assert result.is_error is False, result.content[0].text if result.content else result
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_the_mcp_json_command_answers_from_the_database(database_url: str) -> None:
    require_derived_schema(database_url)

    async def investigation() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(mcp_json_parameters()) as client:
            described = structured(await client.call_tool("describe_corpus", {"role": "engineer"}))
            counted = structured(
                await client.call_tool("count_by", {"role": "manager", "group_by": "dataset"})
            )
            return described, counted

    described, counted = anyio.run(investigation)

    assert described["evidence_id"] == "E1"
    assert counted["evidence_id"] == "E2"
    datasets = described["summary"]["datasets"]
    assert set(datasets) == {"bgl", "openstack"}
    assert all(datasets[name]["entries"] > 0 for name in datasets)
    assert counted["data_scope"]["datasets"] == ["bgl", "openstack"]
    assert {row[0] for row in counted["rows"]} == {"bgl", "openstack"}
    assert counted["summary"]["total"] == described["summary"]["entries_total"]
    assert counted["sql"]
    assert counted["truncated"] is False
