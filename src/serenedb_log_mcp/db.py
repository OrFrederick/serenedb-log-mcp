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
- the call itself is bounded too: the statements run on a thread of their own, and the caller
  stops waiting `CANCEL_GRACE_S` after the timer sent its cancel request or gave up on sending one.
  A statement that is still running then ends the call with `QueryTimeout`; its thread closes the
  connection once SereneDB returns, because closing it from another thread mid-statement would free
  the libpq connection under it (see `_Statements`);
- only a closed or broken connection counts as lost: SereneDB reports ordinary IO errors (a
  missing `read_csv` file, an unreachable HTTP source) as SQLSTATE `08000` on a healthy session;
- on a lost connection (SereneDB 26.10.1 SIGSEGVs intermittently and restarts in a few seconds) the
  layer waits until `SELECT 1` answers on a new connection, retries the statements once and says
  so in `QueryResult.notes`;
- what SereneDB sends as a WARNING, NOTICE or INFO while the statements run goes into
  `QueryResult.notes`, so a server-side warning reaches the Evidence envelope;
- parameters are always bound, never formatted into the SQL;
- `numeric` results (for example `sum()` and SereneDB's `HUGEINT`) come back as `int` when they are
  integral and as `float` otherwise, never as `Decimal`, in text and in binary format, so the
  Evidence envelope serialises them as JSON numbers.

The layer never builds an `Evidence`; tools hand its `QueryResult` to `toolkit.evidence_tool`.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from psycopg.abc import AdaptContext, Buffer
from psycopg.adapt import Loader
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import Format
from psycopg.types.numeric import NumericBinaryLoader

from serenedb_log_mcp.config import (
    DEFAULT_MAX_IDLE_CONNECTIONS,
    DEFAULT_RECONNECT_WAIT_S,
    Role,
    get_settings,
)
from serenedb_log_mcp.toolkit import ToolContractError

if TYPE_CHECKING:
    from serenedb_log_mcp.toolkit import QueryResultLike  # only for the static conformance check

__all__ = [
    "Database",
    "DatabaseLayerError",
    "DatabaseMisconfigured",
    "DatabaseRestarted",
    "DatabaseUnavailable",
    "IntOrFloatNumericBinaryLoader",
    "IntOrFloatNumericLoader",
    "QueryFailed",
    "QueryResult",
    "QueryTimeout",
    "fetch_all",
    "fetch_limited",
    "get_database",
    "int_or_float",
    "open_connection",
]

CONNECT_TIMEOUT_S = 10
"""How long one connection attempt may take (libpq `connect_timeout`)."""
POLL_INTERVAL_S = 1.0
"""Pause between two `SELECT 1` attempts while SereneDB restarts."""

CANCEL_ATTEMPTS = 3
"""How often the timer sends a cancel request that failed (for example psycopg's CancellationTimeout)."""
CANCEL_RETRY_S = 1.0
"""Pause between two cancel requests."""
CANCEL_TIMEOUT_S = 5.0
"""How long one cancel request may take (psycopg's own default is 30 s)."""
CANCEL_GRACE_S = 5.0
"""How long the caller still waits for the statements once the timer has sent a cancel request or
given up on sending one. 26.10.1 ended a cancelled statement within 1.4 s in every #47 run."""

NOTED_SEVERITIES = frozenset({"WARNING", "NOTICE", "INFO"})
"""Notice severities that reach `QueryResult.notes`; DEBUG and LOG are left out."""
MAX_NOTICE_NOTES = 5
"""Distinct notices listed per call; the rest are counted in one more note."""

NARROW_THE_SCOPE = (
    "Narrow the Data Scope: a shorter time range, a Component, level or Pattern filter, or a coarser "
    "trend bucket."
)

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


class IntOrFloatNumericBinaryLoader(Loader):
    """Loads `numeric` in binary format as `int` or `float`, like `IntOrFloatNumericLoader`.

    The layer reads text format, but a cursor that asks for binary (`binary=True`) on a connection
    from `open_connection` must not bring `Decimal` back either. psycopg's own binary loader decodes
    the value to a `Decimal` first.
    """

    format = Format.BINARY

    def __init__(self, oid: int, context: AdaptContext | None = None) -> None:
        super().__init__(oid, context)
        self._decimal = NumericBinaryLoader(oid, context)

    def load(self, data: Buffer) -> int | float:
        return int_or_float(self._decimal.load(data))


# Errors ---------------------------------------------------------------------------------------------


class DatabaseLayerError(ToolError):
    """Base of the layer's errors. They are `ToolError`s, so their text reaches the agent unchanged."""


class QueryTimeout(DatabaseLayerError):
    """The statements ran longer than `SLM_QUERY_TIMEOUT_S` and were cancelled, or were still running
    `CANCEL_GRACE_S` after the cancel request went out or failed."""


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


NoticeHandler = Callable[[psycopg.errors.Diagnostic], None]


class ConnectionLike(Protocol):
    """The part of a psycopg connection the layer uses; tests pass fakes with the same shape."""

    @property
    def closed(self) -> bool: ...

    @property
    def broken(self) -> bool: ...

    def execute(self, query: str, params: Sequence[Any] | None = None) -> CursorLike: ...

    def cancel_safe(self, *, timeout: float = 30.0) -> None: ...

    def add_notice_handler(self, callback: NoticeHandler) -> None: ...

    def remove_notice_handler(self, callback: NoticeHandler) -> None: ...

    def close(self) -> None: ...


class Connector(Protocol):
    def __call__(self, dsn: str, *, client_binding: bool) -> ConnectionLike: ...


def open_connection(dsn: str, *, client_binding: bool) -> psycopg.Connection[tuple[Any, ...]]:
    """A connection as the layer needs it.

    Autocommit, because the layer issues `BEGIN READ ONLY` and `ROLLBACK` itself and starts the
    timeout only after the transaction began. Server-side binding by default, `ClientCursor` when
    `client_binding` (`SLM_CLIENT_BINDING=1`); both bind every value. No automatic prepared
    statements, which is the path the #2 measurements took. `numeric` loads as int or float, in
    text and in binary format.
    """
    conn = psycopg.connect(
        dsn,
        autocommit=True,
        prepare_threshold=None,
        connect_timeout=CONNECT_TIMEOUT_S,
        cursor_factory=psycopg.ClientCursor if client_binding else psycopg.Cursor,
    )
    conn.adapters.register_loader("numeric", IntOrFloatNumericLoader)
    conn.adapters.register_loader("numeric", IntOrFloatNumericBinaryLoader)
    return conn


class _ConnectionLost(Exception):
    """Internal: the session ended under a statement; the caller waits and retries once."""


class _Abandoned(Exception):
    """Internal: the statements outlived the cancel and the grace period; the caller stopped waiting."""


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
        self._notices: list[str] = []
        self._more_notices = 0

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

    def notice(self, diag: psycopg.errors.Diagnostic) -> None:
        """The notice handler of this attempt: keep what SereneDB warns about, once each."""
        severity = (diag.severity_nonlocalized or diag.severity or "").upper()
        if severity not in NOTED_SEVERITIES:
            return
        message = _sentence(_compact(diag.message_primary or "", limit=300))
        note = f"SereneDB sent this {severity} while the statements ran: {message}"
        if note in self._notices:
            return
        if len(self._notices) < MAX_NOTICE_NOTES:
            self._notices.append(note)
        else:
            self._more_notices += 1

    def notice_notes(self) -> list[str]:
        """The notices for `QueryResult.notes`, at most `MAX_NOTICE_NOTES` plus a count of the rest."""
        if not self._more_notices:
            return list(self._notices)
        more = f"SereneDB sent {self._more_notices} more notices while the statements ran"
        return [*self._notices, f"{more}; they are not listed."]


class _Deadline:
    """The time limit of one attempt.

    A timer cancels the statements after `timeout_s` and retries a cancel request that fails, each
    request bounded by `CANCEL_TIMEOUT_S`. Once a request went out, or `CANCEL_ATTEMPTS` failed, the
    timer has done all it can and calls `on_settled`. `stop()` ends it: once `stop()` has returned,
    no cancel request is in flight and none will be sent, and `fired` says whether one was. A
    connection whose deadline fired is never reused, so a cancel can never reach a later call on a
    pooled connection.
    """

    def __init__(self, conn: ConnectionLike, timeout_s: float, on_settled: Callable[[], None]) -> None:
        self._conn = conn
        self.timeout_s = timeout_s
        self._on_settled = on_settled
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
        for attempt in range(CANCEL_ATTEMPTS):
            if attempt and self._stopped.wait(CANCEL_RETRY_S):
                return
            with self._lock:
                if self._stopped.is_set():
                    return
                self.fired = True
                try:
                    self._conn.cancel_safe(timeout=CANCEL_TIMEOUT_S)
                except psycopg.Error as exc:
                    self.errors.append(_first_line(exc))
                    continue
            break  # the request went out
        self._on_settled()

    @property
    def why_it_ran_on(self) -> str:
        """Why statements outlived the limit, for a note or an error message."""
        if len(self.errors) >= CANCEL_ATTEMPTS:
            return f"the cancel request failed ({self.errors[-1]})"
        if self.fired:
            return "SereneDB did not act on the cancel request"
        return "the time limit did not fire in time"


class _Statements[T]:
    """The statements of one attempt, run on a thread of their own that owns the connection.

    The caller waits for them, but only until `grace_s` after the deadline has done all it can (a
    cancel request went out, or every one failed). Then it gives up with `_Abandoned` and leaves the
    connection alone: psycopg's `close()` does not take the connection lock, so closing it from the
    caller would free the libpq connection under the statement still waiting on it. The statement
    thread closes the connection itself once the statement returns, so an abandoned connection is
    never pooled. The price is that SereneDB may keep running the statement, holding that connection,
    until it ends by itself; the layer cannot stop it once the cancel failed.
    """

    def __init__(self, conn: ConnectionLike, work: Callable[[_Session], T], timeout_s: float):
        self._conn = conn
        self._work = work
        self._wake = threading.Event()  # the statements ended, or the deadline has done all it can
        self._done = threading.Event()
        self._lock = threading.Lock()  # decides who closes the connection: the caller or the thread
        self._started = False
        self._finished = False
        self._abandoned = False
        self._value: tuple[T] | None = None  # a box, so that a work() returning None is told apart
        self._error: BaseException | None = None
        self.deadline = _Deadline(conn, timeout_s, self._wake.set)
        # The session checks the deadline before each statement, on the statement thread: a cancel
        # that reaches the backend between two statements is dropped, so the next one must not run.
        self.session = _Session(conn, self.deadline)

    def start(self) -> None:
        context = contextvars.copy_context()  # the statements run in the context of the call
        thread = threading.Thread(
            target=context.run, args=(self._run,), name="serenedb-statements", daemon=True
        )
        thread.start()
        self._started = True

    def result(self, grace_s: float) -> T:
        """The value of `work`, or its exception; `_Abandoned` once the grace period ran out."""
        self._wake.wait()
        if not self._done.wait(grace_s) and self.abandon():
            raise _Abandoned
        if self._error is not None:
            raise self._error
        assert self._value is not None, "the statement thread ended without a value or an error"
        return self._value[0]

    def abandon(self) -> bool:
        """Leave the connection to the statement thread if it still runs; True when it does."""
        with self._lock:
            if self._started and not self._finished:
                self._abandoned = True
            return self._abandoned

    def _run(self) -> None:
        on_notice = self.session.notice
        try:
            self._conn.add_notice_handler(on_notice)  # removed below, so a pooled connection keeps none
            try:
                self._conn.execute("BEGIN READ ONLY")
                self.deadline.start()  # after BEGIN, so the limit covers only the statements
                try:
                    self._value = (self._work(self.session),)
                finally:
                    self.deadline.stop()
            finally:
                self._conn.remove_notice_handler(on_notice)
        except BaseException as exc:  # handed to the caller, which raises it
            self._error = exc
        finally:
            with self._lock:
                self._finished = True
                abandoned = self._abandoned
            if abandoned:
                _close_quietly(self._conn)  # this thread owns it, and its statement has returned
            self._done.set()
            self._wake.set()


class Database:
    """Runs fixed SQL templates against one DSN: read-only, with a timeout and one retry.

    Thread-safe: each call takes its own connection from a small idle pool. `reconnect_wait_s` bounds
    the wait for a restarting server (`SLM_RECONNECT_WAIT_S`) and `max_idle_connections` caps the idle
    pool (`SLM_MAX_IDLE_CONNECTIONS`; 0 opens a new connection for every call).
    """

    def __init__(
        self,
        dsn: str,
        *,
        timeout_s: float = 15.0,
        client_binding: bool = False,
        reconnect_wait_s: float = DEFAULT_RECONNECT_WAIT_S,
        max_idle_connections: int = DEFAULT_MAX_IDLE_CONNECTIONS,
        poll_interval_s: float = POLL_INTERVAL_S,
        cancel_grace_s: float = CANCEL_GRACE_S,
        connect: Connector = open_connection,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.dsn = dsn
        self.timeout_s = timeout_s
        self.client_binding = client_binding
        self.reconnect_wait_s = reconnect_wait_s
        self.max_idle_connections = max_idle_connections
        self.poll_interval_s = poll_interval_s
        self.cancel_grace_s = cancel_grace_s
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
        statements = _Statements(conn, work, self.timeout_s)
        session, deadline = statements.session, statements.deadline
        try:
            statements.start()
            value = statements.result(self.cancel_grace_s)
        except _Abandoned:
            raise QueryTimeout(
                f"The query ran longer than the {self.timeout_s:g} s limit (SLM_QUERY_TIMEOUT_S) and "
                f"was not stopped, because {deadline.why_it_ran_on}. The database layer stopped "
                f"waiting for it {self.cancel_grace_s:g} s later; the statement may still be running "
                f"on SereneDB until it ends by itself. {NARROW_THE_SCOPE}"
            ) from None
        except psycopg.Error as exc:
            if _is_lost(conn):
                _close_quietly(conn)
                raise _ConnectionLost from exc
            self._end(conn, discard=deadline.fired)
            if deadline.fired and isinstance(exc, psycopg.errors.QueryCanceled):
                raise QueryTimeout(_timeout_message(self.timeout_s)) from exc
            raise _rejected(exc, session.running) from exc
        except BaseException:
            if not statements.abandon():  # otherwise the statement thread closes the connection
                self._end(conn, discard=True)
            raise
        session.notes.extend(session.notice_notes())
        if (note := self._overrun_note(session, deadline)) is not None:
            session.notes.append(note)
        self._end(conn, discard=deadline.fired)
        return value, session

    def _overrun_note(self, session: _Session, deadline: _Deadline) -> str | None:
        """A note when the statements finished although the time limit fired or was exceeded.

        `cancel_safe()` is the only thing that stops a statement at the limit, and it is not always
        honoured: on 26.09.1.3 a bound parameter inside a table function such as `generate_series`
        makes SereneDB ignore the cancel (measured by OrFrederick's Claude in the #47 review;
        26.10.1 honoured every cancel, bound or literal). So every result that comes back is
        checked against the limit; one that did not come back within `CANCEL_GRACE_S` ended the
        call with `QueryTimeout` instead.
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
        return (
            f"The statements ran {seconds:.1f} s, past the {limit}, because {deadline.why_it_ran_on}. "
            "The numbers are complete, but the call took longer than allowed: narrow the Data Scope "
            "(a shorter time range or more filters) before the next call."
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
            if len(self._idle) < self.max_idle_connections:
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
        f"cancelled. {NARROW_THE_SCOPE}"
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
    return _sentence(text.splitlines()[0].strip() if text else type(exc).__name__)


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


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

_databases: dict[tuple[str, float, bool, float, int], Database] = {}
_databases_lock = threading.Lock()


def get_database(role: Role | None = None) -> Database:
    """The layer for one Role: `DATABASE_URL_<ROLE>` when it is set, `DATABASE_URL` otherwise.

    Built from the process-wide settings at call time and kept per connection setting, so the idle
    connections survive between tool calls. `role=None` (scripts) uses `DATABASE_URL`.
    """
    settings = get_settings()
    dsn = settings.database_url if role is None else settings.database_url_for(role)
    key = (
        dsn,
        settings.query_timeout_s,
        settings.client_binding,
        settings.reconnect_wait_s,
        settings.max_idle_connections,
    )
    with _databases_lock:
        layer = _databases.get(key)
        if layer is None:
            layer = Database(
                dsn,
                timeout_s=settings.query_timeout_s,
                client_binding=settings.client_binding,
                reconnect_wait_s=settings.reconnect_wait_s,
                max_idle_connections=settings.max_idle_connections,
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
