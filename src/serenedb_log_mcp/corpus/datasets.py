"""Dataset registry and the table resolver `relation(dataset, role, scope)` (plan section 2.7).

Every tool gets its table and index names, and the names of its columns, from `relation()`, and
builds SQL with the returned `psycopg.sql` identifiers. No module under `tools/` may contain a
canonical table or index name; tests/contract/test_resolver_contract.py enforces that. The
award-track tickets change only this module: T19 routes a Data Scope inside an agent index's
slice to that index, T20 adds remote Datasets, and T22 returns per-Role views.

The canonical tables are built by `make derive` (sql/adapters/*.sql, sql/derive/*.sql) in the
schema named by SLM_SCHEMA.

Per-Role views (T22). `make access` (sql/access/roles.sql) gives each Role a schema of its own,
`<SLM_SCHEMA>_<role>` (`role_schema`), with a view of every canonical table under the same name, an
inverted index over the entry views of the Roles that may read Log Entry text, and a LOGIN role
that may read only that schema. When a Role's `DATABASE_URL_<ROLE>` is set, its calls connect as
that login (`db.get_database`) and the resolver returns that Role's views. The Manager's views leave
out the columns in `ROLE_WITHHELD_COLUMNS` (the message), so asking for one, or for the full-text
index over it, raises `RoleAccessError` with what the Role can do instead. Without the DSN a Role
reads the canonical tables, and the Role-scoped tools (`roles.shape_for_role`) remain the
guarantee; the grants are defence in depth (docs/access-control.md).
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, get_args

from mcp.server.mcpserver.exceptions import ToolError
from psycopg import sql

from serenedb_log_mcp.config import ROLES, RoleName, Settings, get_settings

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

# The default Incident table (T10, plan section 2.2). `dataset`, `pattern_key` and `incident_id`
# together identify one Incident, because the id is the running sum() within one Pattern (#1).
# `gap_s` and `merge_scope` record how the table was built, so Evidence can quote them, and
# `t0_utc`/`t1_utc` carry the UTC bounds, so no tool re-derives an epoch from the local timestamp.
INCIDENT_COLUMNS = (
    "dataset",
    "incident_id",
    "pattern_key",
    "gap_s",
    "merge_scope",
    "t0",
    "t1",
    "t0_utc",
    "t1_utc",
    "entries",
    "bursts",
    "nodes",
    "nodecards",
    "midplanes",
    "racks",
    "top_node",
    "top_node_share",
    "shape",
)
# The parameters the default Incident table is built with (#1): a gap of 300 s, chains merged
# across every source. list_incidents reads the table for these and chains at query time otherwise.
DEFAULT_GAP_S = 300
DEFAULT_MERGE_SCOPE = "all"
# The pattern_key of the OpenStack lifecycle Request Failures (T27): one Incident per instance
# whose build or teardown started and never completed (sql/derive/35_os_incidents.sql).
LIFECYCLE_KEY = "lifecycle-incomplete"
# The Shape labels of the Glossary; exactly one per BGL Incident. OpenStack Incidents carry none,
# because the labels are defined on BGL topology (35_os_incidents.sql).
SHAPES = (
    "single-node storm",
    "single-node isolated",
    "one midplane",
    "multi-midplane",
    "machine-wide",
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


ROLE_WITHHELD_COLUMNS: Mapping[RoleName, frozenset[str]] = MappingProxyType(
    {RoleName.MANAGER: frozenset({"message"})}
)
"""Canonical columns a Role's views leave out (sql/access/roles.sql, T22). The Manager decides from
measured frequency and affected Components (#4), so its database login cannot read Log Entry text."""


class RoleAccessError(ToolError):
    """A Role's views (T22) lack what the call needs; the text says what the Role can do instead.

    A `ToolError`, so the toolkit hands its text to the agent unchanged.
    """


def role_schema(schema: str, role: RoleName) -> str:
    """The schema that holds one Role's views of `schema`'s tables (sql/access/roles.sql)."""
    return f"{schema}_{RoleName(role).value}"


def _no_text_message(dataset: str, role: RoleName) -> str:
    return (
        f"Role {role.value!r} reads {dataset} through its own database views (access control per Role), "
        "which hold no Log Entry text, so this call cannot run for it: full-text matching (query), "
        "search_logs and get_context all read the message. Count instead with count_by or trend "
        "without query, filtered by alert_categories, pattern_ids, family_id, components or levels; "
        "Log Entry text is for the Engineer and the Team Lead."
    )


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
    views_for: RoleName | None = None
    """The Role whose views (T22) `schema` holds; None for the canonical tables."""

    @property
    def withheld(self) -> frozenset[str]:
        """The canonical columns this Role's views leave out (empty for the canonical tables)."""
        if self.views_for is None:
            return frozenset()
        return ROLE_WITHHELD_COLUMNS.get(self.views_for, frozenset())

    @property
    def columns(self) -> Mapping[str, str]:
        """Concept or canonical column name -> column name, without the columns the views withhold."""
        if not self.withheld:
            return self.dataset.columns
        return MappingProxyType(
            {
                concept: column
                for concept, column in self.dataset.columns.items()
                if column not in self.withheld
            }
        )

    @property
    def table(self) -> sql.Identifier:
        """The canonical Log Entry table, for plain filters, point lookups and aggregations."""
        return sql.Identifier(self.schema, self.table_ident)

    @property
    def index(self) -> sql.Identifier:
        """The inverted index over that table, for @@, BM25 and ts_* predicates.

        RoleAccessError for a Role whose views hold no message: its login has no full-text index.
        """
        if self.views_for is not None and "message" in self.withheld:
            raise RoleAccessError(_no_text_message(self.dataset.name, self.views_for))
        return sql.Identifier(self.schema, self.index_ident)

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
        """The column for a concept or canonical column name; UnknownColumnError if absent, and
        RoleAccessError if this Role's views withhold it."""
        name = self.dataset.columns.get(concept)
        if self.views_for is not None and name in self.withheld:
            if name == "message":
                raise RoleAccessError(_no_text_message(self.dataset.name, self.views_for))
            raise RoleAccessError(
                f"Role {self.views_for.value!r} reads {self.dataset.name} through its own database views "
                f"(access control per Role), which leave out the column {name!r}, so this call cannot run "
                "for it."
            )
        try:
            return sql.Identifier(self.dataset.columns[concept])
        except KeyError:
            available = ", ".join(sorted(self.columns))
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

    A Role whose `DATABASE_URL_<ROLE>` is set connects as its own LOGIN role, which may read only
    its views, so it gets the relations of `role_schema(SLM_SCHEMA, role)` (T22); every other call
    gets the canonical tables in SLM_SCHEMA. `scope` is accepted so every tool passes it from the
    start; it does not change the result yet (T19 returns the agent index that covers it).
    """
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown Role {role!r}; valid: {', '.join(ROLES)}")
    entry = dataset(dataset_name)
    settings = settings if settings is not None else get_settings()
    schema = settings.slm_schema
    views_for = RoleName(role) if role is not None and role in settings.role_database_urls else None
    if views_for is not None:
        schema = role_schema(schema, views_for)
    return Relation(
        dataset=entry, schema=schema, table_ident=entry.table, index_ident=entry.index, views_for=views_for
    )
