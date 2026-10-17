#!/usr/bin/env python3
"""Access control per Role (T22): per-Role views, LOGIN roles and GRANTs, then a check as each login.

Usage (see `make help`):
    uv run python scripts/access.py            # (re)create everything, then check it
    uv run python scripts/access.py --check    # only check, as each login
    uv run python scripts/access.py --drop     # drop the per-Role views and the LOGIN roles

Creating runs, as the superuser of DATABASE_URL: `drop_statements` (the views and LOGIN roles of an
earlier run), then sql/access/roles.sql. The check then connects as each Role's login and reports
what it may read: its own views (and, for the Roles that search text, its own full-text index), and
not the canonical tables, the canonical indexes or the other Roles' views. It also tries a wrong
password, because a server that trusts every client (the dev container runs with
SERENEDB_HOST_AUTH_METHOD=trust) lets anyone log in as any role.

The grants are defence in depth: any login can still read server files (`read_csv`, `read_text`,
`glob`), so the Role-scoped tools stay the guarantee (docs/access-control.md).

Environment:
    DATABASE_URL                 a superuser connection, which creates the roles and views
    SLM_SCHEMA                   the derived schema (default slm); the views go into <SLM_SCHEMA>_<role>
    DATABASE_URL_ENGINEER, DATABASE_URL_TEAM_LEAD, DATABASE_URL_MANAGER
                                 the connection each Role's tool calls use: its user= is the LOGIN
                                 role to create and its password= the password. The user must be a
                                 dedicated role named slm_..., for example slm_manager, because this
                                 script drops and re-creates it.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from derive import DeriveError, render, split_statements
from serenedb_log_mcp.config import SCHEMA_RE, ConfigError, Role, Settings, load_settings
from serenedb_log_mcp.corpus.datasets import ROLE_WITHHELD_COLUMNS, role_schema

ROLES_SQL = Path(__file__).resolve().parent.parent / "sql" / "access" / "roles.sql"
CONNECT_TIMEOUT_S = 10
LOGIN_RE = re.compile(r"^slm_[a-z0-9_]*[a-z0-9]$")
PASSWORD_RE = re.compile(r"PASSWORD\s+'(?:[^']|'')*'")
DENIED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedColumn)
"""What a refused read raises: no privilege on the relation, or a column the view does not have."""


class AccessError(Exception):
    """The configuration or the server does not allow access control per Role as configured."""


@dataclass(frozen=True)
class Login:
    """The LOGIN role one Role's tool calls connect as."""

    role: Role
    user: str
    password: str = field(repr=False)
    dsn: str = field(repr=False)

    @property
    def env(self) -> str:
        return f"DATABASE_URL_{self.role.value.upper()}"


def logins(settings: Settings) -> dict[Role, Login]:
    """Each Role's login from its DATABASE_URL_<ROLE>; AccessError when one is missing or unsafe."""
    admin = conninfo_to_dict(settings.database_url).get("user")
    found: dict[Role, Login] = {}
    for role in Role:
        env = f"DATABASE_URL_{role.value.upper()}"
        dsn = settings.role_database_urls.get(role)
        if dsn is None:
            raise AccessError(
                f"{env} is not set. Set DATABASE_URL_ENGINEER, DATABASE_URL_TEAM_LEAD and "
                "DATABASE_URL_MANAGER, each with the user= and password= of that Role's login, for "
                "example 'host=localhost port=7890 dbname=postgres user=slm_manager password=...'."
            )
        parts = conninfo_to_dict(dsn)
        user, password = parts.get("user"), parts.get("password")
        if not isinstance(user, str) or not LOGIN_RE.fullmatch(user):
            raise AccessError(
                f"{env} names user={user!r}; this script drops and re-creates that LOGIN role, so it must "
                f"be a dedicated role named slm_..., for example user=slm_{role.value}."
            )
        if user == admin:
            raise AccessError(
                f"{env} names user={user!r}, the administrator of DATABASE_URL; each Role needs a "
                "login of its own."
            )
        if not isinstance(password, str) or not password:
            raise AccessError(f"{env} has no password=; the LOGIN role {user} needs one.")
        for other in found.values():
            if other.user == user:
                raise AccessError(f"{other.env} and {env} both name user={user}; each Role needs its own.")
        found[role] = Login(role, user, password, dsn)
    return found


