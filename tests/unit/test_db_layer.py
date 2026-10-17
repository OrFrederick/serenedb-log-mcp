"""The database layer against fake connections: read-only transactions, limits, timeout and retry.

The fakes model what SereneDB does on the wire: a crash closes the connection in the middle of a
statement (`OperationalError`, the connection is broken afterwards), a restarting server refuses new
connections for a while, and a cancelled statement raises `QueryCanceled` and leaves the
connection usable after a ROLLBACK. The real server is covered by tests/db/test_db_layer_db.py.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp import db
from serenedb_log_mcp.config import Role, load_settings, set_settings
from serenedb_log_mcp.db import (
    Database,
    DatabaseMisconfigured,
    DatabaseRestarted,
    DatabaseUnavailable,
    QueryFailed,
    QueryTimeout,
)
from serenedb_log_mcp.toolkit import ToolContractError

COUNT_SQL = "SELECT count(*) FROM t WHERE a = %s"
ROWS_SQL = "SELECT a, b FROM t WHERE a = %s ORDER BY b LIMIT %s"
CONTROL = {"BEGIN READ ONLY", "ROLLBACK", "SELECT 1"}

Responder = Callable[[str, Sequence[Any] | None], list[tuple[Any, ...]]]


def table_responder(total: int, rows: list[tuple[Any, ...]]) -> Responder:
    def respond(sql: str, params: Sequence[Any] | None) -> list[tuple[Any, ...]]:
        if sql.startswith("SELECT count(*)"):
            return [(total,)]
        limit = params[-1] if params else len(rows)
        return rows[:limit]

    return respond


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeServer:
    """Hands out fake connections and decides when they crash or are refused."""

    def __init__(self, respond: Responder) -> None:
        self.respond = respond
        self.crashes_left = 0
        self.refusals_left = 0
        self.refusal: psycopg.Error = psycopg.OperationalError(
            "connection failed: server closed the connection unexpectedly"
        )
        self.connections: list[FakeConnection] = []
        self.binding: list[bool] = []
        self.cancel_failures_left = 0
        self.interrupt_select_1 = False

    def connect(self, dsn: str, *, client_binding: bool) -> FakeConnection:
        self.binding.append(client_binding)
        if self.refusals_left:
            self.refusals_left -= 1
            raise self.refusal
        conn = FakeConnection(self)
        self.connections.append(conn)
        return conn

    def executed(self) -> list[str]:
        return [sql for conn in self.connections for sql, _ in conn.executed]


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.executed: list[tuple[str, Sequence[Any] | None]] = []
        self.closed = False
        self.broken = False
        self.cancelled = threading.Event()
        self.cancels_while_open = 0

    def execute(self, query: str, params: Sequence[Any] | None = None) -> FakeCursor:
        if self.closed or self.broken:
            raise psycopg.OperationalError("the connection is lost")
        self.executed.append((query, params))
        if query == "SELECT 1" and self.server.interrupt_select_1:
            raise KeyboardInterrupt
        if query in CONTROL:
            return FakeCursor([(1,)] if query == "SELECT 1" else [])
        if self.server.crashes_left:
            self.server.crashes_left -= 1
            self.broken = True
            raise psycopg.OperationalError(
                "consuming input failed: server closed the connection unexpectedly"
            )
        if query == "SELECT slow()":
            if self.cancelled.wait(5):
                self.cancelled.clear()
                raise psycopg.errors.QueryCanceled("canceling statement due to user request")
            return FakeCursor([(1,)])
        if query == "SELECT stubborn()":
            time.sleep(0.2)  # finishes although the timer fired: the cancel arrived too late
            return FakeCursor([(1,)])
        if query == "SELECT * FROM read_csv('missing.csv')":
            # SereneDB reports IO errors as SQLSTATE 08000 on a healthy connection.
            raise psycopg.errors.ConnectionException('No files found that match the pattern "missing.csv"')
        if query == "SELECT * FROM missing":
            raise psycopg.errors.UndefinedTable('relation "missing" does not exist\nLINE 1: ...')
        if query == "SELECT a FROM t WHERE b = %s" and params and "\x00" in str(params[0]):
            raise psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")
        if query == "SELECT 'FAT%' LIMIT %s":
            # psycopg refuses this before anything is sent: a bare % next to a placeholder.
            raise psycopg.ProgrammingError("only '%s', '%b', '%t' are allowed as placeholders, got \"%'\"")
        return FakeCursor(self.server.respond(query, params))

    def cancel_safe(self) -> None:
        if self.closed:
            return  # psycopg's cancel_safe does nothing on a closed connection
        if self.server.cancel_failures_left:
            self.server.cancel_failures_left -= 1
            raise psycopg.errors.CancellationTimeout("cancellation timeout expired")
        self.cancels_while_open += 1
        self.cancelled.set()

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def database(server: FakeServer, clock: FakeClock | None = None, **kwargs: Any) -> Database:
    clock = clock or FakeClock()
    return Database(
        "host=fake",
        connect=server.connect,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **kwargs,
    )


ROWS = [(1, "a"), (1, "b"), (1, "c")]


def test_fetch_limited_runs_count_then_rows_in_one_read_only_transaction() -> None:
    server = FakeServer(table_responder(57, ROWS))

    result = database(server).fetch_limited(COUNT_SQL, ROWS_SQL, [1], 2)

    assert server.executed() == ["BEGIN READ ONLY", COUNT_SQL, ROWS_SQL, "ROLLBACK"]
    assert result.total == 57
    assert result.rows == [(1, "a"), (1, "b")]
    assert result.truncated is True
    assert result.sql == [COUNT_SQL, ROWS_SQL]
    assert result.params == [[1], [1, 2]]
    assert result.notes == []
    assert result.query_ms >= 0


def test_fetch_limited_skips_the_row_query_when_nothing_matches() -> None:
    server = FakeServer(table_responder(0, []))

    result = database(server).fetch_limited(COUNT_SQL, ROWS_SQL, [1], 20)

    assert server.executed() == ["BEGIN READ ONLY", COUNT_SQL, "ROLLBACK"]
    assert result.total == 0
    assert result.rows == []
    assert result.truncated is False
    assert result.sql == [COUNT_SQL]
    assert result.params == [[1]]


def test_fetch_limited_is_not_truncated_when_every_match_is_returned() -> None:
    server = FakeServer(table_responder(3, ROWS))

    result = database(server).fetch_limited(COUNT_SQL, ROWS_SQL, [1], 20)

    assert result.rows == ROWS
    assert result.truncated is False


def test_fetch_limited_accepts_separate_row_parameters() -> None:
    server = FakeServer(table_responder(3, ROWS))
    rows_sql = "SELECT a, ts_highlight(b, %s) FROM t WHERE a = %s LIMIT %s"

    result = database(server).fetch_limited(COUNT_SQL, rows_sql, [1], 2, rows_params=["q", 1, 2])

    assert result.params == [[1], ["q", 1, 2]]


def test_fetch_limited_rejects_a_row_query_that_ignores_the_limit() -> None:
    # The row query has no LIMIT, so the server returns every matching row.
    server = FakeServer(lambda sql, params: [(3,)] if sql.startswith("SELECT count(*)") else ROWS)
    unlimited = "SELECT a, ts_highlight(b, %s) FROM t WHERE a = %s"

    with pytest.raises(ToolContractError, match="limit=2"):
        database(server).fetch_limited(COUNT_SQL, unlimited, [1], 2, rows_params=["q", 1])


@pytest.mark.parametrize("limit", [0, -1])
def test_fetch_limited_rejects_a_limit_below_one(limit: int) -> None:
    with pytest.raises(ToolContractError, match="limit"):
        database(FakeServer(table_responder(1, ROWS))).fetch_limited(COUNT_SQL, ROWS_SQL, [1], limit)


def test_a_count_query_with_the_wrong_shape_is_a_contract_error_that_shows_no_rows() -> None:
    server = FakeServer(lambda sql, params: [("secret log line", 1)])

    with pytest.raises(ToolContractError) as caught:
        database(server).fetch_limited(COUNT_SQL, ROWS_SQL, [1], 5)

    assert "secret log line" not in str(caught.value)


def test_statements_without_parameters_are_sent_without_a_parameter_list() -> None:
    server = FakeServer(lambda sql, params: [("x%",)])

    result = database(server).fetch_all("SELECT 'x%'")

    sent = [params for conn in server.connections for sql, params in conn.executed if sql == "SELECT 'x%'"]
    assert sent == [None]
    assert result.params == [[]]


def test_fetch_all_returns_every_row_as_the_total() -> None:
    server = FakeServer(lambda sql, params: [("2005-06-03", 12), ("2005-06-04", 7)])

    result = database(server).fetch_all("SELECT day, count(*) FROM t WHERE a = %s GROUP BY 1", [1])

    assert result.total == 2
    assert result.rows == [("2005-06-03", 12), ("2005-06-04", 7)]
    assert result.truncated is False
    assert result.params == [[1]]


def test_connections_are_reused_between_calls() -> None:
    server = FakeServer(table_responder(3, ROWS))
    layer = database(server)

    layer.fetch_limited(COUNT_SQL, ROWS_SQL, [1], 20)
    layer.fetch_limited(COUNT_SQL, ROWS_SQL, [1], 20)

    assert len(server.connections) == 1


def test_binding_mode_reaches_the_connector() -> None:
    server = FakeServer(table_responder(3, ROWS))

    database(server, client_binding=True).fetch_all("SELECT a FROM t")

    assert server.binding == [True]


# Lost connections ---------------------------------------------------------------------------------


def test_a_lost_connection_is_retried_once_after_select_1_answers() -> None:
    server = FakeServer(table_responder(57, ROWS))
    server.crashes_left = 1

    result = database(server).fetch_limited(COUNT_SQL, ROWS_SQL, [1], 2)

    assert result.total == 57
    assert result.rows == [(1, "a"), (1, "b")]
    # Only the statements of the successful attempt are recorded.
    assert result.sql == [COUNT_SQL, ROWS_SQL]
    assert len(result.notes) == 1
    assert "restarted" in result.notes[0]
    assert "retried" in result.notes[0]
    assert server.connections[0].broken
    assert ("SELECT 1", None) in server.connections[1].executed


def test_the_retry_waits_while_the_restarting_server_refuses_connections() -> None:
    server = FakeServer(table_responder(57, ROWS))
    clock = FakeClock()
    layer = database(server, clock)
    layer.fetch_all("SELECT a FROM t")  # one pooled connection, which the crash will break
    server.crashes_left, server.refusals_left = 1, 3

    result = layer.fetch_limited(COUNT_SQL, ROWS_SQL, [1], 2)

    assert result.total == 57
    assert len(clock.sleeps) == 3
    assert "3.0 s" in result.notes[0]


def test_a_second_lost_connection_raises_a_tool_error_that_suggests_sort_time() -> None:
    server = FakeServer(table_responder(57, ROWS))
    server.crashes_left = 2

    with pytest.raises(DatabaseRestarted) as caught:
        database(server).fetch_limited(COUNT_SQL, ROWS_SQL, [1], 2)

    assert isinstance(caught.value, ToolError)
    message = str(caught.value)
    assert "restarted" in message
    assert "sort=time" in message
    assert "server closed the connection unexpectedly" in message
    assert isinstance(caught.value.__cause__, psycopg.OperationalError)


def test_an_08000_error_on_a_live_connection_is_a_failed_statement_not_a_restart() -> None:
    server = FakeServer(table_responder(3, ROWS))
    layer = database(server)

    with pytest.raises(QueryFailed) as caught:
        layer.fetch_all("SELECT * FROM read_csv('missing.csv')")

    assert caught.value.sqlstate == "08000"
    assert 'No files found that match the pattern "missing.csv"' in str(caught.value)
    assert server.executed().count("SELECT * FROM read_csv('missing.csv')") == 1
    assert layer.fetch_all("SELECT a FROM t").total == 3
    assert len(server.connections) == 1


def test_the_wait_for_a_restart_is_bounded() -> None:
    server = FakeServer(table_responder(57, ROWS))
    server.refusals_left = 10_000
    clock = FakeClock()

    with pytest.raises(DatabaseUnavailable) as caught:
        database(server, clock, reconnect_wait_s=10.0).fetch_all("SELECT a FROM t")

    assert isinstance(caught.value, ToolError)
    assert clock.now <= 11.0
    assert "SELECT 1" in str(caught.value)
    assert "host=fake" in str(caught.value)


def test_the_unavailable_message_never_shows_a_password() -> None:
    server = FakeServer(table_responder(57, ROWS))
    server.refusals_left = 10_000
    clock = FakeClock()
    layer = Database(
        "host=db port=7890 user=u password=s3cret",
        connect=server.connect,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        reconnect_wait_s=2.0,
    )

    with pytest.raises(DatabaseUnavailable) as caught:
        layer.fetch_all("SELECT a FROM t")

    assert "s3cret" not in str(caught.value)


@pytest.mark.parametrize(
    "dsn",
    [
        "host=db port=7890 dbname=x user=u password=p1 sslpassword=s3cret",
        "postgresql://u:p1@db:7890/x?sslpassword=s3cret",
    ],
)
def test_the_unavailable_message_shows_only_where_it_connected(dsn: str) -> None:
    server = FakeServer(table_responder(57, ROWS))
    server.refusals_left = 10_000
    clock = FakeClock()
    layer = Database(
        dsn, connect=server.connect, sleep=clock.sleep, monotonic=clock.monotonic, reconnect_wait_s=2.0
    )

    with pytest.raises(DatabaseUnavailable) as caught:
        layer.fetch_all("SELECT a FROM t")

    message = str(caught.value)
    assert "host=db port=7890 dbname=x user=u" in message
    assert "s3cret" not in message
    assert "p1" not in message


@pytest.mark.parametrize(
    "refusal",
    [
        psycopg.ProgrammingError('invalid connection option "hostx"'),
        psycopg.OperationalError(
            'connection failed: connection to server at "127.0.0.1", port 7890 failed: FATAL:  role '
            '"nobody" does not exist'
        ),
        psycopg.OperationalError(
            'connection failed: connection to server at "127.0.0.1", port 7890 failed: FATAL:  database '
            '"nope" is not accessible'
        ),
    ],
)
def test_a_misconfigured_dsn_fails_at_once_and_says_so(refusal: psycopg.Error) -> None:
    server = FakeServer(table_responder(57, ROWS))
    server.refusals_left = 10_000
    server.refusal = refusal
    clock = FakeClock()

    with pytest.raises(DatabaseMisconfigured) as caught:
        database(server, clock).fetch_all("SELECT a FROM t")

    assert isinstance(caught.value, DatabaseUnavailable)
    assert clock.sleeps == []
    assert "DATABASE_URL" in str(caught.value)
    assert "restarting" not in str(caught.value)


# Timeouts and other errors --------------------------------------------------------------------------


def test_a_slow_statement_is_cancelled_and_the_connection_stays_usable() -> None:
    server = FakeServer(table_responder(3, ROWS))
    layer = database(server, timeout_s=0.05)

    started = time.monotonic()
    with pytest.raises(QueryTimeout) as caught:
        layer.fetch_all("SELECT slow()")
    elapsed = time.monotonic() - started

    assert isinstance(caught.value, ToolError)
    assert "0.05 s" in str(caught.value)
    assert elapsed < 2
    # A connection whose time limit fired is never reused, so no cancel can reach a later call.
    assert server.connections[0].closed
    result = layer.fetch_limited(COUNT_SQL, ROWS_SQL, [1], 20)
    assert result.total == 3


class ManualTimer:
    """Stands in for threading.Timer so a test can choose when the callback runs.

    Like threading.Timer, `cancel()` only stops a callback that has not started yet.
    """

    instances: ClassVar[list[ManualTimer]] = []

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.function = function
        self.daemon = False
        self.cancelled = False
        self.entered = threading.Event()
        self.proceed = threading.Event()
        self.thread: threading.Thread | None = None
        ManualTimer.instances.append(self)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.cancelled = True

    def fire_and_pause(self) -> None:
        """Run the callback in its own thread; it pauses right after it has been entered."""

        def run() -> None:
            if self.cancelled:
                return
            self.entered.set()
            self.proceed.wait(5)
            self.function()

        self.thread = threading.Thread(target=run)
        self.thread.start()
        self.entered.wait(5)


def test_a_fired_limit_stops_the_call_before_the_next_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancel that reaches a backend idle between two statements is dropped by the server."""
    monkeypatch.setattr(threading, "Timer", ManualTimer)
    ManualTimer.instances.clear()

    def respond(sql: str, params: Sequence[Any] | None) -> list[tuple[Any, ...]]:
        if sql == COUNT_SQL:
            timer = ManualTimer.instances[-1]
            timer.fire_and_pause()
            timer.proceed.set()
            assert timer.thread is not None
            timer.thread.join(5)  # the limit fires as the count finishes
            return [(57,)]
        return ROWS

    server = FakeServer(respond)

    with pytest.raises(QueryTimeout):
        database(server, timeout_s=1).fetch_limited(COUNT_SQL, ROWS_SQL, [1], 2)

    assert ROWS_SQL not in server.executed()
    assert server.connections[0].closed


