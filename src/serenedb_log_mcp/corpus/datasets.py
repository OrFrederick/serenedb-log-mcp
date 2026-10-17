"""Dataset registry and the table resolver `relation(dataset, role, scope)` (plan section 2.7).

Every tool gets its table and index names, and the names of its columns, from `relation()`, and
builds SQL with the returned `psycopg.sql` identifiers. No module under `tools/` may contain a
canonical table or index name; tests/contract/test_resolver_contract.py enforces that. The
award-track tickets change only this module: T19 routes a Data Scope inside an agent index's
slice to that index (`corpus/agent_indexes.py`), T20 adds remote Datasets, and T22 returns
per-Role views.

The canonical tables are built by `make derive` (sql/adapters/*.sql, sql/derive/*.sql) in the
schema named by SLM_SCHEMA.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, get_args

from psycopg import sql

from serenedb_log_mcp.config import ROLES, RoleName, Settings, get_settings
from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex

DatasetName = Literal["bgl", "openstack"]
DATASET_NAMES: tuple[DatasetName, ...] = get_args(DatasetName)

# Canonical relation names (plan section 2.2). Only this module and the SQL under sql/ name them.
ENTRY_TABLES: Mapping[DatasetName, str] = MappingProxyType({"bgl": "bgl_entry", "openstack": "os_entry"})
ENTRY_INDEXES: Mapping[DatasetName, str] = MappingProxyType(
    {"bgl": "bgl_entry_idx", "openstack": "os_entry_idx"}
)
OWNERSHIP = "ownership"
PATTERN_DICT = "pattern_dict"
PATTERNS = "pattern"
INCIDENTS = "incident"
INSTANCES = "os_instance"
CANONICAL_TABLES = frozenset(
    {*ENTRY_TABLES.values(), OWNERSHIP, PATTERN_DICT, PATTERNS, INCIDENTS, INSTANCES}
)
CANONICAL_INDEXES = frozenset(ENTRY_INDEXES.values())
TEXT_DICTIONARY = "log_text"
"""The text search dictionary of the `message` column (sql/derive/90_indexes.sql); agent indexes reuse it."""

OWNERSHIP_IS_STUB = True
OWNERSHIP_NOTE = (
    "Teams come from a stub Ownership Table with fictional Teams (TODO(organiser)); "
    "the organisers' Ownership Table replaces it."
)
# The value of the Ownership Table's team_source column on every row of the stub. A tool that joins
# the table can select team_source to say where a Team came from; T25 sets its own value.
OWNERSHIP_STUB_SOURCE = "stub"

BGL_COLUMNS = (
    "entry_id",
    "ts",
    "epoch_utc",
    "node",
    "rack",
    "midplane",
    "nodecard",
    "component",
    "level",
    "alert_tag",
    "message",
    "template_id",
    "family_id",
    "pattern_key",
)
OS_COLUMNS = (
    "entry_id",
    "ts",
    "epoch_utc",
    "source_id",
    "log_file",
    "pid",
    "component",
    "level",
    "request_id",
    "instance_id",
    "instance_ref",
    "endpoint",
    "http_status",
    "latency_s",
    "message",
    "template_id",
    "family_id",
    "pattern_key",
)

# Concepts that tools use across Datasets, mapped to each Dataset's column. Every canonical column
# also maps to itself. "source" is what a Burst runs on (#1): a BGL node, an OpenStack instance.
_COMMON_CONCEPTS = {"time": "ts", "epoch": "epoch_utc", "pattern": "template_id"}
_BGL_CONCEPTS = {**_COMMON_CONCEPTS, "source": "node", "alert_category": "alert_tag"}
_OS_CONCEPTS = {**_COMMON_CONCEPTS, "source": "instance_ref", "instance": "instance_ref"}


class UnknownDatasetError(ValueError):
    """The Dataset name is not in the registry."""


class UnknownColumnError(ValueError):
    """The Dataset has no column for the requested concept."""


@dataclass(frozen=True)
class Dataset:
    """One Dataset of the registry."""

    name: DatasetName
    title: str
    table: str
    index: str
    table_columns: tuple[str, ...]
    columns: Mapping[str, str]
    time_note: str
    ts_zone: str
    """The IANA time zone that `ts` is logged in, for zoneinfo.ZoneInfo; epoch_utc is always UTC."""

    @property
    def ts_is_local(self) -> bool:
        """True when `ts` is a local time, so a window across Datasets must use epoch_utc."""
        return self.ts_zone != "UTC"


def _dataset(
    name: DatasetName,
    title: str,
    table_columns: tuple[str, ...],
    concepts: Mapping[str, str],
    time_note: str,
    ts_zone: str,
) -> Dataset:
    columns = {column: column for column in table_columns} | dict(concepts)
    return Dataset(
        name=name,
        title=title,
        table=ENTRY_TABLES[name],
        index=ENTRY_INDEXES[name],
        table_columns=table_columns,
        columns=MappingProxyType(columns),
        time_note=time_note,
        ts_zone=ts_zone,
    )


DATASETS: Mapping[DatasetName, Dataset] = MappingProxyType(
    {
        "bgl": _dataset(
            "bgl",
            "BGL",
            BGL_COLUMNS,
            _BGL_CONCEPTS,
            "ts is the local time as logged (US Pacific, with daylight saving time); "
            "time gaps are computed on epoch_utc.",
            "America/Los_Angeles",
        ),
        "openstack": _dataset(
            "openstack",
            "OpenStack",
            OS_COLUMNS,
            _OS_CONCEPTS,
            "ts is the time as logged, which carries no zone and is taken as UTC.",
            "UTC",
        ),
    }
)


@dataclass(frozen=True)
class Relation:
    """Where one Dataset's Log Entries are for one Role and Data Scope, as SQL identifiers."""

    dataset: Dataset
    schema: str
    table_ident: str = field(repr=False)
    index_ident: str = field(repr=False)
    agent_index: AgentIndex | None = None
    """The agent index (T19) `index` routes to, when the Data Scope lies inside its slice."""

    @property
    def columns(self) -> Mapping[str, str]:
        """Concept or canonical column name -> column name."""
        return self.dataset.columns

    @property
    def table(self) -> sql.Identifier:
        """The canonical Log Entry table, for plain filters, point lookups and aggregations."""
        return sql.Identifier(self.schema, self.table_ident)

    @property
    def index(self) -> sql.Identifier:
        """The inverted index over that table, for @@, BM25 and ts_* predicates.

        It is the agent index when the Data Scope lies inside an agent index's slice; reading it
        then records the use, so the Evidence says which index answered (`toolkit.evidence_tool`).
        """
        if self.agent_index is not None:
            agent_indexes.record_use(self.agent_index)
        return sql.Identifier(self.schema, self.index_ident)

    @property
    def text_dictionary(self) -> sql.Identifier:
        """The text search dictionary of the indexed `message` column, for building agent indexes."""
        return sql.Identifier(self.schema, TEXT_DICTIONARY)

    @property
    def table_name(self) -> str:
        """`schema.table`, for Evidence notes and the Data Scope."""
        return f"{self.schema}.{self.table_ident}"

    @property
    def index_name(self) -> str:
        """`schema.index`, for Evidence notes (which index answered)."""
        return f"{self.schema}.{self.index_ident}"

    @property
    def ownership_table(self) -> sql.Identifier:
        """The Ownership Table: (dataset, component, team, team_source). A stub until T25, see
        OWNERSHIP_NOTE; team_source is OWNERSHIP_STUB_SOURCE on every stub row."""
        return sql.Identifier(self.schema, OWNERSHIP)

    @property
    def pattern_dict_table(self) -> sql.Identifier:
        """The Pattern dictionary, one row per distinct message (T9)."""
        return sql.Identifier(self.schema, PATTERN_DICT)

    @property
    def patterns_table(self) -> sql.Identifier:
        """The Pattern catalogue, one row per Pattern (T9)."""
        return sql.Identifier(self.schema, PATTERNS)

    @property
    def incidents_table(self) -> sql.Identifier:
        """The default Incident table (T10, T27)."""
        return sql.Identifier(self.schema, INCIDENTS)

    @property
    def instances_table(self) -> sql.Identifier:
        """The OpenStack instance phases (T11)."""
        return sql.Identifier(self.schema, INSTANCES)

    def column(self, concept: str) -> sql.Identifier:
        """The column for a concept or canonical column name; UnknownColumnError if absent."""
        try:
            return sql.Identifier(self.dataset.columns[concept])
        except KeyError:
            available = ", ".join(sorted(self.dataset.columns))
            raise UnknownColumnError(
                f"{self.dataset.name} has no column {concept!r}; available: {available}"
            ) from None