def _ident(name: str) -> str:
    return sql.Identifier(name).as_string(None)


def _schema(name: str) -> str:
    if not SCHEMA_RE.fullmatch(name):
        raise AccessError(f"schema {name!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
    return _ident(name)


def variables(slm: str, found: Mapping[Role, Login]) -> dict[str, str]:
    """The placeholder values of sql/access/roles.sql."""
    values = {"slm": _schema(slm)}
    for role, login in found.items():
        values[f"{role.value}_schema"] = _schema(role_schema(slm, role))
        values[f"{role.value}_login"] = _ident(login.user)
        values[f"{role.value}_password"] = sql.Literal(login.password).as_string(None)
    return values


def statements(slm: str, found: Mapping[Role, Login], path: Path = ROLES_SQL) -> list[str]:
    """sql/access/roles.sql, rendered and split into statements."""
    return split_statements(render(path.read_text(), variables(slm, found)))


def drop_statements(slm: str, found: Mapping[Role, Login]) -> list[str]:
    """Drop the per-Role views (their schemas) and then the LOGIN roles, which then own nothing."""
    schemas = [f"DROP SCHEMA IF EXISTS {_schema(role_schema(slm, role))} CASCADE" for role in found]
    roles = [f"DROP ROLE IF EXISTS {_ident(login.user)}" for login in found.values()]
    return schemas + roles


def redact(text: str) -> str:
    """`text` with every PASSWORD literal masked, for output and error messages."""
    return PASSWORD_RE.sub("PASSWORD '***'", text)


def _summary(statement: str) -> str:
    first = redact(" ".join(statement.split()))
    return first if len(first) <= 100 else first[:97] + "..."


def run(conn: psycopg.Connection[Any], batch: Sequence[str]) -> None:
    """Execute `batch` in order, printing each statement (passwords masked) with its time."""
    for statement in batch:
        started = time.perf_counter()
        try:
            conn.execute(statement)
        except psycopg.Error as exc:
            raise AccessError(
                f"`{_summary(statement)}` failed: {type(exc).__name__}: {_first_line(exc)}"
            ) from exc
        print(f"{time.perf_counter() - started:8.2f}s  {_summary(statement)}", flush=True)


def _first_line(exc: BaseException) -> str:
    text = redact(str(exc).strip())
    return text.splitlines()[0] if text else type(exc).__name__


# ----------------------------------------------------------------- the check


@dataclass(frozen=True)
class Probe:
    """One read a login tries, and whether access control should allow it."""

    role: Role
    what: str
    statement: sql.Composed
    allowed: bool


@dataclass(frozen=True)
class Outcome:
    probe: Probe
    allowed: bool
    detail: str

    @property
    def as_expected(self) -> bool:
        return self.allowed == self.probe.allowed


def reads_text(role: Role) -> bool:
    """Whether the Role's views hold the message, and so its own full-text index."""
    return "message" not in ROLE_WITHHELD_COLUMNS.get(role, frozenset())


def probes(slm: str) -> list[Probe]:
    """What each login must and must not read."""
    found: list[Probe] = []
    canonical = sql.Identifier(slm, "bgl_entry")
    canonical_index = sql.Identifier(slm, "bgl_entry_idx")
    for role in Role:
        own = role_schema(slm, role)
        table, index = sql.Identifier(own, "bgl_entry"), sql.Identifier(own, "bgl_entry_idx")
        found.append(
            Probe(role, f"its view {own}.bgl_entry", sql.SQL("SELECT count(*) FROM {}").format(table), True)
        )
        if reads_text(role):
            found.append(
                Probe(
                    role,
                    f"full-text search on its index {own}.bgl_entry_idx",
                    sql.SQL("SELECT count(*) FROM {} WHERE message @@ 'error'").format(index),
                    True,
                )
            )
        else:
            found.append(
                Probe(
                    role,
                    f"the message column of {own}.bgl_entry",
                    sql.SQL("SELECT message FROM {} LIMIT 1").format(table),
                    False,
                )
            )
        found.append(
            Probe(
                role,
                f"the canonical table {slm}.bgl_entry",
                sql.SQL("SELECT count(*) FROM {}").format(canonical),
                False,
            )
        )
        found.append(
            Probe(
                role,
                f"the canonical index {slm}.bgl_entry_idx",
                sql.SQL("SELECT count(*) FROM {} WHERE message @@ 'error'").format(canonical_index),
                False,
            )
        )
        for other in Role:
            if other is not role:
                theirs = role_schema(slm, other)
                found.append(
                    Probe(
                        role,
                        f"the {other.value} view {theirs}.bgl_entry",
                        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(theirs, "bgl_entry")),
                        False,
                    )
                )
    return found


