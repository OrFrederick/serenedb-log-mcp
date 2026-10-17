"""Build and drop agent indexes (T19) on a separate write connection.

Every other statement in this server runs through `db.py` inside `BEGIN READ ONLY`, and that stays
true: the read-only layer has no write path. `create_index` and `drop_index` are the only tools that
change the database, and they do it here, on a write connection of their own:

- **Which login.** `DATABASE_URL`, never `DATABASE_URL_<ROLE>`: `CREATE INDEX` needs ownership of
  the canonical table, and the per-Role logins of T22 are SELECT-only on views. The reads around a
  build (the slice count, the catalog, the verification count) use the same login through the
  read-only layer, so they see what the write connection built.
- **Short-lived and never pooled.** One connection per call, closed afterwards, so the read-only
  pool can never hand out a connection that ran DDL.
- **Atomic marking.** `CREATE INDEX … WHERE ts >= … AND ts < …` and the `COMMENT ON INDEX` marker
  run in one transaction. An index without the marker is never an agent index, so a failed or
  interrupted build leaves either nothing or a complete, marked index that `drop_index` can remove.
- **Published before use.** `VACUUM (REFRESH_TABLE)` follows the commit (the index is eventually
  consistent), and the index is used only after `SELECT count(*)` on it equals the slice count.
  If it does not after a second refresh, the index is dropped again.
- **Bounded.** A slice holds at most `MAX_SLICE_ROWS` Log Entries, so a build stays a few seconds
  on the shared server; a timer cancels a build that runs past `BUILD_TIMEOUT_S` (SereneDB did not
  act on a cancel of a short build in our test, so the cap is the real bound).
- **One at a time.** A process-wide lock serialises builds and drops, and at most one agent index
  exists per schema and Dataset. Existing agent indexes in the catalog (from an earlier server
  process) are adopted into the registry whenever `create_index` or `drop_index` runs.

`build_ms` is what the trade-off needs: the time from `CREATE INDEX` until the index is usable
(`create_ms` + `refresh_ms`), without the slice count, the marker and the verification.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from psycopg import sql

from serenedb_log_mcp.config import Settings, get_settings
from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex, new_name
from serenedb_log_mcp.corpus.datasets import ENTRY_TABLES, Relation, relation
from serenedb_log_mcp.db import QueryResult, fetch_all, open_connection
from serenedb_log_mcp.scope import ScopeError

__all__ = [
    "BUILD_TIMEOUT_S",
    "MAX_SLICE_ROWS",
    "CatalogIndex",
    "IndexReport",
    "create_agent_index",
    "create_statement",
    "drop_agent_index",
    "existing_agent_indexes",
]

MAX_SLICE_ROWS = 1_000_000
"""Most Log Entries one agent index may hold. A full BGL index (4.7M rows) took 3 to 15 s to build
(#2), so a capped slice builds in a few seconds and never loads the shared server for long."""
BUILD_TIMEOUT_S = 300.0
"""A build still running after this is cancelled (if SereneDB acts on the cancel)."""

CATALOG_SQL = """SELECT c.relname, d.description, i.tablename
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_indexes AS i ON i.schemaname = n.nspname AND i.indexname = c.relname
LEFT JOIN pg_description AS d ON d.objoid = c.oid
WHERE n.nspname = %s
ORDER BY c.relname"""
"""Every index in the schema with its comment and table; agent indexes are picked out in Python."""

_lock = threading.Lock()


@dataclass(frozen=True)
class CatalogIndex:
    """One index of the schema as the catalog lists it."""

    schema: str
    name: str
    comment: str | None
    table: str

    @property
    def agent(self) -> AgentIndex | None:
        """The agent index this is: generated name, valid marker, on its Dataset's canonical table."""
        found = AgentIndex.from_marker(self.schema, self.name, self.comment)
        if found is None or ENTRY_TABLES.get(found.dataset) != self.table:  # type: ignore[call-overload]
            return None
        return found


@dataclass(frozen=True)
class IndexReport:
    """What `create_agent_index` or `drop_agent_index` did, with every statement for the Evidence."""

    index: AgentIndex
    status: Literal["built", "reused", "dropped"]
    result: QueryResult
    create_ms: float = 0.0
    refresh_ms: float = 0.0
    drop_ms: float = 0.0

    @property
    def build_ms(self) -> float:
        """From CREATE INDEX until the index is usable: create plus refresh."""
        return self.create_ms + self.refresh_ms


# Statements -----------------------------------------------------------------------------------------


def create_statement(rel: Relation, name: str, start: datetime, end: datetime) -> sql.Composed:
    """The partial inverted index over [start, end) of the Dataset's canonical table.

    It indexes the same columns as the Dataset's full index (every canonical column, `message` with
    the `log_text` dictionary), so every predicate a tool puts on the full index works on it too.
    The bounds are literals: SereneDB, like PostgreSQL, binds no parameters in DDL.
    """
    columns = [
        sql.SQL("{} {}").format(sql.Identifier(column), rel.text_dictionary)
        if column == "message"
        else sql.Identifier(column)
        for column in rel.dataset.table_columns
    ]
    ts = rel.column("time")
    return sql.SQL("CREATE INDEX {} ON {} USING inverted ({}) WHERE {} >= {} AND {} < {}").format(
        sql.Identifier(name),
        rel.table,
        sql.SQL(", ").join(columns),
        ts,
        sql.Literal(start),
        ts,
        sql.Literal(end),
    )


def _count_slice(rel: Relation, start: datetime, end: datetime) -> QueryResult:
    ts = rel.column("time")
    statement = sql.SQL("SELECT count(*) FROM {} WHERE {} >= %s AND {} < %s").format(rel.table, ts, ts)
    return fetch_all(statement.as_string(None), [start, end])


def _count_index(schema: str, name: str) -> QueryResult:
    statement = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, name))
    return fetch_all(statement.as_string(None))


