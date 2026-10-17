"""`create_index` and `drop_index` end to end through MCP on SereneDB (T19), in a scratch schema.

The synthetic corpus of tests/support/aggregation_corpus.py has 2 "steady kernel message" Log
Entries every day on NODE_A, so a count over one day inside the slice has a whole-Dataset baseline
(median_week 14) that differs from what the slice alone would give (0). Each call that an agent
index answers must return exactly what the same call returns from the full index.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from aggregation_corpus import NODE_A
from aggregation_tools import call, call_error, independent, row_dicts
from psycopg import sql

from serenedb_log_mcp import index_admin
from serenedb_log_mcp.corpus import agent_indexes

pytestmark = pytest.mark.db

SLICE = {"dataset": "bgl", "time_from": "2005-07-18", "time_to": "2005-07-25"}
INSIDE = {"time_from": "2005-07-20", "time_to": "2005-07-21"}
QUERY = "steady kernel message"


def count_by(**scope: str) -> dict[str, Any]:
    return call("count_by", role="engineer", group_by="node", dataset="bgl", query=QUERY, **scope)


def trend(**scope: str) -> dict[str, Any]:
    return call("trend", role="engineer", dataset="bgl", bucket="hour", query=QUERY, **scope)


def agent_index_rows(database_url: str, schema: str) -> list[tuple[str, str | None, str | None]]:
    """(name, indexdef, comment) of every index in `schema` whose name starts with agent_idx_.

    indexdef is NULL for an inverted index on SereneDB 26.09.1.3.
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT i.indexname, i.indexdef, d.description FROM pg_indexes AS i "
                "JOIN pg_namespace AS n ON n.nspname = i.schemaname "
                "JOIN pg_class AS c ON c.relnamespace = n.oid AND c.relname = i.indexname "
                "LEFT JOIN pg_description AS d ON d.objoid = c.oid "
                "WHERE i.schemaname = %s AND i.indexname LIKE 'agent_idx_%%' ORDER BY 1",
                [schema],
            ).fetchall()
        ]


def without_routing_notes(evidence: dict[str, Any]) -> list[str]:
    return [note for note in evidence["notes"] if "agent index" not in note]


def test_create_query_and_drop_an_agent_index(corpus: str, database_url: str) -> None:
    before_count, before_trend = count_by(**INSIDE), trend(**INSIDE)
    assert before_count["data_scope"]["agent_indexes"] is None

    created = call("create_index", role="engineer", **SLICE)

    row = dict(zip(created["columns"], created["rows"][0], strict=True))
    name = row["index"]
    [(slice_rows,)] = independent(corpus, "SELECT count(*) FROM {bgl} WHERE ts >= %s AND ts < %s", [
        "2005-07-18", "2005-07-25"])  # fmt: skip
    assert row["rows"] == slice_rows == 115  # 14 steady, 100 storm, 1 APPSEV
    assert row["status"] == "built"
    assert row["build_ms"] > 0
    assert agent_indexes.is_agent_name(name)
    [(found, definition, comment)] = agent_index_rows(database_url, corpus)
    assert found == name
    # SereneDB 26.10.1 prints the definition; 26.09.1.3 (the arm64 dev image) leaves indexdef NULL
    # for an inverted index (PR #59 review). The name, the marker and the slice count below hold on
    # both, so only the definition check depends on the version.
    if definition is not None:
        assert "USING inverted" in definition
        assert "WHERE" in definition
    assert comment is not None
    assert comment.startswith(agent_indexes.MARKER)
    assert any("CREATE INDEX" in statement for statement in created["sql"])

    # Inside the slice: the agent index answers, and every number is what the full index gave.
    routed_count, routed_trend = count_by(**INSIDE), trend(**INSIDE)
    for routed, before in ((routed_count, before_count), (routed_trend, before_trend)):
        assert routed["data_scope"]["agent_indexes"] == {"bgl": f"{corpus}.{name}"}
        assert any(f"{corpus}.{name}" in note for note in routed["notes"])
        assert routed["rows"] == before["rows"]
        assert routed["summary"] == before["summary"]
        assert without_routing_notes(routed) == without_routing_notes(before)
    nodes = row_dicts(routed_count)
    assert [(r["node"], r["count"], r["median_week"]) for r in nodes] == [(NODE_A, 2, 14)]
    # The ranking read the agent index; the whole-Dataset baselines still read the full index.
    assert f'"{name}"' in routed_count["sql"][0]
    assert '"bgl_entry_idx"' in routed_count["sql"][1]

    # A Data Scope that reaches outside the slice keeps reading the full index.
    outside = count_by(time_from="2005-07-01", time_to="2005-07-21")
    assert outside["data_scope"]["agent_indexes"] is None

    dropped = call("drop_index", role="engineer", name=name)

    assert dict(zip(dropped["columns"], dropped["rows"][0], strict=True))["status"] == "dropped"
    assert agent_index_rows(database_url, corpus) == []
    assert agent_indexes.registered_all() == []
    after = count_by(**INSIDE)
    assert after["data_scope"]["agent_indexes"] is None
    assert after["rows"] == before_count["rows"]


def test_one_agent_index_per_dataset_and_the_same_slice_is_reused(corpus: str) -> None:
    first = call("create_index", role="team_lead", **SLICE)
    name = first["rows"][0][0]

    again = call("create_index", role="team_lead", **SLICE)
    other = call_error(
        "create_index", role="team_lead", dataset="bgl", time_from="2005-06-06", time_to="2005-06-13"
    )

    assert again["rows"][0][0] == name
    assert again["summary"]["status"] == "reused"
    assert again["summary"]["build_ms"] == 0
    assert name in other
    assert "drop_index" in other
    call("drop_index", role="team_lead", name=name)


def test_drop_index_refuses_every_index_create_index_did_not_build(corpus: str, database_url: str) -> None:
    assert "only drops the agent indexes" in call_error("drop_index", role="engineer", name="bgl_entry_idx")

    impostor = "agent_idx_bgl_00000000"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE INDEX {} ON {} USING inverted (entry_id)").format(
                sql.Identifier(impostor), sql.Identifier(corpus, "bgl_entry")
            )
        )
    message = call_error("drop_index", role="engineer", name=impostor)

    assert "not built by create_index" in message
    assert [row[0] for row in agent_index_rows(database_url, corpus)] == [impostor]
    assert "no agent index" in call_error("drop_index", role="engineer", name="agent_idx_bgl_ffffffff")


def test_a_slice_above_the_cap_builds_nothing(
    corpus: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(index_admin, "MAX_SLICE_ROWS", 100)

    message = call_error("create_index", role="engineer", **SLICE)

    assert "115 Log Entries" in message
    assert "100" in message
    assert agent_index_rows(database_url, corpus) == []
