"""The database access layer: every tool runs its SQL through this module (plan section 2.4).

What is true for every statement is written once here:

- it runs inside `BEGIN READ ONLY`, because SereneDB does not enforce
  `default_transaction_read_only`;
- a `threading.Timer` calls `conn.cancel_safe()` after `SLM_QUERY_TIMEOUT_S`, because SereneDB
  accepts `statement_timeout` but does not enforce it. The cancel is not always honoured: on
  26.09.1.3 a bound parameter inside a table function such as `generate_series` makes SereneDB
  ignore it (measured in the #47 review), while 26.10.1 honoured every cancel, bound or literal, in
  both binding modes (33 of 33 runs). So every result is also checked against the limit, and a
  call that ran past it says so in `QueryResult.notes`;
- only a closed or broken connection counts as lost: SereneDB reports ordinary IO errors (a
  missing `read_csv` file, an unreachable HTTP source) as SQLSTATE `08000` on a healthy session;
- on a lost connection (SereneDB 26.10.1 SIGSEGVs intermittently and restarts in a few seconds) the
  layer waits until `SELECT 1` answers on a new connection, retries the statements once and says
  so in `QueryResult.notes`;
- parameters are always bound, never formatted into the SQL;
- `numeric` results (for example `sum()` and SereneDB's `HUGEINT`) come back as `int` when they are
  integral and as `float` otherwise, never as `Decimal`, so the Evidence envelope serialises them
  as JSON numbers.

The layer never builds an `Evidence`; tools hand its `QueryResult` to `toolkit.evidence_tool`.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from psycopg.abc import Buffer
from psycopg.adapt import Loader
from psycopg.conninfo import conninfo_to_dict

from serenedb_log_mcp.config import Role, get_settings
from serenedb_log_mcp.toolkit import ToolContractError

if TYPE_CHECKING:
    from serenedb_log_mcp.toolkit import QueryResultLike  # only for the static conformance check

__all__ = [
    "Database",
    "DatabaseLayerError",
    "DatabaseMisconfigured",
    "DatabaseRestarted",
    "DatabaseUnavailable",
    "IntOrFloatNumericLoader",
    "QueryFailed",
    "QueryResult",
    "QueryTimeout",
    "fetch_all",
    "fetch_limited",
    "get_database",
    "int_or_float",
    "open_connection",
    "set_connector",
]

CONNECT_TIMEOUT_S = 10
"""How long one connection attempt may take (libpq `connect_timeout`)."""
RECONNECT_WAIT_S = 30.0
"""How long the layer waits for `SELECT 1` after a lost connection. A restart took 2 to 3 s (#2)."""
POLL_INTERVAL_S = 1.0
"""Pause between two `SELECT 1` attempts while SereneDB restarts."""
MAX_IDLE_CONNECTIONS = 4
"""Idle connections kept per DSN, so a call does not pay for a new connection each time."""

CANCEL_ATTEMPTS = 3
"""How often the timer sends a cancel request that failed (for example psycopg's CancellationTimeout)."""
CANCEL_RETRY_S = 1.0
"""Pause between two cancel requests."""

_T = TypeVar("_T")


@dataclass(frozen=True)
class QueryResult:
    """What every query helper returns (plan section 2.7).

    `sql` holds each statement exactly as the tool passed it to psycopg: `%s` where parameters were
    bound and `%%` for a literal `%` in a statement that binds parameters, so running `sql[i]` with
    `params[i]` through psycopg (or this layer) repeats the call, provided a statement without
    parameters is run with `params[i] or None` (an empty list makes psycopg parse placeholders, so a
    bare `%` would fail); a SQL client needs `%%` read as `%`. `params` holds the bound values, one
    list per statement in the same order. `query_ms` is the wall-clock time of those statements
    only. `notes` carries what the agent must know about how the numbers were obtained, for example
    a retry after a database restart.
    """

    total: int
    rows: list[tuple[Any, ...]]
    truncated: bool
    query_ms: float
    sql: list[str]
    params: list[list[Any]]
    notes: list[str] = field(default_factory=list)

    @classmethod
    def combine(
        cls,
        *results: QueryResult,
        total: int,
        rows: Sequence[Sequence[Any]],
        truncated: bool | None = None,
    ) -> QueryResult:
        """One result for a tool that ran several statements, for example a search plus its facets.

        `sql` and `params` are concatenated in the order given, `query_ms` is summed and `notes`
        are merged without repeats. The caller decides `total` and `rows`; `truncated` defaults to
        `len(rows) < total`.
        """
        if not results:
            raise ValueError("QueryResult.combine needs at least one result")
        notes: list[str] = []
        for part in results:
            notes.extend(note for note in part.notes if note not in notes)
        kept = [tuple(row) for row in rows]
        return cls(
            total=total,
            rows=kept,
            truncated=len(kept) < total if truncated is None else truncated,
            query_ms=sum(part.query_ms for part in results),
            sql=[statement for part in results for statement in part.sql],
            params=[list(values) for part in results for values in part.params],
            notes=notes,
        )


if TYPE_CHECKING:

    def _conforms_to_the_evidence_contract(result: QueryResult) -> QueryResultLike:
        """mypy proves here that `QueryResult` is what `toolkit.evidence_tool` consumes."""
        return result


def int_or_float(value: Decimal) -> int | float:
    """A `numeric` value as JSON can carry it: `int` when integral, `float` otherwise."""
    if value.is_finite() and value == value.to_integral_value():
        return int(value)
    return float(value)


class IntOrFloatNumericLoader(Loader):
    """Loads `numeric` (text format) as `int` or `float` instead of `Decimal`.

    SereneDB returns `numeric` for `sum()` over integers, window sums and `HUGEINT`; as `Decimal`
    those would reach the Evidence envelope as JSON strings.
    """

    def load(self, data: Buffer) -> int | float:
        return int_or_float(Decimal(bytes(data).decode()))


# Errors ---------------------------------------------------------------------------------------------


class DatabaseLayerError(ToolError):
    """Base of the layer's errors. They are `ToolError`s, so their text reaches the agent unchanged."""


class QueryTimeout(DatabaseLayerError):
    """The statements ran longer than `SLM_QUERY_TIMEOUT_S` and were cancelled."""


class QueryFailed(DatabaseLayerError):
    """A statement did not run; `sqlstate` says why (None when psycopg refused it) and `sql` is
    the statement. Not retried."""

    def __init__(self, message: str, sqlstate: str | None, sql: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.sql = sql


class DatabaseRestarted(DatabaseLayerError):
    """The connection was lost again when the statements were retried after a restart."""


class DatabaseUnavailable(DatabaseLayerError):
    """SereneDB did not answer `SELECT 1` within the bounded wait."""


class DatabaseMisconfigured(DatabaseUnavailable):
    """SereneDB answered but refused the connection settings; waiting cannot help."""


# Connections ----------------------------------------------------------------------------------------


class CursorLike(Protocol):
    def fetchall(self) -> list[tuple[Any, ...]]: ...


class ConnectionLike(Protocol):
    """The part of a psycopg connection the layer uses; tests pass fakes with the same shape."""

    @property
    def closed(self) -> bool: ...

    @property
    def broken(self) -> bool: ...

    def execute(self, query: str, params: Sequence[Any] | None = None) -> CursorLike: ...

    def cancel_safe(self) -> None: ...

    def close(self) -> None: ...


class Connector(Protocol):
    def __call__(self, dsn: str, *, client_binding: bool) -> ConnectionLike: ...


def open_connection(dsn: str, *, client_binding: bool) -> psycopg.Connection[tuple[Any, ...]]:
    """A connection as the layer needs it.

    Autocommit, because the layer issues `BEGIN READ ONLY` and `ROLLBACK` itself and starts the
    timeout only after the transaction began. Server-side binding by default, `ClientCursor` when
    `client_binding` (`SLM_CLIENT_BINDING=1`); both bind every value. No automatic prepared
    statements, which is the path the #2 measurements took. `numeric` loads as int or float.
    """
    conn = psycopg.connect(
        dsn,
        autocommit=True,
        prepare_threshold=None,
        connect_timeout=CONNECT_TIMEOUT_S,
        cursor_factory=psycopg.ClientCursor if client_binding else psycopg.Cursor,
    )
    conn.adapters.register_loader("numeric", IntOrFloatNumericLoader)
    return conn


class _ConnectionLost(Exception):
    """Internal: the session ended under a statement; the caller waits and retries once."""


class _Session:
    """The statements of one attempt, with the time they took."""

    def __init__(self, conn: ConnectionLike, deadline: _Deadline | None = None) -> None:
        self._conn = conn
        self._deadline = deadline
        self.sql: list[str] = []
        self.params: list[list[Any]] = []
        self.notes: list[str] = []
        self.running: str | None = None  # the statement in progress, named when it fails
        self.query_ms = 0.0

    def fetch(self, sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        values = list(params)
        if self._deadline is not None and self._deadline.fired:
            # A cancel that reached the backend between two statements was dropped by the server,
            # so the next statement would run without a limit: stop here instead.
            raise QueryTimeout(_timeout_message(self._deadline.timeout_s))
        self.running = sql
        started = time.perf_counter()
        # No parameter list at all when there are no values, so a literal % stays a literal.
        rows = self._conn.execute(sql, values or None).fetchall()
        self.query_ms += (time.perf_counter() - started) * 1000
        self.sql.append(sql)
        self.params.append(values)
        return rows


class _Deadline:
    """The time limit of one attempt.

    A timer cancels the statements after `timeout_s` and retries a cancel request that fails.
    `stop()` ends it: once `stop()` has returned, no cancel request is in flight and none will be
    sent, and `fired` says whether one was. A connection whose deadline fired is never reused, so a
    cancel can never reach a later call on a pooled connection.
    """

    def __init__(self, conn: ConnectionLike, timeout_s: float) -> None:
        self._conn = conn
        self.timeout_s = timeout_s
        self._lock = threading.Lock()  # held while a cancel request is decided and sent
        self._stopped = threading.Event()
        self._timer = threading.Timer(timeout_s, self._cancel)
        self._timer.daemon = True
        self.fired = False
        self.errors: list[str] = []

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        with self._lock:  # waits for a cancel request in flight
            self._stopped.set()
        self._timer.cancel()

    def _cancel(self) -> None:
        for _ in range(CANCEL_ATTEMPTS):
            with self._lock:
                if self._stopped.is_set():
                    return
                self.fired = True
                try:
                    self._conn.cancel_safe()
                except psycopg.Error as exc:
                    self.errors.append(_first_line(exc))
                else:
                    return
            if self._stopped.wait(CANCEL_RETRY_S):
                return


class Database:
    """Runs fixed SQL templates against one DSN: read-only, with a timeout and one retry.

    Thread-safe: each call takes its own connection from a small idle pool.
    """

    def __init__(
        self,
        dsn: str,
        *,
        timeout_s: float = 15.0,
        client_binding: bool = False,
        reconnect_wait_s: float = RECONNECT_WAIT_S,
        poll_interval_s: float = POLL_INTERVAL_S,
        connect: Connector = open_connection,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.dsn = dsn
        self.timeout_s = timeout_s
        self.client_binding = client_binding
        self.reconnect_wait_s = reconnect_wait_s
        self.poll_interval_s = poll_interval_s
        self._connect = connect
        self._sleep = sleep
        self._monotonic = monotonic
        self._idle: list[ConnectionLike] = []
        self._lock = threading.Lock()

    def fetch_limited(
        self,
        count_sql: str,
        rows_sql: str,
        params: Sequence[Any],
        limit: int,
        *,
        rows_params: Sequence[Any] | None = None,
    ) -> QueryResult:
        """The exact total from `count_sql`, then at most `limit` rows from `rows_sql`.

        `count_sql` must return one row with one integer. `rows_sql` runs with `params` plus
        `limit` appended, so its last placeholder is the LIMIT; pass `rows_params` when the row
        query binds other values. A row query that returns more than `limit` rows is a template
        bug and raises `toolkit.ToolContractError`, as does a `limit` below 1. The row query is
        skipped when the total is 0. Both statements run in one read-only transaction, so the total
        and the rows come from one snapshot.

        A literal `%` must be written `%%` in every statement that binds parameters, and
        `rows_sql` always binds at least `limit`. Only a statement run with no parameters at all
        (for example a `count_sql` with empty `params`) takes a bare `%`. A WHERE clause shared by
        both statements is therefore safest with `%%`, or with the pattern bound as a parameter.
        """
        if limit < 1:
            raise ToolContractError(f"fetch_limited needs a limit of at least 1, not {limit}.")
        bound_rows = [*params, limit] if rows_params is None else list(rows_params)

        def work(session: _Session) -> tuple[int, list[tuple[Any, ...]]]:
            total = _count(session.fetch(count_sql, params), count_sql)
            rows = session.fetch(rows_sql, bound_rows) if total else []
            return total, rows

        (total, rows), session, notes = self._run(work)
        if len(rows) > limit:
            raise ToolContractError(
                f"The row query returned {len(rows)} rows for limit={limit}; its LIMIT must bind the "
                f"limit (the last parameter, or the one in rows_params): {rows_sql}"
            )
        return QueryResult(
            total=total,
            rows=rows,
            truncated=len(rows) < total,
            query_ms=session.query_ms,
            sql=session.sql,
            params=session.params,
            notes=notes,
        )

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        """Every row of one statement, for results that are complete by construction.

        Use it for aggregates whose size the template bounds (trend buckets, a few scalars); the
        total is the number of rows and the result is never truncated. Never cap the rows inside a
        `fetch_all` template: a LIMIT there would be published as the whole population. Use
        `fetch_limited` whenever more rows can match than the call returns.
        """
        rows, session, notes = self._run(lambda session: session.fetch(sql, params))
        return QueryResult(
            total=len(rows),
            rows=rows,
            truncated=False,
            query_ms=session.query_ms,
            sql=session.sql,
            params=session.params,
            notes=notes,
        )

    def close(self) -> None:
        """Close the idle connections."""
        self._drop_idle()

    # Retry --------------------------------------------------------------------------------------

    def _run(self, work: Callable[[_Session], _T]) -> tuple[_T, _Session, list[str]]:
        try:
            value, session = self._attempt(work)
        except _ConnectionLost:
            pass  # wait for the restart below, then retry once
        else:
            return value, session, session.notes
        waited = self._wait_until_ready()
        note = (
            "SereneDB closed the connection during this call, most likely because it restarted "
            f"after a crash. The database layer waited {waited:.1f} s until it answered SELECT 1 "
            "and retried the statements once; the numbers come from that retry."
        )
        try:
            value, session = self._attempt(work)
        except _ConnectionLost as lost:
            cause = lost.__cause__
            raise DatabaseRestarted(
                "SereneDB closed the connection again when the statements were retried, so it "
                "restarted twice during this call (SereneDB 26.10.1 crashes intermittently): "
                f"{_first_line(cause or lost)} If this call sorted by relevance, call it again with "
                "sort=time; otherwise call it again once more, or narrow the Data Scope."
            ) from cause
        return value, session, [note, *session.notes]

    def _wait_until_ready(self) -> float:
        """Wait until `SELECT 1` answers on a new connection; bounded by `reconnect_wait_s`."""
        self._drop_idle()  # a restart ended every session, so the idle ones are dead too
        started = self._monotonic()
        deadline = started + self.reconnect_wait_s
        while True:
            conn: ConnectionLike | None = None
            try:
                conn = self._connect(self.dsn, client_binding=self.client_binding)
                conn.execute("SELECT 1").fetchall()
            except psycopg.Error as exc:
                if conn is not None:
                    _close_quietly(conn)
                if _is_misconfigured(exc):
                    raise DatabaseMisconfigured(
                        f"SereneDB at {_safe_dsn(self.dsn)} refused the connection settings "
                        f"({_first_line(exc)}). Check DATABASE_URL (or DATABASE_URL_<ROLE>); "
                        "waiting will not help."
                    ) from exc
                now = self._monotonic()
                if now >= deadline:
                    raise DatabaseUnavailable(
                        f"SereneDB at {_safe_dsn(self.dsn)} is not reachable: it did not answer "
                        f"SELECT 1 within {self.reconnect_wait_s:g} s ({_first_line(exc)}). It may "
                        "still be restarting, so call the tool again in a minute. If it keeps "
                        "failing, check that SereneDB is running and that DATABASE_URL points at it."
                    ) from exc
                self._sleep(min(self.poll_interval_s, deadline - now))
            except BaseException:
                if conn is not None:
                    _close_quietly(conn)
                raise
            else:
                self._release(conn)
                return self._monotonic() - started

    # One attempt --------------------------------------------------------------------------------

    def _attempt(self, work: Callable[[_Session], _T]) -> tuple[_T, _Session]:
        try:
            conn = self._acquire()
        except psycopg.Error as exc:
            raise _ConnectionLost from exc
        deadline = _Deadline(conn, self.timeout_s)
        session = _Session(conn, deadline)
        try:
            conn.execute("BEGIN READ ONLY")
            deadline.start()  # after BEGIN, so the limit covers only the statements
            try:
                value = work(session)
            finally:
                deadline.stop()
        except psycopg.Error as exc:
            if _is_lost(conn):
                _close_quietly(conn)
                raise _ConnectionLost from exc
            self._end(conn, discard=deadline.fired)
            if deadline.fired and isinstance(exc, psycopg.errors.QueryCanceled):
                raise QueryTimeout(_timeout_message(self.timeout_s)) from exc
            raise _rejected(exc, session.running) from exc
        except BaseException:
            self._end(conn, discard=True)
            raise
        if (note := self._overrun_note(session, deadline)) is not None:
            session.notes.append(note)
        self._end(conn, discard=deadline.fired)
        return value, session

    def _overrun_note(self, session: _Session, deadline: _Deadline) -> str | None:
        """A note when the statements finished although the time limit fired or was exceeded.

        `cancel_safe()` is the only thing that enforces the limit, and it is not always honoured:
        on 26.09.1.3 a bound parameter inside a table function such as `generate_series` makes
        SereneDB ignore the cancel (measured by OrFrederick's Claude in the #47 review; 26.10.1
        honoured every cancel, bound or literal). So every result is checked against the limit.
        """
        seconds = session.query_ms / 1000
        if not deadline.fired and seconds <= self.timeout_s:
            return None
        limit = f"{self.timeout_s:g} s limit (SLM_QUERY_TIMEOUT_S)"
        if seconds <= self.timeout_s:
            return (
                f"The statements finished just as the {limit} expired. The numbers are complete, "
                "but a wider Data Scope may time out."
            )
        if len(deadline.errors) >= CANCEL_ATTEMPTS:
            why = f"the cancel request failed ({deadline.errors[-1]})"
        elif deadline.fired:
            why = "SereneDB did not act on the cancel request"
        else:
            why = "the time limit did not fire in time"
        return (
            f"The statements ran {seconds:.1f} s, past the {limit}, because {why}. The numbers are "
            "complete, but the call took longer than allowed: narrow the Data Scope (a shorter time "
            "range or more filters) before the next call."
        )

    def _end(self, conn: ConnectionLike, *, discard: bool) -> None:
        """Roll the read-only transaction back and pool the connection, or drop it."""
        if discard:
            _close_quietly(conn)  # closing the session ends its read-only transaction
            return
        try:
            conn.execute("ROLLBACK")
        except psycopg.Error:
            _close_quietly(conn)
            return
        self._release(conn)

    # Pool ---------------------------------------------------------------------------------------

    def _acquire(self) -> ConnectionLike:
        with self._lock:
            while self._idle:
                conn = self._idle.pop()
                if not (conn.closed or conn.broken):
                    return conn
        return self._connect(self.dsn, client_binding=self.client_binding)

    def _release(self, conn: ConnectionLike) -> None:
        if conn.closed or conn.broken:
            return
        with self._lock:
            if len(self._idle) < MAX_IDLE_CONNECTIONS:
                self._idle.append(conn)
                return
        _close_quietly(conn)

    def _drop_idle(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for conn in idle:
            _close_quietly(conn)


def _timeout_message(timeout_s: float) -> str:
    return (
        f"The query ran longer than the {timeout_s:g} s limit (SLM_QUERY_TIMEOUT_S) and was "
        "cancelled. Narrow the Data Scope: a shorter time range, a Component, level or Pattern "
        "filter, or a coarser trend bucket."
    )


def _rejected(exc: psycopg.Error, sql: str | None) -> QueryFailed:
    """The error for a statement that did not run, with the hint that fits who refused it."""
    statement = f" Statement: {_compact(sql)}" if sql else ""
    if exc.sqlstate is None and isinstance(exc, psycopg.DataError):
        # psycopg could not encode a bound value, for example a NUL byte in a string.
        return QueryFailed(
            f"A value you passed cannot be sent to SereneDB: {_first_line(exc)} Remove the offending "
            f"characters from the value and call the tool again.{statement}",
            None,
            sql,
        )
    if exc.sqlstate is None:
        # psycopg refused the statement before sending it, so no input of the agent's can fix it.
        return QueryFailed(
            f"The tool's SQL template is broken, so the query was not sent to SereneDB: "
            f"{_first_line(exc)} This is a bug in the tool, not in your inputs; use another tool "
            f"for this question.{statement}",
            None,
            sql,
        )
    return QueryFailed(
        f"SereneDB rejected the query ({exc.sqlstate}): {_first_line(exc)} Check the names you "
        "filtered on with describe_corpus and call the tool again; if they are right, the tool's "
        f"SQL is at fault.{statement}",
        exc.sqlstate,
        sql,
    )


def _compact(sql: str, limit: int = 200) -> str:
    """One line of SQL for an error message."""
    text = " ".join(sql.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _count(rows: list[tuple[Any, ...]], sql: str) -> int:
    if len(rows) != 1 or len(rows[0]) != 1 or not isinstance(rows[0][0], int):
        # Never echo the rows: they can hold Log Entry text, and error text skips the Role hook.
        shape = f"{len(rows)} rows of {len(rows[0]) if rows else 0} values"
        raise ToolContractError(f"The count query must return one row with one integer, not {shape}: {sql}")
    return int(rows[0][0])


def _is_lost(conn: ConnectionLike) -> bool:
    """Did the session end under the statement (crash, restart, network), not the statement fail?

    Only the connection's own state decides. The SQLSTATE cannot: SereneDB reports ordinary IO
    errors (a missing `read_csv` file, an unreachable HTTP source, ATTACH of a missing database) as
    class 08 (`08000`) on a healthy connection, and a crash or a terminated backend leaves the
    connection `broken` (measured on 26.09.1.3 and 26.10.1).
    """
    return conn.closed or conn.broken


def _close_quietly(conn: ConnectionLike) -> None:
    with contextlib.suppress(psycopg.Error):
        conn.close()


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    line = text.splitlines()[0].strip() if text else type(exc).__name__
    return line if line.endswith((".", "!", "?")) else f"{line}."


_SHOWN_DSN_KEYS = ("host", "hostaddr", "port", "dbname", "user")
"""The DSN parts an error message may show; everything else (password, sslpassword, …) stays out."""


def _safe_dsn(dsn: str) -> str:
    """Where the layer connects, for error messages: only the keys in `_SHOWN_DSN_KEYS`."""
    try:
        parts = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return "<unparseable DATABASE_URL>"
    return " ".join(f"{key}={parts[key]}" for key in _SHOWN_DSN_KEYS if key in parts)


_CONFIG_REFUSAL_RE = re.compile(
    r"FATAL:\s+(?:role \S+ does not exist|database \S+ (?:does not exist|is not accessible)"
    r"|password authentication failed|no pg_hba\.conf entry)"
)


def _is_misconfigured(exc: psycopg.Error) -> bool:
    """Did SereneDB refuse the connection settings, rather than fail to answer?

    An unparseable DSN raises ProgrammingError. A wrong user or database arrives as an
    OperationalError without a SQLSTATE, so the libpq text decides (SereneDB 26.10.1 says
    `database "x" is not accessible`).
    """
    return isinstance(exc, psycopg.ProgrammingError) or bool(_CONFIG_REFUSAL_RE.search(str(exc)))


# Process-wide layers --------------------------------------------------------------------------------

_databases: dict[tuple[str, float, bool], Database] = {}
_databases_lock = threading.Lock()
_connector: Connector = open_connection


def set_connector(connect: Connector | None) -> Connector:
    """Open the connections of every process-wide layer with `connect`; None restores `open_connection`.

    Returns the connector that was in place, so a caller can put it back. The ledger replay (T13,
    `export.replay`) wraps the real connection with it, so that every statement a tool issues runs
    with the parameters the Evidence Ledger stored for it. The layers built so far are dropped with
    their idle connections, so no connection opened the other way is reused afterwards.
    """
    global _connector
    with _databases_lock:
        layers = list(_databases.values())
        _databases.clear()
        previous = _connector
        _connector = open_connection if connect is None else connect
    for layer in layers:
        layer.close()
    return previous


def get_database(role: Role | None = None) -> Database:
    """The layer for one Role: `DATABASE_URL_<ROLE>` when it is set, `DATABASE_URL` otherwise.

    Built from the process-wide settings at call time and kept per connection setting, so the idle
    connections survive between tool calls. `role=None` (scripts) uses `DATABASE_URL`.
    """
    settings = get_settings()
    dsn = settings.database_url if role is None else settings.database_url_for(role)
    key = (dsn, settings.query_timeout_s, settings.client_binding)
    with _databases_lock:
        layer = _databases.get(key)
        if layer is None:
            layer = Database(
                dsn,
                timeout_s=settings.query_timeout_s,
                client_binding=settings.client_binding,
                connect=_connector,
            )
            _databases[key] = layer
        return layer


def fetch_limited(
    count_sql: str,
    rows_sql: str,
    params: Sequence[Any],
    limit: int,
    *,
    role: Role | None = None,
    rows_params: Sequence[Any] | None = None,
) -> QueryResult:
    """`Database.fetch_limited` on the layer for `role`."""
    return get_database(role).fetch_limited(count_sql, rows_sql, params, limit, rows_params=rows_params)


def fetch_all(sql: str, params: Sequence[Any] = (), *, role: Role | None = None) -> QueryResult:
    """`Database.fetch_all` on the layer for `role`."""
    return get_database(role).fetch_all(sql, params)