def _catalog(schema: str) -> tuple[QueryResult, list[CatalogIndex]]:
    result = fetch_all(CATALOG_SQL, [schema])
    found = [CatalogIndex(schema, str(name), comment, str(table)) for name, comment, table in result.rows]
    return result, found


def existing_agent_indexes(*, settings: Settings | None = None) -> list[AgentIndex]:
    """The agent indexes in SLM_SCHEMA as the catalog lists them; the registry is left alone."""
    schema = (settings if settings is not None else get_settings()).slm_schema
    _, found = _catalog(schema)
    return [index.agent for index in found if index.agent is not None]


def _adopt(schema: str, found: Sequence[CatalogIndex]) -> list[AgentIndex]:
    """Make the registry match the catalog: register its agent indexes, forget the vanished ones."""
    present = [index.agent for index in found if index.agent is not None]
    for index in agent_indexes.registered_all():
        if index.schema == schema and index not in present:
            agent_indexes.unregister(schema, index.name)
    for index in present:
        agent_indexes.register(index)
    return present


# The write connection -------------------------------------------------------------------------------


@dataclass
class _WriteSession:
    """One short-lived write connection; records each statement it ran and how long it took."""

    settings: Settings
    conn: Any = None
    statements: list[str] = field(default_factory=list)
    ms: list[float] = field(default_factory=list)

    def __enter__(self) -> _WriteSession:
        # open_connection: autocommit (transactions are explicit), no prepared statements.
        self.conn = open_connection(self.settings.database_url, client_binding=False)
        return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(psycopg.Error):
            self.conn.close()

    def run(self, statement: sql.Composable) -> float:
        """Run one statement, record it, return its wall time in ms."""
        text = statement.as_string(None)
        started = time.perf_counter()
        self.conn.execute(text)
        elapsed = (time.perf_counter() - started) * 1000
        self.statements.append(text)
        self.ms.append(elapsed)
        return elapsed

    def control(self, statement: str) -> None:
        """BEGIN, COMMIT or ROLLBACK: run, not recorded (the read tools record none either)."""
        self.conn.execute(statement)

    @property
    def lost(self) -> bool:
        return bool(self.conn is None or self.conn.closed or self.conn.broken)

    def result(self) -> QueryResult:
        return QueryResult(0, [], False, sum(self.ms), list(self.statements), [[] for _ in self.statements])


