"""`index_admin`: building and dropping agent indexes (T19), against a fake database.

The write connection is a fake that records every statement, and the read-only layer is replaced
by a fake `fetch_all` that answers the slice count, the catalog listing and the verification count.
The real statements run in tests/db/test_agent_index_db.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp import clock, index_admin
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex
from serenedb_log_mcp.corpus.datasets import BGL_COLUMNS, relation
from serenedb_log_mcp.db import QueryResult
from serenedb_log_mcp.scope import ScopeError

SLM = load_settings({})
START, END = datetime(2005, 8, 22), datetime(2005, 9, 19)
ROWS = 310_868
NAME = "agent_idx_bgl_0a1b2c3d"
WAVE = AgentIndex(schema="slm", name=NAME, dataset="bgl", time_from=START, time_to=END, rows=ROWS)


@dataclass
class FakeCursor:
    rows: list[tuple[Any, ...]] = field(default_factory=list)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


@dataclass
class FakeConnection:
    """A write connection that records statements and fails on the first one matching `fail_on`."""

    fail_on: str | None = None
    lose_connection: bool = False
    executed: list[str] = field(default_factory=list)
    closed: bool = False
    broken: bool = False

    def execute(self, query: str, params: Sequence[Any] | None = None) -> FakeCursor:
        assert isinstance(query, str), "statements must be rendered before they run"
        assert params is None, "DDL takes no bound parameters"
        if self.fail_on is not None and self.fail_on in query:
            self.fail_on = None
            if self.lose_connection:
                self.broken = True
                raise psycopg.OperationalError("server closed the connection unexpectedly")
            raise psycopg.errors.FeatureNotSupported("no such feature")
        self.executed.append(query)
        if query.startswith("EXPLAIN ANALYZE"):
            return FakeCursor([("╭─ Summary ───────────╮",), ("│ Total Time: 0.2391s │",), ("╰──╯",)])
        return FakeCursor()

    def cancel_safe(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeDatabase:
    """The read-only layer: answers by statement shape and records what it was asked."""

    def __init__(self, slice_rows: int = ROWS, catalog: list[tuple[str, str | None, str]] | None = None,
                 verify: Sequence[int | None] = (ROWS,)) -> None:  # fmt: skip
        self.slice_rows = slice_rows
        self.catalog = catalog or []
        self.verify = list(verify)
        self.asked: list[str] = []

    def fetch_all(self, statement: str, params: Sequence[Any] = (), *, role: Any = None) -> QueryResult:
        self.asked.append(statement)
        if "pg_class" in statement:
            rows: list[tuple[Any, ...]] = list(self.catalog)
        elif "agent_idx_" in statement:
            held = self.verify.pop(0) if len(self.verify) > 1 else self.verify[0]
            rows = [] if held is None else [(held,)]
        else:
            rows = [(self.slice_rows,)]
        return QueryResult(len(rows), rows, False, 1.0, [statement], [list(params)])


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    agent_indexes.clear()
    monkeypatch.setattr(index_admin, "new_name", lambda dataset: f"agent_idx_{dataset}_0a1b2c3d")
    yield
    agent_indexes.clear()


def use(
    monkeypatch: pytest.MonkeyPatch, database: FakeDatabase, conn: FakeConnection
) -> list[FakeConnection]:
    """Install the fakes; returns the list of write connections opened."""
    opened: list[FakeConnection] = []

    def connect(dsn: str, *, client_binding: bool) -> FakeConnection:
        opened.append(conn if not opened else FakeConnection())
        return opened[-1]

    monkeypatch.setattr(index_admin, "fetch_all", database.fetch_all)
    monkeypatch.setattr(index_admin, "open_connection", connect)
    return opened


# ----------------------------------------------------------------- the DDL


def test_the_create_statement_indexes_every_canonical_column_over_the_slice_only() -> None:
    text = index_admin.create_statement(relation("bgl", settings=SLM), NAME, START, END).as_string(None)

    assert text.startswith(f'CREATE INDEX "{NAME}" ON "slm"."bgl_entry" USING inverted (')
    assert '"message" "slm"."log_text"' in text
    for column in BGL_COLUMNS:
        assert f'"{column}"' in text
    # BGL logs US Pacific time, so the UTC slice (#74) is cut on epoch_utc, not on the local ts.
    assert text.endswith('WHERE "epoch_utc" >= 1124668800.0 AND "epoch_utc" < 1127088000.0')


def test_the_slice_holds_what_a_utc_data_scope_reads_not_the_local_day() -> None:
    """The routing bug of the #59 review: a slice cut on BGL's local ts missed the Log Entries of a UTC
    Data Scope that fell outside the local day, while the Evidence said the counts matched."""
    utc_scope_start = datetime(2005, 8, 22, 3)  # 2005-08-21 20:00 in US Pacific time
    text = index_admin.create_statement(relation("bgl", settings=SLM), NAME, utc_scope_start, END).as_string(
        None
    )

    assert f'"epoch_utc" >= {clock.utc_bound(relation("bgl", settings=SLM).dataset, utc_scope_start)}' in text
    assert '"ts"' not in text.split("WHERE")[1]


def test_a_dataset_logged_in_utc_is_cut_on_ts() -> None:
    text = index_admin.create_statement(relation("openstack", settings=SLM), NAME, START, END).as_string(None)

    assert "WHERE \"ts\" >= '2005-08-22 00:00:00'::timestamp" in text


# ----------------------------------------------------------------- the slice


def test_an_empty_slice_is_refused_before_anything_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = use(monkeypatch, FakeDatabase(slice_rows=0), FakeConnection())

    with pytest.raises(ScopeError, match="no Log Entries"):
        index_admin.create_agent_index("bgl", START, END, settings=SLM)
    assert opened == []


def test_a_slice_above_the_cap_is_refused_with_its_size(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = use(monkeypatch, FakeDatabase(slice_rows=1_240_932), FakeConnection())

    with pytest.raises(ScopeError) as excinfo:
        index_admin.create_agent_index("bgl", START, END, settings=SLM, max_rows=1_000_000)

    message = str(excinfo.value)
    assert "1,240,932" in message
    assert "1,000,000" in message
    assert "time_from" in message
    assert opened == []


# ----------------------------------------------------------------- building


def test_a_build_creates_and_marks_the_index_in_one_transaction_then_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    database = FakeDatabase()
    use(monkeypatch, database, conn)

    report = index_admin.create_agent_index("bgl", START, END, settings=SLM)

    kinds = [statement.split()[0] for statement in conn.executed]
    assert kinds == ["BEGIN", "CREATE", "COMMENT", "COMMIT", "VACUUM"]
    assert WAVE.marker() in conn.executed[2]
    assert conn.executed[4] == 'VACUUM (REFRESH_TABLE) "slm"."bgl_entry"'
    assert conn.closed
    assert report.status == "built"
    assert report.index == WAVE
    assert report.build_ms == pytest.approx(report.create_ms + report.refresh_ms)
    assert agent_indexes.registered("slm", "bgl") == WAVE
    # The Evidence holds every statement that ran, except the transaction control.
    statements = [s.split()[0] for s in report.result.sql]
    assert statements == ["SELECT", "SELECT", "CREATE", "COMMENT", "VACUUM", "SELECT"]
    assert len(report.result.params) == len(report.result.sql)


def test_a_failed_create_is_rolled_back_and_nothing_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConnection(fail_on="CREATE INDEX")
    use(monkeypatch, FakeDatabase(), conn)

    with pytest.raises(ToolError, match="rolled back"):
        index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert conn.executed[-1] == "ROLLBACK"
    assert agent_indexes.registered_all() == []


def test_a_lost_connection_during_the_build_leaves_nothing_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConnection(fail_on="CREATE INDEX", lose_connection=True)
    use(monkeypatch, FakeDatabase(), conn)

    with pytest.raises(ToolError, match="restart"):
        index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert "ROLLBACK" not in conn.executed
    assert conn.closed
    assert agent_indexes.registered_all() == []


def test_an_index_that_does_not_hold_the_whole_slice_is_dropped_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    opened = use(monkeypatch, FakeDatabase(verify=[ROWS - 5, ROWS - 5]), conn)

    with pytest.raises(ToolError, match="dropped"):
        index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert conn.executed.count('VACUUM (REFRESH_TABLE) "slm"."bgl_entry"') == 2  # one more try
    cleanup = opened[-1].executed
    assert cleanup == [f'DROP INDEX IF EXISTS "slm"."{NAME}"']
    assert agent_indexes.registered_all() == []


def test_a_verification_without_a_count_drops_the_index_again(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConnection()
    opened = use(monkeypatch, FakeDatabase(verify=[None]), conn)

    with pytest.raises(ToolError, match="dropped again"):
        index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert opened[-1].executed == [f'DROP INDEX IF EXISTS "slm"."{NAME}"']
    assert agent_indexes.registered_all() == []


def test_a_second_refresh_that_completes_the_index_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConnection()
    use(monkeypatch, FakeDatabase(verify=[ROWS - 5, ROWS]), conn)

    report = index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert report.status == "built"
    assert agent_indexes.registered("slm", "bgl") == WAVE


# ----------------------------------------------------------------- one agent index per Dataset


def test_an_existing_agent_index_for_another_slice_is_adopted_and_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = AgentIndex("slm", "agent_idx_bgl_ffffffff", "bgl", datetime(2005, 6, 1), datetime(2005, 6, 8), 9)
    conn = FakeConnection()
    use(monkeypatch, FakeDatabase(catalog=[(other.name, other.marker(), "bgl_entry")]), conn)

    with pytest.raises(ToolError) as excinfo:
        index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert "agent_idx_bgl_ffffffff" in str(excinfo.value)
    assert "drop_index" in str(excinfo.value)
    assert conn.executed == []
    assert agent_indexes.registered("slm", "bgl") == other


def test_the_same_slice_again_reuses_the_existing_index(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConnection()
    use(monkeypatch, FakeDatabase(catalog=[(NAME, WAVE.marker(), "bgl_entry")]), conn)

    report = index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert report.status == "reused"
    assert report.build_ms == 0
    assert conn.executed == []
    assert agent_indexes.registered("slm", "bgl") == WAVE


def test_a_registered_index_missing_from_the_catalog_is_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_indexes.register(AgentIndex("slm", "agent_idx_bgl_ffffffff", "bgl", START, END, ROWS))
    use(monkeypatch, FakeDatabase(), FakeConnection())

    index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert agent_indexes.registered("slm", "bgl") == WAVE


# ----------------------------------------------------------------- dropping


@pytest.mark.parametrize(
    "name", ["bgl_entry_idx", "slm.os_entry_idx", "agent_idx_bgl_xyz", "x; DROP TABLE y"]
)
def test_drop_refuses_anything_but_an_agent_index_name_without_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    database = FakeDatabase()
    opened = use(monkeypatch, database, FakeConnection())

    with pytest.raises(ToolError, match="only drops the agent indexes"):
        index_admin.drop_agent_index(name, settings=SLM)

    assert database.asked == []
    assert opened == []


def test_drop_refuses_an_agent_index_of_another_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeDatabase(), FakeConnection())

    with pytest.raises(ToolError, match="slm_t10"):
        index_admin.drop_agent_index(f"slm_t10.{NAME}", settings=SLM)


def test_drop_refuses_an_index_with_an_agent_name_but_no_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = use(monkeypatch, FakeDatabase(catalog=[(NAME, "made by hand", "bgl_entry")]), FakeConnection())

    with pytest.raises(ToolError, match="not built by create_index"):
        index_admin.drop_agent_index(NAME, settings=SLM)

    assert opened == []


def test_drop_refuses_an_unknown_agent_index_and_lists_the_existing_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = AgentIndex("slm", "agent_idx_bgl_ffffffff", "bgl", START, END, ROWS)
    use(monkeypatch, FakeDatabase(catalog=[(other.name, other.marker(), "bgl_entry")]), FakeConnection())

    with pytest.raises(ToolError) as excinfo:
        index_admin.drop_agent_index(NAME, settings=SLM)

    assert re.search(r"no agent index .*agent_idx_bgl_0a1b2c3d", str(excinfo.value))
    assert "agent_idx_bgl_ffffffff" in str(excinfo.value)


def test_drop_removes_a_marked_agent_index_and_unregisters_it(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_indexes.register(WAVE)
    conn = FakeConnection()
    use(monkeypatch, FakeDatabase(catalog=[(NAME, WAVE.marker(), "bgl_entry")]), conn)

    report = index_admin.drop_agent_index(f"slm.{NAME}", settings=SLM)

    assert conn.executed == [f'DROP INDEX "slm"."{NAME}"']
    assert report.status == "dropped"
    assert report.index == WAVE
    assert agent_indexes.registered_all() == []


def test_a_marker_on_an_index_of_another_table_is_not_an_agent_index(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeDatabase(catalog=[(NAME, WAVE.marker(), "ownership")]), FakeConnection())

    with pytest.raises(ToolError, match="not built by create_index"):
        index_admin.drop_agent_index(NAME, settings=SLM)


def test_existing_agent_indexes_lists_only_marked_ones_without_adopting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = [(NAME, WAVE.marker(), "bgl_entry"), ("bgl_entry_idx", None, "bgl_entry")]
    use(monkeypatch, FakeDatabase(catalog=catalog), FakeConnection())

    assert index_admin.existing_agent_indexes(settings=SLM) == [WAVE]
    assert agent_indexes.registered_all() == []


# ----------------------------------------------------------------- server-side build time (bench)


def test_a_plain_build_reports_no_server_time(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConnection()
    use(monkeypatch, FakeDatabase(), conn)

    report = index_admin.create_agent_index("bgl", START, END, settings=SLM)

    assert report.create_server_ms is None
    assert conn.executed[1].startswith("CREATE INDEX")


def test_an_explained_build_runs_the_same_ddl_and_reports_the_server_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    use(monkeypatch, FakeDatabase(), conn)

    report = index_admin.create_agent_index("bgl", START, END, settings=SLM, explain=True)

    kinds = [statement.split()[0] for statement in conn.executed]
    assert kinds == ["BEGIN", "EXPLAIN", "COMMENT", "COMMIT", "VACUUM"]
    plain = index_admin.create_statement(relation("bgl", settings=SLM), NAME, START, END).as_string(None)
    assert conn.executed[1] == f"EXPLAIN ANALYZE {plain}"
    assert report.create_server_ms == pytest.approx(239.1)
    assert agent_indexes.registered("slm", "bgl") == WAVE


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ("│ Total Time: 0.0016s │", 1.6),
        ("╭─ Summary ─╮\n│ Total Time: 1.25s │", 1250.0),
        ("no summary", None),
    ],
)
def test_the_server_time_is_read_from_the_explain_analyze_summary(plan: str, expected: float | None) -> None:
    found = index_admin.explain_total_ms(plan)

    assert found == (None if expected is None else pytest.approx(expected))


def test_a_forged_marker_for_another_dataset_on_bgl_entry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # The PR #59 review's attempt: a structurally valid openstack marker on an index of bgl_entry.
    forged = AgentIndex("slm", "agent_idx_openstack_cafebabe", "openstack", START, END, ROWS)
    opened = use(
        monkeypatch, FakeDatabase(catalog=[(forged.name, forged.marker(), "bgl_entry")]), FakeConnection()
    )

    with pytest.raises(ToolError, match="not built by create_index"):
        index_admin.drop_agent_index(forged.name, settings=SLM)

    assert opened == []
    assert index_admin.existing_agent_indexes(settings=SLM) == []
