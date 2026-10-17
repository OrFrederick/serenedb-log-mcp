"""Access control per Role (T22) on a real SereneDB: per-Role views, LOGIN roles and GRANTs.

Every test connects as the LOGIN roles that sql/access/roles.sql created for this session (see
conftest.py). What is shown:

- each login reads its own views, and is refused the canonical tables and indexes, the raw loader
  tables and every other Role's views; the Manager's views hold no message;
- with DATABASE_URL_<ROLE> set, every tool runs as each Role through that Role's login and views;
  the Manager's aggregation tools work and give the Engineer's numbers, and the calls that need Log
  Entry text tell the Manager what to use instead;
- a server pinned with --role answers only as that Role, through that Role's views;
- `make derive` drops the views (they would block it), and `make access` restores them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from access_setup import Access, apply
from aggregation_tools import call, call_error
from mcp import Client
from psycopg import sql

import access
import derive
from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import Role, load_settings
from serenedb_log_mcp.corpus import facts
from serenedb_log_mcp.db import get_database
from serenedb_log_mcp.server import create_server

pytestmark = pytest.mark.db

DERIVED = ("bgl_entry", "os_entry", "ownership", "pattern", "pattern_dict", "os_instance", "incident")
INDEXES = ("bgl_entry_idx", "os_entry_idx")
NO_TEXT = "no Log Entry text"


def connect(dsn: str) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn, autocommit=True, connect_timeout=10)


def refusal(conn: psycopg.Connection[Any], statement: sql.Composable) -> str | None:
    """The error SereneDB raised for `statement`, or None when it ran."""
    try:
        conn.execute(statement).fetchall()
    except psycopg.Error as exc:
        return f"{type(exc).__name__}: {str(exc).strip().splitlines()[0]}"
    return None


def count(relation: sql.Identifier) -> sql.Composed:
    return sql.SQL("SELECT count(*) FROM {}").format(relation)


# ----------------------------------------------------------------- what each login may read


@pytest.mark.parametrize("role", list(Role))
def test_each_login_reads_its_own_views(granted: Access, role: Role) -> None:
    with connect(granted.dsns[role]) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (granted.users[role],)
        for view in DERIVED:
            assert refusal(conn, count(sql.Identifier(granted.schema(role), view))) is None, view


def test_the_manager_login_cannot_read_the_canonical_tables_or_indexes(granted: Access) -> None:
    with connect(granted.dsns[Role.MANAGER]) as conn:
        for table in DERIVED:
            error = refusal(conn, count(sql.Identifier(granted.slm, table)))
            assert error is not None
            assert "InsufficientPrivilege: permission denied" in error, error
        for index in INDEXES:
            error = refusal(
                conn,
                sql.SQL("SELECT count(*) FROM {} WHERE message @@ 'error'").format(
                    sql.Identifier(granted.slm, index)
                ),
            )
            assert error is not None
            assert "InsufficientPrivilege: permission denied" in error, error
        for raw in ("bgl", "openstack"):
            error = refusal(conn, count(sql.Identifier(granted.raw, raw)))
            assert error is not None
            assert "InsufficientPrivilege" in error, error


def test_the_manager_login_cannot_read_the_other_roles_views(granted: Access) -> None:
    with connect(granted.dsns[Role.MANAGER]) as conn:
        for other in (Role.ENGINEER, Role.TEAM_LEAD):
            for view in DERIVED:
                error = refusal(conn, count(sql.Identifier(granted.schema(other), view)))
                assert error is not None
                assert "InsufficientPrivilege: permission denied" in error, error
            error = refusal(
                conn,
                sql.SQL("SELECT count(*) FROM {} WHERE message @@ 'error'").format(
                    sql.Identifier(granted.schema(other), "bgl_entry_idx")
                ),
            )
            assert error is not None
            assert "InsufficientPrivilege" in error, error


def test_the_managers_views_hold_no_message(granted: Access) -> None:
    own = granted.schema(Role.MANAGER)
    with connect(granted.dsns[Role.MANAGER]) as conn:
        for view in ("bgl_entry", "os_entry", "pattern_dict"):
            error = refusal(conn, sql.SQL("SELECT message FROM {} LIMIT 1").format(sql.Identifier(own, view)))
            assert error is not None
            assert "message" in error, error
            assert "message" not in [
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s",
                    [own, view],
                ).fetchall()
            ]
    with connect(granted.admin_dsn) as conn:
        own_index = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = 'i'",
            [own],
        ).fetchone()
    assert own_index == (0,)


@pytest.mark.parametrize(
    ("role", "other"), [(Role.ENGINEER, Role.TEAM_LEAD), (Role.TEAM_LEAD, Role.ENGINEER)]
)
def test_the_text_roles_read_only_their_own_views_and_index(granted: Access, role: Role, other: Role) -> None:
    with connect(granted.dsns[role]) as conn:
        own_index = sql.Identifier(granted.schema(role), "bgl_entry_idx")
        found = conn.execute(
            sql.SQL(
                "SELECT e.entry_id, BM25(e.tableoid) FROM {} AS e WHERE e.message @@ 'error' "
                "ORDER BY BM25(e.tableoid) DESC LIMIT 1"
            ).format(own_index)
        ).fetchall()
        assert len(found) == 1
        assert found[0][1] > 0
        for denied in (
            count(sql.Identifier(granted.slm, "bgl_entry")),
            count(sql.Identifier(granted.schema(other), "bgl_entry")),
            count(sql.Identifier(granted.schema(Role.MANAGER), "bgl_entry")),
        ):
            error = refusal(conn, denied)
            assert error is not None
            assert "InsufficientPrivilege" in error, error


def test_no_login_may_create_anything(granted: Access) -> None:
    for role in Role:
        with connect(granted.dsns[role]) as conn:
            for statement in (
                sql.SQL("CREATE TABLE {} (x INTEGER)").format(
                    sql.Identifier(granted.schema(role), "t22_probe")
                ),
                sql.SQL("CREATE VIEW {} AS SELECT 1 AS x").format(sql.Identifier(granted.slm, "t22_probe")),
            ):
                error = refusal(conn, statement)
                assert error is not None
                assert "InsufficientPrivilege" in error, error


def test_make_access_check_passes(granted: Access, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {"DATABASE_URL": granted.admin_dsn, **granted.env}.items():
        monkeypatch.setenv(name, value)

    assert access.main(["--check", "--schema", granted.slm]) == 0
    outcomes = access.check(granted.slm, access.logins(load_settings()))
    assert outcomes
    assert all(outcome.as_expected for outcome in outcomes)
    assert {outcome.probe.role for outcome in outcomes} == set(Role)


# ----------------------------------------------------------------- the tools through each login


@pytest.fixture
def as_logins(granted: Access, monkeypatch: pytest.MonkeyPatch) -> Access:
    """The process settings with DATABASE_URL_<ROLE> set: each Role's calls use its login."""
    settings = load_settings({"DATABASE_URL": granted.admin_dsn, **granted.env}, slm_schema=granted.slm)
    monkeypatch.setattr(config, "_active", settings)
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.DEFAULT_ROLE_HOOK)
    facts.clear_cache()
    return granted


