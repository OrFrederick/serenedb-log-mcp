"""Per-Role views and LOGIN roles for the access-control db tests (T22).

`create(database_url, slm, raw)` gives three LOGIN roles of unique names (`slm_t22_<hex>_<role>`,
because roles are server-wide objects on a shared server) and applies sql/access/roles.sql for them
over the derived schema `slm`; `drop(access)` removes both again and checks that nothing is left.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import access
from serenedb_log_mcp.config import Role, load_settings
from serenedb_log_mcp.corpus.datasets import role_schema


@dataclass(frozen=True)
class Access:
    """A derived schema with per-Role views and the LOGIN roles that may read them."""

    slm: str
    raw: str
    admin_dsn: str
    dsns: dict[Role, str]
    users: dict[Role, str]

    @property
    def env(self) -> dict[str, str]:
        """The DATABASE_URL_<ROLE> variables, as a server or `make access` reads them."""
        return {f"DATABASE_URL_{role.value.upper()}": dsn for role, dsn in self.dsns.items()}

    def schema(self, role: Role) -> str:
        return role_schema(self.slm, role)


def apply(granted: Access) -> None:
    """(Re)create the per-Role views and LOGIN roles, as `make access` does."""
    found = access.logins(load_settings({"DATABASE_URL": granted.admin_dsn, **granted.env}))
    with psycopg.connect(granted.admin_dsn, autocommit=True) as conn:
        access.run(conn, access.drop_statements(granted.slm, found))
        access.run(conn, access.statements(granted.slm, found))


def drop(granted: Access) -> None:
    found = access.logins(load_settings({"DATABASE_URL": granted.admin_dsn, **granted.env}))
    with psycopg.connect(granted.admin_dsn, autocommit=True) as conn:
        access.run(conn, access.drop_statements(granted.slm, found))
        users = list(granted.users.values())
        left = conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s::VARCHAR[])", [users]
        ).fetchall()
        schemas = [granted.schema(role) for role in Role]
        views = conn.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(%s::VARCHAR[])",
            [schemas],
        ).fetchall()
    assert not left, f"LOGIN roles left on the server: {left}"
    assert not views, f"per-Role view schemas left on the server: {views}"


def create(database_url: str, slm: str, raw: str) -> Access:
    """Three fresh LOGIN roles with random passwords, and their views of `slm`."""
    suffix = uuid.uuid4().hex[:8]
    base = conninfo_to_dict(database_url)
    users = {role: f"slm_t22_{suffix}_{role.value}" for role in Role}
    dsns = {
        role: make_conninfo(**{**base, "user": users[role], "password": secrets.token_hex(8)})
        for role in Role
    }
    found = Access(slm=slm, raw=raw, admin_dsn=database_url, dsns=dsns, users=users)
    try:
        apply(found)
    except BaseException:
        drop(found)  # a half-applied run must not leave LOGIN roles on the shared server
        raise
    return found