def test_a_cancel_that_starts_as_the_statements_finish_never_reaches_a_pooled_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threading, "Timer", ManualTimer)
    ManualTimer.instances.clear()

    def respond(sql: str, params: Sequence[Any] | None) -> list[tuple[Any, ...]]:
        # The timer callback is entered while the statement returns its rows.
        ManualTimer.instances[-1].fire_and_pause()
        return [(1,)]

    server = FakeServer(respond)
    layer = database(server)

    assert layer.fetch_all("SELECT a FROM t").rows == [(1,)]
    timer = ManualTimer.instances[-1]
    timer.proceed.set()
    assert timer.thread is not None
    timer.thread.join(5)

    conn = server.connections[0]
    assert conn.closed or conn.cancels_while_open == 0


def test_a_failed_cancel_request_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "CANCEL_RETRY_S", 0.01)
    server = FakeServer(table_responder(3, ROWS))
    server.cancel_failures_left = 1

    with pytest.raises(QueryTimeout):
        database(server, timeout_s=0.02).fetch_all("SELECT slow()")


def test_a_time_limit_that_could_not_be_enforced_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "CANCEL_RETRY_S", 0.01)
    server = FakeServer(table_responder(3, ROWS))
    server.cancel_failures_left = 99

    result = database(server, timeout_s=0.02).fetch_all("SELECT stubborn()")

    assert result.rows == [(1,)]
    assert server.cancel_failures_left == 99 - db.CANCEL_ATTEMPTS
    assert len(result.notes) == 1
    assert "the cancel request failed" in result.notes[0]
    assert "cancellation timeout expired" in result.notes[0]


