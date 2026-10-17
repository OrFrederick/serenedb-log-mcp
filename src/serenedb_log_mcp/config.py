"""Settings from the environment (plan section 2.5).

`load_settings(environ)` is pure and testable. `get_settings()` returns the process-wide
settings that tools read at call time; the CLI replaces them once at startup with
`set_settings()` so that a `--role` pin reaches every tool.

`Settings` validates every field however it is built (environment, overrides or the constructor),
keeps the DSNs out of its repr because they can carry passwords, and is immutable.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator


class Role(StrEnum):
    """Who is asking. The Role selects the connection, the defaults and the output shape (#4).

    This is the one definition of the Role names in the tree; `evidence.Role` re-exports it. It is a
    StrEnum, so a member equals and hashes like its value and works wherever a name string is
    expected (argparse choices, environment variable suffixes, JSON keys).
    """

    ENGINEER = "engineer"
    TEAM_LEAD = "team_lead"
    MANAGER = "manager"


RoleName = Role
ROLES: tuple[Role, ...] = tuple(Role)

DEFAULT_DATABASE_URL = "host=localhost port=7890 dbname=postgres user=postgres"
DEFAULT_RECONNECT_WAIT_S = 30.0
"""How long the database layer waits for `SELECT 1` after a lost connection; a restart took 2 to 3 s (#2)."""
DEFAULT_MAX_IDLE_CONNECTIONS = 4
"""Idle connections the database layer keeps per DSN; one connect over the dev tunnel took about 2.3 s."""
SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

# Field name -> environment variable, for error messages.
ENV_NAMES = {
    "database_url": "DATABASE_URL",
    "role_database_urls": "DATABASE_URL_<ROLE>",
    "slm_schema": "SLM_SCHEMA",
    "raw_schema": "SLM_RAW_SCHEMA",
    "data_dir": "SLM_DATA_DIR",
    "query_timeout_s": "SLM_QUERY_TIMEOUT_S",
    "client_binding": "SLM_CLIENT_BINDING",
    "ledger_path": "LEDGER_PATH",
    "export_dir": "SLM_EXPORT_DIR",
    "pattern_tier1": "SLM_PATTERN_TIER1",
    "reconnect_wait_s": "SLM_RECONNECT_WAIT_S",
    "max_idle_connections": "SLM_MAX_IDLE_CONNECTIONS",
}


class ConfigError(ValueError):
    """An environment variable or override has a value the server cannot use."""


class Settings(BaseModel):
    """Runtime settings. Build them with `load_settings` so that errors name the variable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: str = Field(default=DEFAULT_DATABASE_URL, repr=False)
    role_database_urls: Mapping[RoleName, str] = Field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )
    slm_schema: str = "slm"
    raw_schema: str = "public"
    data_dir: Path = Path("data")
    query_timeout_s: float = 15.0
    client_binding: bool = False
    ledger_path: Path | None = None
    export_dir: Path = Path("exports")
    pattern_tier1: bool = True
    reconnect_wait_s: float = DEFAULT_RECONNECT_WAIT_S
    max_idle_connections: int = Field(default=DEFAULT_MAX_IDLE_CONNECTIONS, ge=0)
    role: RoleName | None = None

    @field_validator("slm_schema", "raw_schema")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        if not SCHEMA_RE.fullmatch(value):
            raise ValueError(f"{value!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
        return value

    @field_validator("query_timeout_s", "reconnect_wait_s")
    @classmethod
    def _finite_positive(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{value!r} is not a finite number of seconds greater than 0")
        return value

    @field_validator("database_url")
    @classmethod
    def _valid_dsn(cls, value: str) -> str:
        return _check_dsn("DATABASE_URL", value)

    @field_validator("role_database_urls", mode="after")
    @classmethod
    def _read_only(cls, value: Mapping[RoleName, str]) -> Mapping[RoleName, str]:
        for role, dsn in value.items():
            _check_dsn(f"DATABASE_URL_{role.upper()}", dsn)
        return MappingProxyType(dict(value))

    @field_serializer("role_database_urls")
    def _as_dict(self, value: Mapping[RoleName, str]) -> dict[RoleName, str]:
        return dict(value)

    def database_url_for(self, role: RoleName) -> str:
        """The DSN for one Role: `DATABASE_URL_<ROLE>` when set, otherwise `DATABASE_URL`."""
        return self.role_database_urls.get(role, self.database_url)


def load_settings(environ: Mapping[str, str] | None = None, **overrides: Any) -> Settings:
    """Read the settings from `environ` (default `os.environ`); `overrides` win (for CLI flags).

    Raises ConfigError naming the environment variable (or the override) that is wrong.
    """
    env = os.environ if environ is None else environ

    def get(name: str) -> str | None:
        value = env.get(name, "").strip()
        return value or None

    values: dict[str, Any] = {}
    if (dsn := get("DATABASE_URL")) is not None:
        values["database_url"] = dsn
    role_urls = {role: url for role in ROLES if (url := get(f"DATABASE_URL_{role.upper()}")) is not None}
    if role_urls:
        values["role_database_urls"] = role_urls
    if (schema := get("SLM_SCHEMA")) is not None:
        values["slm_schema"] = schema
    if (raw_schema := get("SLM_RAW_SCHEMA")) is not None:
        values["raw_schema"] = raw_schema
    if (data_dir := get("SLM_DATA_DIR")) is not None:
        values["data_dir"] = Path(data_dir)
    if (timeout := get("SLM_QUERY_TIMEOUT_S")) is not None:
        values["query_timeout_s"] = _number("SLM_QUERY_TIMEOUT_S", timeout)
    if (binding := get("SLM_CLIENT_BINDING")) is not None:
        values["client_binding"] = _flag("SLM_CLIENT_BINDING", binding)
    if (ledger := get("LEDGER_PATH")) is not None:
        values["ledger_path"] = Path(ledger)
    if (export_dir := get("SLM_EXPORT_DIR")) is not None:
        values["export_dir"] = Path(export_dir)
    if (tier1 := get("SLM_PATTERN_TIER1")) is not None:
        values["pattern_tier1"] = _flag("SLM_PATTERN_TIER1", tier1)
    if (wait := get("SLM_RECONNECT_WAIT_S")) is not None:
        values["reconnect_wait_s"] = _number("SLM_RECONNECT_WAIT_S", wait)
    if (idle := get("SLM_MAX_IDLE_CONNECTIONS")) is not None:
        values["max_idle_connections"] = _whole_number("SLM_MAX_IDLE_CONNECTIONS", idle)
    values.update(overrides)
    try:
        return Settings(**values)
    except ValidationError as exc:
        raise ConfigError(_describe(exc, overrides)) from None


def _describe(exc: ValidationError, overrides: Mapping[str, Any]) -> str:
    """One line per invalid field, named as the user set it; never echoes a DSN."""
    problems = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "settings"
        source = field if field in overrides else ENV_NAMES.get(field, field)
        problems.append(f"{source}: {error['msg']}")
    return "; ".join(problems)


def _check_dsn(name: str, value: str) -> str:
    # The message never repeats the value, because a DSN can carry a password.
    try:
        conninfo_to_dict(value)
    except psycopg.ProgrammingError:
        raise ValueError(
            f"{name} is not a valid libpq connection string (key=value pairs or a postgresql:// URI)"
        ) from None
    return value


def _flag(name: str, value: str) -> bool:
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name}={value!r} is not a flag; use 1 or 0")


def _number(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise ConfigError(f"{name}={value!r} is not a number of seconds") from None


def _whole_number(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"{name}={value!r} is not a whole number") from None


_active: Settings | None = None


def get_settings() -> Settings:
    """The process-wide settings, read from `os.environ` on first use."""
    global _active
    if _active is None:
        _active = load_settings()
    return _active


def set_settings(settings: Settings) -> None:
    """Replace the process-wide settings (the CLI does this once at startup)."""
    global _active
    _active = settings
