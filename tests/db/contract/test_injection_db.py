"""Injection strings are inert (T15): they match nothing or raise a ToolError, never an SQL error.

Every free-text parameter (a string or a list of strings that is not an enum) of every tool in
tools/list gets each string in `INJECTIONS`, in the tool's first representative call
(tests/support/contract_calls.py). Each string starts with a word no Log Entry contains, so a filter
that treats it as data matches nothing, while one that let it change the SQL would match rows or
fail. A call must then either succeed with nothing matched, or fail with a `ToolError`; the database
layer must never have reported an SQL error (a statement SereneDB rejected, or a template psycopg
could not send). A NUL byte is the one value psycopg refuses to encode; that is a ToolError with a
fix hint and no SQL is sent, so it passes.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from contract_calls import (
    REPRESENTATIVE_CALLS,
    WRITING_TOOLS,
    branches,
    free_string_parameters,
    representative_calls,
)
from contract_session import Outcome, listing, run_session
from tool_contract import EVIDENCE_FIELDS

from serenedb_log_mcp import db

INJECTIONS = (
    "zqxj'; DROP TABLE zqxj_t; --",
    "zqxj'OR'1'='1",
    'zqxj"); SELECT version(); --',
    "zqxj' UNION SELECT * FROM pg_catalog.pg_tables --",
    "zqxj %s %(x)s {x} {} $1 ?",
    "zqxj */ -- \\' \\\" ;",
    "zqxj 1-2-3 R30-M0-N9",
    "zqxj\x00nul",
)

COMPANIONS: dict[str, dict[str, str]] = {
    "baseline_from": {"baseline_to": "2017-05-16T00:10:00"},
    "baseline_to": {"baseline_from": "2017-05-16T00:00:00"},
}
"""A valid partner, so the injected half of a pair reaches its parser instead of the "give both"
check."""

MATCHED_COUNTS = ("total", "entries_total", "requests_total", "instances_total")
"""Summary keys that count the matched units when total_matches counts something else (trend's
buckets)."""


def cases() -> list[tuple[str, str]]:
    tools = listing()
    return [
        (name, parameter)
        for name in sorted(tools)
        if name not in WRITING_TOOLS or name in REPRESENTATIVE_CALLS
        for parameter in free_string_parameters(tools[name].input_schema)
    ]


def injected(name: str, parameter: str, value: str) -> dict[str, Any]:
    tool = listing()[name]
    base = (representative_calls(name, tool.input_schema) or [{}])[0]
    is_list = any(
        option.get("type") == "array" for option in branches(tool.input_schema["properties"][parameter])
    )
    companions = {
        k: v for k, v in COMPANIONS.get(parameter, {}).items() if k in tool.input_schema["properties"]
    }
    return {**base, **companions, "role": "engineer", parameter: [value] if is_list else value}


def matched_nothing(record: dict[str, Any]) -> bool:
    if record["total_matches"] == 0:
        return True
    return any(record["summary"].get(key) == 0 for key in MATCHED_COUNTS)


ANY_WORD_TOOLS = frozenset({"search_patterns"})
"""Tools that rank by any of the query's words (T14), so an injection string may match on its own
words ('select', 'version'); for them the check is that exactly those words were searched."""


def verdict(outcome: Outcome, name: str = "", value: str = "") -> str | None:
    """What is wrong with one injected call's result, or None."""
    if outcome.error is not None:
        return None  # an error result carries a ToolError's text; SQL errors are caught by the spy
    record = outcome.data or {}
    if set(record) != EVIDENCE_FIELDS:
        return None  # no query behind it (verify_answer): the string is only text
    if name in ANY_WORD_TOOLS:
        foreign = sorted(w for w in record["summary"].get("words") or [] if w not in value.lower())
        if not foreign:
            return None
        return f"searched words that are not in the string: {foreign}"
    if not matched_nothing(record):
        return f"matched {record['total_matches']} units ({record['summary']})"
    return None


@pytest.mark.usefixtures("sample")
@pytest.mark.parametrize(("name", "parameter"), cases())
def test_injection_strings_match_nothing_or_raise_a_tool_error(
    name: str, parameter: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql_errors: list[str] = []
    rejected = db._rejected

    def spy(exc: psycopg.Error, sql: str | None) -> db.QueryFailed:
        if exc.sqlstate is not None or not isinstance(exc, psycopg.DataError):
            sql_errors.append(
                f"{exc.sqlstate}: {str(exc).strip().splitlines()[0] if str(exc).strip() else exc!r}"
            )
        return rejected(exc, sql)

    monkeypatch.setattr(db, "_rejected", spy)

    _, outcomes = run_session([(name, injected(name, parameter, value)) for value in INJECTIONS])

    assert sql_errors == [], f"{name}.{parameter} reached SereneDB as SQL: {sql_errors}"
    wrong = {
        repr(value): problem
        for value, o in zip(INJECTIONS, outcomes, strict=True)
        if (problem := verdict(o, name, value))
    }
    assert wrong == {}, f"{name}.{parameter}: an injection string changed the result: {wrong}"