def test_the_probe_connection_is_closed_when_the_wait_is_interrupted() -> None:
    server = FakeServer(table_responder(3, ROWS))
    server.crashes_left = 1
    server.interrupt_select_1 = True

    with pytest.raises(KeyboardInterrupt):
        database(server).fetch_all("SELECT a FROM t")

    probe = server.connections[1]
    assert probe.closed


def test_a_connection_whose_cancel_came_too_late_is_not_reused() -> None:
    server = FakeServer(table_responder(3, ROWS))
    layer = database(server, timeout_s=0.02)

    assert layer.fetch_all("SELECT stubborn()").rows == [(1,)]

    assert server.connections[0].closed
    layer.fetch_all("SELECT a FROM t")
    assert len(server.connections) == 2


def test_a_statement_that_ignored_the_cancel_carries_an_overrun_note() -> None:
    server = FakeServer(table_responder(3, ROWS))

    result = database(server, timeout_s=0.02).fetch_all("SELECT stubborn()")

    assert result.rows == [(1,)]
    assert len(result.notes) == 1
    note = result.notes[0]
    assert "past the 0.02 s limit" in note
    assert "did not act on the cancel request" in note
    assert "complete" in note
    assert "narrow the Data Scope" in note


def test_a_statement_that_ran_past_the_limit_is_noted_even_if_the_timer_never_fired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threading, "Timer", ManualTimer)  # never fires

    result = database(FakeServer(table_responder(3, ROWS)), timeout_s=0.02).fetch_all("SELECT stubborn()")

    assert len(result.notes) == 1
    assert "past the 0.02 s limit" in result.notes[0]