# Create ---------------------------------------------------------------------------------------------


def create_agent_index(
    dataset: str,
    start: datetime,
    end: datetime,
    *,
    settings: Settings | None = None,
    max_rows: int | None = None,
) -> IndexReport:
    """Build (or reuse) the agent index of `dataset` over [start, end) and register it for routing.

    Every statement runs as `DATABASE_URL`, whatever the asking Role (see the module docstring).
    Raises ScopeError for an empty slice or one above `max_rows` (default `MAX_SLICE_ROWS`, read at
    call time) and ToolError when another slice of the Dataset is already indexed or the build fails.
    """
    settings = settings if settings is not None else get_settings()
    rel = relation(dataset, settings=settings)
    schema = rel.schema
    counted = _count_slice(rel, start, end)
    rows = int(counted.rows[0][0])
    _check_slice(rows, rel.dataset.name, start, end, MAX_SLICE_ROWS if max_rows is None else max_rows)
    with _lock:
        listing, found = _catalog(schema)
        existing = next((i for i in _adopt(schema, found) if i.dataset == rel.dataset.name), None)
        if existing is not None:
            if existing.time_from == start and existing.time_to == end:
                result = QueryResult.combine(counted, listing, total=0, rows=[])
                return IndexReport(existing, "reused", result)
            raise ToolError(
                f"{existing.dataset} already has the agent index {existing.qualified_name} over "
                f"{existing.time_from.isoformat()} to {existing.time_to.isoformat()} "
                f"({existing.rows:,} Log Entries), and there is at most one agent index per Dataset. "
                "Calls whose Data Scope lies inside that slice already use it. To index another "
                f"slice, call drop_index(name='{existing.name}') first."
            )
        index = AgentIndex(schema, new_name(rel.dataset.name), rel.dataset.name, start, end, rows)
        with _WriteSession(settings) as session:
            create_ms = _build(session, rel, index)
            refresh_ms, verified = _publish(session, rel, index)
        agent_indexes.register(index)
    result = QueryResult.combine(counted, listing, session.result(), *verified, total=0, rows=[])
    return IndexReport(index, "built", result, create_ms=create_ms, refresh_ms=refresh_ms)


def _check_slice(rows: int, dataset: str, start: datetime, end: datetime, max_rows: int) -> None:
    span = f"{dataset} from {start.isoformat()} to {end.isoformat()}"
    if rows == 0:
        raise ScopeError(
            f"The slice of {span} holds no Log Entries, so there is nothing to index. Call "
            "describe_corpus for the Dataset's time range and pass time_from and time_to inside it."
        )
    if rows > max_rows:
        days = max((end - start).total_seconds() / 86400, 1 / 1440)
        raise ScopeError(
            f"The slice of {span} holds {rows:,} Log Entries, more than the {max_rows:,} an agent "
            f"index may hold (about {rows / days:,.0f} per day in this slice). Narrow time_from and "
            "time_to to the period you are investigating."
        )


def _build(session: _WriteSession, rel: Relation, index: AgentIndex) -> float:
    """CREATE INDEX and its marker in one transaction; returns the CREATE INDEX time in ms."""
    timer = threading.Timer(BUILD_TIMEOUT_S, lambda: session.conn.cancel_safe())
    timer.daemon = True
    try:
        session.control("BEGIN")
        timer.start()
        create_ms = session.run(create_statement(rel, index.name, index.time_from, index.time_to))
        session.run(
            sql.SQL("COMMENT ON INDEX {} IS {}").format(
                sql.Identifier(index.schema, index.name), sql.Literal(index.marker())
            )
        )
        session.control("COMMIT")
    except psycopg.Error as exc:
        if session.lost:
            raise ToolError(
                "SereneDB closed the connection while building the agent index, most likely because "
                "it restarted after a crash. The build ran in an open transaction, so nothing was "
                "left behind; call create_index again."
            ) from exc
        with contextlib.suppress(psycopg.Error):
            session.control("ROLLBACK")
        if isinstance(exc, psycopg.errors.QueryCanceled):
            raise ToolError(
                f"Building the agent index ran past {BUILD_TIMEOUT_S:g} s and was cancelled and rolled "
                "back. Narrow time_from and time_to to a smaller slice."
            ) from exc
        raise ToolError(
            f"SereneDB refused to build the agent index ({exc.sqlstate or 'no SQLSTATE'}: "
            f"{_first_line(exc)}). The transaction was rolled back, so nothing was left behind."
        ) from exc
    finally:
        timer.cancel()
    return create_ms