def an_entry_id(granted: Access) -> int:
    with connect(granted.admin_dsn) as conn:
        row = conn.execute(
            sql.SQL("SELECT min(entry_id) FROM {} WHERE node LIKE 'R%%'").format(
                sql.Identifier(granted.slm, "bgl_entry")
            )
        ).fetchone()
    assert row is not None
    return int(row[0])


def _evidence(role: Role, _: Access) -> dict[str, Any]:
    produced = call("count_by", role=role.value, group_by="component", dataset="openstack")
    return {"evidence_id": produced["evidence_id"]}


def a_pattern_key(granted: Access) -> str:
    """The BGL Pattern key with the most Incidents in the derived samples."""
    with connect(granted.admin_dsn) as conn:
        row = conn.execute(
            sql.SQL(
                "SELECT pattern_key FROM {} WHERE dataset = 'bgl' "
                "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"
            ).format(sql.Identifier(granted.slm, "incident"))
        ).fetchone()
    assert row is not None
    return str(row[0])


CallSpec = Callable[[Role, Access], dict[str, Any]]
TOOL_CALLS: dict[str, CallSpec] = {
    "describe_corpus": lambda role, granted: {},
    "search_logs": lambda role, granted: {"dataset": "bgl", "query": "error"},
    "get_context": lambda role, granted: {
        "dataset": "bgl",
        "entry_id": an_entry_id(granted),
        "before": 2,
        "after": 2,
    },
    "count_by": lambda role, granted: {"group_by": "component"},
    "trend": lambda role, granted: {"dataset": "bgl", "bucket": "week", "group_by": "alert_category"},
    "latency_stats": lambda role, granted: {"metric": "api"},
    "get_evidence": _evidence,
    "get_role_brief": lambda role, granted: {},
    "list_incidents": lambda role, granted: {"dataset": "bgl"},
    "find_precursors": lambda role, granted: {"dataset": "bgl", "target_pattern": a_pattern_key(granted)},
}
"""One call per registered tool, run as every Role. A new tool must add its call here, so it is
checked through each Role's login and views."""
NEEDS_TEXT = {"search_logs", "get_context"}
"""The tools that return Log Entry text; the Manager's views do not hold it."""


def registered_tools() -> set[str]:
    import anyio

    async def names() -> set[str]:
        async with Client(create_server(), mode="legacy") as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    return anyio.run(names)