def test_a_statement_that_finished_as_the_limit_fired_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(threading, "Timer", ManualTimer)
    ManualTimer.instances.clear()

    def respond(sql: str, params: Sequence[Any] | None) -> list[tuple[Any, ...]]:
        timer = ManualTimer.instances[-1]
        timer.fire_and_pause()
        timer.proceed.set()
        assert timer.thread is not None
        timer.thread.join(5)  # the cancel went out while the statement was still running
        return [(1,)]

    result = database(FakeServer(respond), timeout_s=15).fetch_all("SELECT a FROM t")

    assert len(result.notes) == 1
    assert "finished just as the 15 s limit" in result.notes[0]


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_failing_cancel_request_does_not_escape_the_timer_thread() -> None:
    server = FakeServer(table_responder(3, ROWS))
    server.cancel_failures_left = 99
    layer = database(server, timeout_s=0.02)

    assert layer.fetch_all("SELECT stubborn()").rows == [(1,)]

    assert server.connections[0].closed
    assert layer.fetch_all("SELECT a FROM t").total == 3


def test_a_value_psycopg_cannot_send_is_reported_as_an_input_error() -> None:
    server = FakeServer(table_responder(3, ROWS))

    with pytest.raises(QueryFailed) as caught:
        database(server).fetch_all("SELECT a FROM t WHERE b = %s", ["bad\x00value"])

    message = str(caught.value)
    assert "template" not in message
    assert "NUL" in message
    assert "value" in message


