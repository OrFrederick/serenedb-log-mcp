"""Re-run an Evidence Ledger and check that its numbers still hold (T13, decisions.md "Verification").

`replay(records, investigation)` re-runs every record of an Evidence Ledger and, when it is given the
answers of the investigation, checks every number in them with `verify.check_answer`, the checks
`verify_answer` runs. `scripts/replay_ledger.py` is the command line around it.

How one record is replayed: the record's tool is called again, in-process, with the Role and the
arguments the record stored, and every statement the tool issues runs through the database layer as
usual (read-only, timeout, one retry). A wrapped connection (`db.set_connector`) recognises each of
the record's stored statements by its SQL text and parameters and binds the parameters exactly as the
Evidence Ledger stored them, as JSON (a TIMESTAMP is its ISO string). So the statements that run are
the stored statements with the stored parameters, which is what a reader copying them out of the
report would run, and the tool's own code turns their results into the envelope again. Statements a
record does not hold (the facts a tool reads to validate its inputs) run unchanged.

The fresh envelope is then compared with the stored one:
- `sql` and `params` must be the same, otherwise the current tools run other SQL than the record
  (`sql_changed`, `params_changed`) and the ledger needs recording again;
- `total_matches`, `returned`, the Data Scope and the summary for every record;
- `columns` and `rows` only for results not sorted by relevance: Log Entries with equal BM25 scores
  come in no fixed order (plan section 2.4), so another run may return other rows with the same
  score, while total_matches does not change.
Numbers compare exactly, except floats, which may differ in the last bits of their precision.

The replay never writes: the fresh envelopes go to an in-memory Evidence Ledger, whatever
LEDGER_PATH says, and the process state (settings, ledger, connector) is restored afterwards.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from pydantic_core import to_jsonable_python

from serenedb_log_mcp import config, db, ledger
from serenedb_log_mcp.corpus import facts
from serenedb_log_mcp.db import ConnectionLike, Connector, CursorLike
from serenedb_log_mcp.export.answers import Investigation
from serenedb_log_mcp.verify import INCIDENT_TOOLS, check_answer

__all__ = [
    "AnswerReplay",
    "Mismatch",
    "RecordReplay",
    "ReplayReport",
    "StoredStatements",
    "compare_records",
    "replay",
    "sorted_by_relevance",
]

Record = Mapping[str, Any]
ServerFactory = Callable[[], MCPServer]

REL_TOL = 1e-9
"""Relative tolerance for floats: SereneDB may sum floats in another order on another run."""

_BM25_ORDER = re.compile(r"\bORDER\s+BY\s+BM25\(", re.IGNORECASE)
_SHOWN_CHARS = 160


@dataclass(frozen=True)
class Mismatch:
    """One way a record or an answer no longer holds."""

    kind: str
    detail: str
    evidence_id: str | None = None
    answer: int | None = None
    """The 1-based number of the answer, for a problem in an answer."""


@dataclass(frozen=True)
class RecordReplay:
    """What replaying one Evidence record found."""

    evidence_id: str
    tool: str
    total_matches: Any
    statements: int
    """How many of the record's stored statements ran with their stored parameters."""
    rows_compared: bool
    mismatches: list[Mismatch] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerReplay:
    """What checking one answer against the Evidence Ledger found."""

    number: int
    role: str
    question: str
    numbers: int
    cited: list[str]
    mismatches: list[Mismatch] = field(default_factory=list)


@dataclass(frozen=True)
class ReplayReport:
    """The replay of a ledger and, optionally, of the answers that cite it."""

    records: list[RecordReplay]
    answers: list[AnswerReplay]

    @property
    def mismatches(self) -> list[Mismatch]:
        found = [m for item in self.records for m in item.mismatches]
        return found + [m for answer in self.answers for m in answer.mismatches]

    @property
    def ok(self) -> bool:
        return not self.mismatches


def sorted_by_relevance(record: Record) -> bool:
    """Whether the record's rows are in BM25 order, where equal scores come in no fixed order."""
    arguments = record.get("arguments") or {}
    return arguments.get("sort") == "relevance" or any(
        _BM25_ORDER.search(str(statement)) for statement in record.get("sql") or []
    )