def _publish(session: _WriteSession, rel: Relation, index: AgentIndex) -> tuple[float, list[QueryResult]]:
    """Refresh until the index holds the whole slice (two tries); drop it again otherwise."""
    refresh = sql.SQL("VACUUM (REFRESH_TABLE) {}").format(rel.table)
    refresh_ms = 0.0
    verified: list[QueryResult] = []
    try:
        for _ in range(2):
            refresh_ms += session.run(refresh)
            verified.append(_count_index(index.schema, index.name))
            held = int(verified[-1].rows[0][0])
            if held == index.rows:
                return refresh_ms, verified
        problem = f"it holds {held:,} of the slice's {index.rows:,} Log Entries after two refreshes"
    except (psycopg.Error, ToolError) as exc:
        problem = f"refreshing it failed ({_first_line(exc)})"
    dropped = _drop_quietly(session.settings, index)
    raise ToolError(
        f"The agent index {index.qualified_name} was built, but {problem}, so it "
        + ("was dropped again" if dropped else "could not be dropped again; call drop_index on it")
        + ". Call create_index again."
    )


def _drop_quietly(settings: Settings, index: AgentIndex) -> bool:
    """Best-effort DROP INDEX IF EXISTS on a new write connection; True when it ran."""
    try:
        with _WriteSession(settings) as session:
            session.run(sql.SQL("DROP INDEX IF EXISTS {}").format(sql.Identifier(index.schema, index.name)))
    except psycopg.Error:
        return False
    return True


# Drop -----------------------------------------------------------------------------------------------


def drop_agent_index(name: str, *, settings: Settings | None = None) -> IndexReport:
    """Drop one agent index that `create_agent_index` built; ToolError for anything else."""
    settings = settings if settings is not None else get_settings()
    schema = settings.slm_schema
    bare = _bare_name(name, schema)
    with _lock:
        listing, found = _catalog(schema)
        present = _adopt(schema, found)
        entry = next((index for index in found if index.name == bare), None)
        if entry is None:
            names = ", ".join(index.qualified_name for index in present) or "none"
            raise ToolError(
                f"There is no agent index {schema}.{bare}; the agent indexes in {schema} are: {names}. "
                "Pass a name exactly as create_index returned it."
            )
        index = entry.agent
        if index is None:
            raise ToolError(
                f"The index {schema}.{bare} was not built by create_index (it carries no agent index "
                "marker on its Dataset's table), so drop_index refuses to drop it."
            )
        with _WriteSession(settings) as session:
            drop_ms = session.run(sql.SQL("DROP INDEX {}").format(sql.Identifier(schema, bare)))
        agent_indexes.unregister(schema, bare)
    result = QueryResult.combine(listing, session.result(), total=0, rows=[])
    return IndexReport(index, "dropped", result, drop_ms=drop_ms)


def _bare_name(name: str, schema: str) -> str:
    """The index name without its schema; ToolError unless it is an agent index name in `schema`."""
    given = name.strip()
    qualifier, _, bare = given.rpartition(".")
    if qualifier and qualifier != schema and agent_indexes.is_agent_name(bare):
        raise ToolError(
            f"{given!r} names schema {qualifier!r}, but this server's agent indexes live in "
            f"{schema!r} (SLM_SCHEMA); pass the name create_index returned."
        )
    if not agent_indexes.is_agent_name(bare):
        raise ToolError(
            f"drop_index only drops the agent indexes create_index built, which are named like "
            f"agent_idx_bgl_0a1b2c3d; {given!r} is not one. The Datasets' full indexes are built "
            "by `make derive` and cannot be dropped through this tool."
        )
    return bare


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__