def test_a_statement_psycopg_refuses_is_reported_as_a_template_bug() -> None:
    server = FakeServer(table_responder(3, ROWS))

    with pytest.raises(QueryFailed) as caught:
        database(server).fetch_all("SELECT 'FAT%' LIMIT %s", [5])

    message = str(caught.value)
    assert caught.value.sqlstate is None
    assert "SereneDB rejected" not in message
    assert "describe_corpus" not in message
    assert "template" in message
    assert "only '%s', '%b', '%t' are allowed as placeholders" in message


def test_query_failed_names_the_statement_that_failed() -> None:
    server = FakeServer(table_responder(3, ROWS))

    with pytest.raises(QueryFailed) as caught:
        database(server).fetch_limited(COUNT_SQL, "SELECT * FROM missing", [1], 5)

    assert caught.value.sql == "SELECT * FROM missing"
    assert "SELECT * FROM missing" in str(caught.value)


def test_a_rejected_statement_raises_query_failed_without_a_retry() -> None:
    server = FakeServer(table_responder(3, ROWS))
    layer = database(server)

    with pytest.raises(QueryFailed) as caught:
        layer.fetch_all("SELECT * FROM missing")

    assert isinstance(caught.value, ToolError)
    assert 'relation "missing" does not exist' in str(caught.value)
    assert "LINE 1" not in str(caught.value)
    assert caught.value.sqlstate == "42P01"
    assert server.executed().count("SELECT * FROM missing") == 1
    assert server.executed()[-1] == "ROLLBACK"
    assert layer.fetch_all("SELECT a FROM t").total == 3
    assert len(server.connections) == 1


