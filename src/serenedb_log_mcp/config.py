"""Settings from the environment (plan section 2.5).

`load_settings(environ)` is pure and testable. `get_settings()` returns the process-wide
settings that tools read at call time; the CLI replaces them once at startup with
`set_settings()` so that a `--role` pin reaches every tool.

`Settings` validates every field however it is built (environment, overrides or the constructor),
keeps the DSNs out of its repr because they can carry passwords, and is immutable.

`safe_dsn` is the one place that turns a DSN into text that may be logged or printed; the loader,
the readiness wait and the test gates all use it, so a password cannot reach a log or CI output.
"""

from __future__ import annotations

import functools
import logging
import math
import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import psycopg
from psycopg import pq
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
SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# The prefix of the per-Role DSN variables. Anything else behind it is a misconfiguration, because
# `database_url_for` would silently fall back to the shared DATABASE_URL for that Role.
ROLE_URL_PREFIX = "DATABASE_URL_"

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
}


logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """An environment variable or override has a value the server cannot use."""


# What libpq quotes in a parse error can be the password itself, so quoted fragments that come from
# the DSN are taken out of the message.
_QUOTED = re.compile(r'"([^"]*)"')
# Secrets to look for in a DSN libpq could not parse, where the keys are not available: a
# `password=` or `sslpassword=` value, and the password of a postgresql://user:password@host URI.
_DSN_SECRETS = (
    re.compile(r"(?i)(?:ssl)?password\s*=\s*(\S+)"),
    re.compile(r"://[^/@]*:(.+)@"),
)


@functools.cache
def _secret_dsn_keys() -> frozenset[str]:
    """The connection parameters libpq itself marks as secret, plus the two password keys.

    libpq flags `password`, `sslpassword` and `oauth_client_secret` with dispchar `*`, which is how
    psql decides not to echo them. Asking libpq keeps this list correct as it grows.
    """
    try:
        options = pq.Conninfo.get_defaults()
    except psycopg.OperationalError:
        options = []
    marked = {option.keyword.decode() for option in options if option.dispchar == b"*"}
    return frozenset(marked | {"password", "sslpassword"})


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return "password" in lowered or lowered in _secret_dsn_keys()


def error_detail(exc: BaseException) -> str:
    """The first line of an exception message, or its class name when the message is empty."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def safe_dsn(dsn: str) -> str:
    """`dsn` with every secret removed, for logs, skip reasons and error messages.

    libpq parses a `postgresql://user:password@host` URI into the same keys as a key=value string,
    so a URI-embedded credential is removed here as well. A DSN libpq cannot parse reports the
    parse error rather than the value, and falls back to the error class if the message could
    still carry a secret.
    """
    try:
        parts = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        return f"<unparseable DSN: {_parse_error(dsn, exc)}>"
    visible = " ".join(f"{key}={value}" for key, value in parts.items() if not _is_secret(key))
    return visible or "<empty DSN>"


def _parse_error(dsn: str, exc: psycopg.Error) -> str:
    message = error_detail(exc)
    redacted = _QUOTED.sub(
        lambda m: '"..."' if len(m[1]) > 1 and m[1] in dsn else m[0],
        message,
    )
    secrets = [found for pattern in _DSN_SECRETS for found in pattern.findall(dsn) if found]
    if any(secret in redacted for secret in secrets):
        return type(exc).__name__
    return redacted


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
    role: RoleName | None = None

    @field_validator("slm_schema", "raw_schema")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        if not SCHEMA_RE.fullmatch(value):
            raise ValueError(f"{value!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
        return value

    @field_validator("query_timeout_s")
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
    role_urls = {role: url for role in ROLES if (url := get(f"{ROLE_URL_PREFIX}{role.upper()}")) is not None}
    if role_urls:
        values["role_database_urls"] = role_urls
    _warn_about_unknown_role_urls(env)
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
    values.update(overrides)
    try:
        return Settings(**values)
    except ValidationError as exc:
        raise ConfigError(_describe(exc, overrides)) from None


def _warn_about_unknown_role_urls(env: Mapping[str, str]) -> None:
    """Warn about every `DATABASE_URL_*` variable that is not a Role, naming the ones that work.

    Such a variable is read by nothing, so the Role it was meant for keeps using the shared
    DATABASE_URL. That is a silent access-scoping failure, so it is worth a line on stderr. The
    warning names the variable but never its value, which can carry a password.
    """
    known = {f"{ROLE_URL_PREFIX}{role.upper()}" for role in ROLES}
    unknown = sorted(
        name
        for name, value in env.items()
        if name.startswith(ROLE_URL_PREFIX) and name not in known and value.strip()
    )
    for name in unknown:
        logger.warning(
            "%s is set but is not a per-Role DSN variable, so it is ignored and that Role keeps "
            "using DATABASE_URL. The variables that are read are %s.",
            name,
            ", ".join(sorted(known)),
        )


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