def dataset(name: str) -> Dataset:
    """The registry entry for a Dataset name (case-insensitive); UnknownDatasetError otherwise."""
    key = name.strip().lower()
    for candidate in DATASET_NAMES:
        if candidate == key:
            return DATASETS[candidate]
    hint = ""
    if close := difflib.get_close_matches(key, DATASET_NAMES, n=1):
        hint = f"; did you mean {close[0]!r}?"
    raise UnknownDatasetError(f"unknown Dataset {name!r}; valid: {', '.join(DATASET_NAMES)}{hint}")


def relation(
    dataset_name: str,
    role: RoleName | None = None,
    scope: object | None = None,
    *,
    settings: Settings | None = None,
) -> Relation:
    """Resolve the tables and index of a Dataset for a Role and Data Scope.

    `scope` is the Data Scope the statements read (a `DataScope`, or anything with ISO-8601
    `time_from` and `time_to`). When its whole time range lies inside the slice of the Dataset's
    agent index (T19), `index` is that agent index; otherwise, and without a scope, it is the
    Dataset's full index. Pass no scope for statements that read beyond the Data Scope, such as the
    whole-Dataset baselines. `role` does not change the result yet; T22 returns the Role's views.
    """
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown Role {role!r}; valid: {', '.join(ROLES)}")
    entry = dataset(dataset_name)
    schema = (settings if settings is not None else get_settings()).slm_schema
    agent = agent_indexes.covering(schema, entry.name, *_time_range(scope))
    return Relation(
        dataset=entry,
        schema=schema,
        table_ident=entry.table,
        index_ident=entry.index if agent is None else agent.name,
        agent_index=agent,
    )


def _time_range(scope: object | None) -> tuple[datetime | None, datetime | None]:
    """The scope's time range as datetimes; None for an open side or a scope without one."""

    def bound(name: str) -> datetime | None:
        value = getattr(scope, name, None)
        return datetime.fromisoformat(value) if isinstance(value, str) else None

    return bound("time_from"), bound("time_to")
