"""Settings from the environment (plan section 2.5).

`load_settings(environ)` is pure and testable. `get_settings()` returns the process-wide
settings that tools read at call time; the CLI replaces them once at startup with
`set_settings()` so that a `--role` pin reaches every tool.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

RoleName = Literal["engineer", "team_lead", "manager"]
ROLES: tuple[RoleName, ...] = get_args(RoleName)

DEFAULT_DATABASE_URL = "host=localhost port=7890 dbname=postgres user=postgres"
SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class ConfigError(ValueError):
    """An environment variable has a value the server cannot use."""


class Settings(BaseModel):
    """Runtime settings. Build them with `load_settings`, not from the constructor."""

    model_config = ConfigDict(frozen=True)

    database_url: str = DEFAULT_DATABASE_URL
    role_database_urls: dict[RoleName, str] = Field(default_factory=dict)
    slm_schema: str = "slm"
    raw_schema: str = "public"
    data_dir: Path = Path("data")
    query_timeout_s: float = 15.0
    client_binding: bool = False
    ledger_path: Path | None = None
    export_dir: Path = Path("exports")
    pattern_tier1: bool = True
    role: RoleName | None = None

    def database_url_for(self, role: RoleName) -> str:
        """The DSN for one Role: `DATABASE_URL_<ROLE>` when set, otherwise `DATABASE_URL`."""
        return self.role_database_urls.get(role, self.database_url)


def load_settings(environ: Mapping[str, str] | None = None, **overrides: Any) -> Settings:
    """Read the settings from `environ` (default `os.environ`); `overrides` win (for CLI flags)."""
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
        values["slm_schema"] = _schema("SLM_SCHEMA", schema)
    if (raw_schema := get("SLM_RAW_SCHEMA")) is not None:
        values["raw_schema"] = _schema("SLM_RAW_SCHEMA", raw_schema)
    if (data_dir := get("SLM_DATA_DIR")) is not None:
        values["data_dir"] = Path(data_dir)
    if (timeout := get("SLM_QUERY_TIMEOUT_S")) is not None:
        values["query_timeout_s"] = _positive_float("SLM_QUERY_TIMEOUT_S", timeout)
    if (binding := get("SLM_CLIENT_BINDING")) is not None:
        values["client_binding"] = _flag("SLM_CLIENT_BINDING", binding)
    if (ledger := get("LEDGER_PATH")) is not None:
        values["ledger_path"] = Path(ledger)
    if (export_dir := get("SLM_EXPORT_DIR")) is not None:
        values["export_dir"] = Path(export_dir)
    if (tier1 := get("SLM_PATTERN_TIER1")) is not None:
        values["pattern_tier1"] = _flag("SLM_PATTERN_TIER1", tier1)
    values.update(overrides)
    return Settings(**values)


def _schema(name: str, value: str) -> str:
    if not SCHEMA_RE.fullmatch(value):
        raise ConfigError(f"{name}={value!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
    return value


def _flag(name: str, value: str) -> bool:
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name}={value!r} is not a flag; use 1 or 0")


def _positive_float(name: str, value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise ConfigError(f"{name}={value!r} is not a number of seconds") from None
    if number <= 0:
        raise ConfigError(f"{name}={value!r} must be greater than 0")
    return number


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