# Per-Role connections -------------------------------------------------------------------------------


def test_get_database_uses_the_role_dsn_when_it_is_set() -> None:
    set_settings(load_settings({"DATABASE_URL": "host=shared", "DATABASE_URL_MANAGER": "host=mgr"}))

    assert db.get_database(Role.MANAGER).dsn == "host=mgr"
    assert db.get_database(Role.ENGINEER).dsn == "host=shared"
    assert db.get_database(None).dsn == "host=shared"


def test_get_database_reads_timeout_and_binding_from_settings() -> None:
    set_settings(load_settings({"SLM_QUERY_TIMEOUT_S": "2.5", "SLM_CLIENT_BINDING": "1"}))

    layer = db.get_database(Role.ENGINEER)

    assert layer.timeout_s == 2.5
    assert layer.client_binding is True


def test_get_database_returns_one_layer_per_connection_setting() -> None:
    set_settings(load_settings({"DATABASE_URL": "host=shared"}))

    assert db.get_database(Role.ENGINEER) is db.get_database(Role.TEAM_LEAD)


def test_a_call_can_ask_for_a_longer_timeout_than_the_default() -> None:
    # get_trace (T21): a cold trace lookup reads remote row groups for longer than SLM_QUERY_TIMEOUT_S.
    set_settings(load_settings({"DATABASE_URL": "host=shared", "SLM_QUERY_TIMEOUT_S": "15"}))

    longer = db.get_database(Role.ENGINEER, timeout_s=120.0)

    assert longer.timeout_s == 120.0
    assert db.get_database(Role.ENGINEER).timeout_s == 15.0
    assert longer is db.get_database(Role.ENGINEER, timeout_s=120.0)