class StoredStatements:
    """The statements of the record being replayed, and which of them ran as stored."""

    def __init__(self) -> None:
        self._statements: list[tuple[str, list[Any]]] = []
        self.ran: set[int] = set()
        self._lock = threading.Lock()

    def load(self, record: Record) -> None:
        sql = [str(s) for s in record.get("sql") or []]
        params = list(record.get("params") or [])
        with self._lock:
            self._statements = [(s, list(params[i]) if i < len(params) else []) for i, s in enumerate(sql)]
            self.ran = set()

    def bind(self, query: str, params: Sequence[Any] | None) -> Sequence[Any] | None:
        """The parameters to run `query` with: the stored ones when it is a stored statement."""
        values = to_jsonable_python(list(params or ()))
        with self._lock:
            for index, (sql, stored) in enumerate(self._statements):
                if sql == query and stored == values:
                    self.ran.add(index)
                    # No parameter list at all when there are none, so a literal % stays a literal
                    # (db.QueryResult: a statement without parameters runs with None).
                    return list(stored) or None
        return params


class _StoredParamsConnection:
    """A connection that runs a record's stored statements with the parameters as stored."""

    def __init__(self, conn: ConnectionLike, statements: StoredStatements) -> None:
        self._conn = conn
        self._statements = statements

    @property
    def closed(self) -> bool:
        return self._conn.closed

    @property
    def broken(self) -> bool:
        return self._conn.broken

    def execute(self, query: str, params: Sequence[Any] | None = None) -> CursorLike:
        return self._conn.execute(query, self._statements.bind(query, params))

    def cancel_safe(self) -> None:
        self._conn.cancel_safe()

    def close(self) -> None:
        self._conn.close()


def replay(
    records: Sequence[Record],
    investigation: Investigation | None = None,
    *,
    server_factory: ServerFactory | None = None,
    connect: Connector | None = None,
) -> ReplayReport:
    """Re-run every record of `records` and check the answers of `investigation` against them.

    `server_factory` builds the server whose tools re-run the records (default: the product server);
    `connect` opens the real connections (default: `db.open_connection`).
    """
    statements = StoredStatements()
    base = connect or db.open_connection

    def stored_params(dsn: str, *, client_binding: bool) -> ConnectionLike:
        return _StoredParamsConnection(base(dsn, client_binding=client_binding), statements)

    with _replaying(stored_params):
        if server_factory is None:
            from serenedb_log_mcp.server import create_server  # the server imports this package

            server_factory = create_server
        server = server_factory()
        offered = {tool.name for tool in anyio.run(server.list_tools)}
        replayed = [_replay_record(server, offered, statements, record) for record in records]
    answers = (
        _check_answers(investigation, records, incident_tools=bool(offered & INCIDENT_TOOLS))
        if investigation is not None
        else []
    )
    return ReplayReport(records=replayed, answers=answers)


@contextmanager
def _replaying(connect: Connector) -> Iterator[None]:
    """An in-memory Evidence Ledger, no Role pin and the wrapped connections; all restored afterwards."""
    settings = config.get_settings()
    config.set_settings(settings.model_copy(update={"ledger_path": None, "role": None}))
    ledger.reset_ledger()
    previous = db.set_connector(connect)
    try:
        yield
    finally:
        db.set_connector(previous)
        config.set_settings(settings)
        ledger.reset_ledger()
        facts.clear_cache()  # the facts read during the replay came through the wrapped connections


def _replay_record(
    server: MCPServer, offered: set[str], statements: StoredStatements, record: Record
) -> RecordReplay:
    evidence_id = str(record.get("evidence_id"))
    tool = str(record.get("tool"))

    def result(statements_ran: int, *mismatches: Mismatch) -> RecordReplay:
        return RecordReplay(
            evidence_id=evidence_id,
            tool=tool,
            total_matches=record.get("total_matches"),
            statements=statements_ran,
            rows_compared=not sorted_by_relevance(record),
            mismatches=list(mismatches),
        )

    if tool not in offered:
        return result(
            0,
            Mismatch(
                "not_replayable",
                f"this server offers no tool {tool!r}, so {evidence_id} cannot be re-run",
                evidence_id,
            ),
        )
    arguments = {"role": record.get("role"), **(record.get("arguments") or {})}
    facts.clear_cache()  # so a tool that serves facts from the cache (describe_corpus) runs them again
    statements.load(record)
    scratch = ledger.get_ledger()
    before = len(scratch.all())
    try:
        outcome = anyio.run(lambda: server.call_tool(tool, arguments))
    except Exception as exc:  # the SDK raises ToolError for arguments that no longer validate
        return result(len(statements.ran), Mismatch("call_failed", _first_line(str(exc)), evidence_id))
    if getattr(outcome, "is_error", False):
        content = getattr(outcome, "content", None) or []
        text = getattr(content[0], "text", "") if content else ""
        return result(len(statements.ran), Mismatch("call_failed", _first_line(text), evidence_id))
    produced = scratch.all()[before:]
    if len(produced) != 1:
        return result(
            len(statements.ran),
            Mismatch(
                "call_failed", f"the call stored {len(produced)} Evidence records instead of 1", evidence_id
            ),
        )
    return result(len(statements.ran), *compare_records(record, produced[0]))