def try_probe(conn: psycopg.Connection[Any], probe: Probe) -> Outcome:
    try:
        rows = conn.execute(probe.statement).fetchall()
    except DENIED as exc:
        return Outcome(probe, False, f"denied: {_first_line(exc)}")
    value = rows[0][0] if rows and rows[0] else None
    shown = f"{value:,}" if isinstance(value, int) else "a row"
    return Outcome(probe, True, f"allowed ({shown})")


def check(slm: str, found: Mapping[Role, Login]) -> list[Outcome]:
    """Connect as each login and try every probe."""
    outcomes: list[Outcome] = []
    for role, login in found.items():
        with psycopg.connect(login.dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S) as conn:
            row = conn.execute("SELECT current_user").fetchone()
            if not row or row[0] != login.user:
                raise AccessError(f"{login.env} connected as {row[0] if row else None!r}, not {login.user!r}")
            outcomes += [try_probe(conn, probe) for probe in probes(slm) if probe.role is role]
    return outcomes


def checks_passwords(login: Login) -> bool:
    """Whether the server refuses `login` with a wrong password (False under trust auth)."""
    wrong = make_conninfo(login.dsn, password=login.password + "-wrong")
    try:
        psycopg.connect(wrong, connect_timeout=CONNECT_TIMEOUT_S).close()
    except psycopg.OperationalError:
        return True
    return False


def report(outcomes: Sequence[Outcome], passwords_checked: bool) -> list[str]:
    lines = []
    for outcome in outcomes:
        mark = "ok " if outcome.as_expected else "BAD"
        lines.append(f"  {mark} {outcome.probe.role.value:10s} {outcome.probe.what}: {outcome.detail}")
    if not passwords_checked:
        lines.append(
            "  note: this server accepted a wrong password (trust auth), so anyone who can reach it can "
            "log in as any role; run SereneDB with password authentication for real separation."
        )
    return lines


# ----------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="access.py", description="Per-Role views, LOGIN roles and GRANTs (T22), checked as each login."
    )
    parser.add_argument("--schema", help="the derived schema (default: SLM_SCHEMA, else slm)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="only check, as each login")
    mode.add_argument("--drop", action="store_true", help="drop the per-Role views and LOGIN roles")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        slm = args.schema or settings.slm_schema
        _schema(slm)
        found = logins(settings)
        if not args.check:
            with psycopg.connect(
                settings.database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S
            ) as conn:
                run(conn, drop_statements(slm, found))
                if args.drop:
                    print(f"access: dropped the per-Role views of {slm} and the LOGIN roles")
                    return 0
                run(conn, statements(slm, found))
        outcomes = check(slm, found)
        for line in report(outcomes, checks_passwords(found[Role.MANAGER])):
            print(line)
        if bad := [o for o in outcomes if not o.as_expected]:
            print(f"access: {len(bad)} of {len(outcomes)} checks failed", file=sys.stderr)
            return 2
        print(f"access: {len(outcomes)} checks passed")
    except (AccessError, DeriveError, ConfigError, psycopg.Error) as exc:
        print(
            f"access: {redact(str(exc).strip().splitlines()[0]) if str(exc).strip() else type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