def test_every_registered_tool_has_a_call_here() -> None:
    assert registered_tools() == set(TOOL_CALLS), (
        "add the new tool to TOOL_CALLS so it is checked through each Role's login and views"
    )


@pytest.mark.parametrize("tool", sorted(TOOL_CALLS))
@pytest.mark.parametrize("role", list(Role))
def test_every_tool_runs_as_each_role_through_its_login(as_logins: Access, role: Role, tool: str) -> None:
    arguments = {"role": role.value, **TOOL_CALLS[tool](role, as_logins)}

    if role is Role.MANAGER and tool in NEEDS_TEXT:
        error = call_error(tool, **arguments)
        assert NO_TEXT in error
        assert "count_by" in error
        return
    result = call(tool, **arguments)
    assert result["role"] == role.value
    statements = "\n".join(result.get("sql", []))
    if statements:
        # Every statement read this Role's views, never the canonical tables.
        assert f'"{as_logins.schema(role)}".' in statements
        assert f'"{as_logins.slm}".' not in statements


def test_the_manager_cannot_filter_counts_by_text(as_logins: Access) -> None:
    error = call_error("count_by", role="manager", group_by="component", query="error")

    assert NO_TEXT in error


def test_each_role_counts_the_same_log_entries_through_its_own_views(as_logins: Access) -> None:
    by_role = {
        role: call("count_by", role=role.value, group_by="alert_category", dataset="bgl", top=50)
        for role in Role
    }

    rows = {role: result["rows"] for role, result in by_role.items()}
    assert rows[Role.ENGINEER] == rows[Role.TEAM_LEAD] == rows[Role.MANAGER]
    assert {result["total_matches"] for result in by_role.values()} == {
        by_role[Role.ENGINEER]["total_matches"]
    }


def test_the_text_roles_find_what_the_canonical_index_finds(
    as_logins: Access, monkeypatch: pytest.MonkeyPatch
) -> None:
    through_views = {
        role: call("search_logs", role=role.value, dataset="bgl", query="error", sort="relevance")
        for role in (Role.ENGINEER, Role.TEAM_LEAD)
    }
    monkeypatch.setattr(
        config, "_active", load_settings({"DATABASE_URL": as_logins.admin_dsn}, slm_schema=as_logins.slm)
    )
    facts.clear_cache()
    canonical = call("search_logs", role="engineer", dataset="bgl", query="error", sort="relevance")

    assert canonical["total_matches"] > 0
    for result in through_views.values():
        assert result["total_matches"] == canonical["total_matches"]
        assert result["summary"] == canonical["summary"]


@pytest.mark.parametrize("role", list(Role))
def test_the_database_layer_connects_as_the_roles_login(as_logins: Access, role: Role) -> None:
    result = get_database(role).fetch_all("SELECT current_user")

    assert result.rows == [(as_logins.users[role],)]


# ----------------------------------------------------------------- the --role pin (T3) with logins


@pytest.fixture
def pinned_to_manager(as_logins: Access, monkeypatch: pytest.MonkeyPatch) -> Access:
    """A server started with --role manager and per-Role DSNs (cli.main does the same)."""
    settings = load_settings(
        {"DATABASE_URL": as_logins.admin_dsn, **as_logins.env}, slm_schema=as_logins.slm, role="manager"
    )
    monkeypatch.setattr(config, "_active", settings)
    return as_logins


def test_a_server_pinned_to_the_manager_answers_only_as_the_manager(pinned_to_manager: Access) -> None:
    error = call_error("count_by", role="engineer", group_by="component")
    assert "--role manager" in error

    result = call("count_by", role="manager", group_by="component")
    assert result["role"] == "manager"
    assert f'"{pinned_to_manager.schema(Role.MANAGER)}".' in "\n".join(result["sql"])


def test_a_server_pinned_to_the_manager_cannot_return_log_entries(pinned_to_manager: Access) -> None:
    assert "--role manager" in call_error("search_logs", role="engineer", dataset="bgl", query="error")
    assert NO_TEXT in call_error("search_logs", role="manager", dataset="bgl", query="error")


# ----------------------------------------------------------------- derive and access together


def test_derive_drops_the_role_views_and_make_access_restores_them(granted: Access) -> None:
    # A view blocks CREATE OR REPLACE TABLE on its source, so derive drops the per-Role views first.
    try:
        assert (
            derive.main(["--schema", granted.slm, "--raw-schema", granted.raw, "--only", "60_os_instance"])
            == 0
        )
        with connect(granted.admin_dsn) as conn:
            left = conn.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(%s::VARCHAR[])",
                [[granted.schema(role) for role in Role]],
            ).fetchall()
        assert left == []
    finally:
        apply(granted)
    with connect(granted.dsns[Role.MANAGER]) as conn:
        assert refusal(conn, count(sql.Identifier(granted.schema(Role.MANAGER), "os_instance"))) is None