def compare_records(stored: Record, fresh: Record) -> list[Mismatch]:
    """How `fresh`, the record replayed, differs from `stored`, the record in the ledger."""
    evidence_id = str(stored.get("evidence_id"))
    found: list[Mismatch] = []

    def add(kind: str, detail: str | None) -> None:
        if detail is not None:
            found.append(Mismatch(kind, detail, evidence_id))

    sql_then, sql_now = list(stored.get("sql") or []), list(fresh.get("sql") or [])
    if sql_then != sql_now:
        add("sql_changed", _statement_difference(sql_then, sql_now))
    else:
        add("params_changed", _difference(stored.get("params"), fresh.get("params"), "params"))
    for name, kind in (("total_matches", "total_changed"), ("returned", "returned_changed")):
        if _difference(stored.get(name), fresh.get(name), name) is not None:
            add(kind, f"{name} recorded {_show(stored.get(name))}, now {_show(fresh.get(name))}")
    add("scope_changed", _difference(stored.get("data_scope"), fresh.get("data_scope"), "data_scope"))
    add("summary_changed", _difference(stored.get("summary"), fresh.get("summary"), "summary"))
    if not sorted_by_relevance(stored):
        add(
            "rows_changed",
            _difference(stored.get("columns"), fresh.get("columns"), "columns")
            or _difference(stored.get("rows"), fresh.get("rows"), "rows"),
        )
    return found


def _check_answers(
    investigation: Investigation, records: Sequence[Record], *, incident_tools: bool
) -> list[AnswerReplay]:
    checked = []
    for number, answer in enumerate(investigation.answers, start=1):
        report = check_answer(answer.role, answer.answer, records, incident_tools=incident_tools)
        mismatches = [
            Mismatch(problem.kind, f"{problem.section}: {problem.text} ({problem.hint})", answer=number)
            for problem in report.problems
        ]
        checked.append(
            AnswerReplay(
                number=number,
                role=answer.role.value,
                question=answer.question,
                numbers=len(report.checked_numbers),
                cited=list(report.cited_evidence),
                mismatches=mismatches,
            )
        )
    return checked


# Comparing values ---------------------------------------------------------------------------------------


def _difference(then: Any, now: Any, path: str) -> str | None:
    """Where `now` first differs from `then`, as one line, or None when they are the same."""
    if isinstance(then, Mapping) and isinstance(now, Mapping):
        for key in dict.fromkeys([*then, *now]):
            where = f"{path}.{key}"
            if key not in then:
                return f"{where} is new: now {_show(now[key])}"
            if key not in now:
                return f"{where} is gone: recorded {_show(then[key])}"
            if (found := _difference(then[key], now[key], where)) is not None:
                return found
        return None
    if isinstance(then, list) and isinstance(now, list):
        if len(then) != len(now):
            return f"{path} has {len(now)} items now, {len(then)} recorded"
        for index, (a, b) in enumerate(zip(then, now, strict=True)):
            if (found := _difference(a, b, f"{path}[{index}]")) is not None:
                return found
        return None
    return None if _same_value(then, now) else f"{path}: recorded {_show(then)}, now {_show(now)}"


def _same_value(then: Any, now: Any) -> bool:
    if isinstance(then, bool) or isinstance(now, bool):
        return type(then) is type(now) and then == now
    if isinstance(then, int | float) and isinstance(now, int | float):
        return then == now or math.isclose(then, now, rel_tol=REL_TOL, abs_tol=REL_TOL)
    return bool(then == now)


def _statement_difference(then: list[str], now: list[str]) -> str:
    for index in range(max(len(then), len(now))):
        a = then[index] if index < len(then) else None
        b = now[index] if index < len(now) else None
        if a != b:
            return (
                f"statement {index + 1} differs: recorded {_compact(a)}, now {_compact(b)}. The current "
                "tools run other SQL than this record: check that SLM_SCHEMA names the schema in the "
                "recorded SQL, otherwise record the ledger again with the current tools."
            )
    return "the statements differ"


def _compact(statement: str | None) -> str:
    if statement is None:
        return "(none)"
    text = " ".join(statement.split())
    return text if len(text) <= _SHOWN_CHARS else text[: _SHOWN_CHARS - 1] + "…"


def _show(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _SHOWN_CHARS else text[: _SHOWN_CHARS - 1] + "…"


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else "the call failed without a message"
