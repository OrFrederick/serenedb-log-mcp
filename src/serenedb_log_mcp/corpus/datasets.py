"""Dataset registry and the table resolver `relation(dataset, role, scope)` (plan section 2.7).

Every tool gets its table and index names, and the names of its columns, from `relation()`, and
builds SQL with the returned `psycopg.sql` identifiers. No module under `tools/` may contain a
canonical table or index name; tests/contract/test_resolver_contract.py enforces that. The
award-track tickets change only this module: T19 routes a Data Scope inside an agent index's
slice to that index (`corpus/agent_indexes.py`), T20 adds remote Datasets, and T22 returns
per-Role views.

The canonical tables are built by `make derive` (sql/adapters/*.sql, sql/derive/*.sql) in the
schema named by SLM_SCHEMA.

Remote Datasets (T20). `text_bench` is about 1,000,000,000 OTel Log Entries in one 41 GB Parquet
file on S3, queried in place through a view (`make huge-setup`, sql/remote/text_bench.sql). It is
not part of the Base Corpus, so a call that names no Dataset never reads it, and it has no full
index: indexing a billion rows is exactly what the Dataset is there to avoid. So the resolver
enforces two rules, with errors that tell the agent how to proceed:

- every Data Scope on it names a time slice of at most `RemoteSource.max_scan_span`; the file is
  sorted by time, so a slice reads only the Parquet row groups it overlaps;
- full-text matching needs an agent index (T19) over the slice: `Relation.index` raises instead of
  naming an index that does not exist.

Its facts (time range, Components and levels with their counts) come from `tb_facts`, which the
setup step computes once, because computing them per call would scan the whole file.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal, get_args

from psycopg import sql

from serenedb_log_mcp.config import ROLES, RoleName, Settings, get_settings
from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex
from serenedb_log_mcp.scope import ScopeError

DatasetName = Literal["bgl", "openstack", "text_bench"]
DATASET_NAMES: tuple[DatasetName, ...] = get_args(DatasetName)
"""Every registered Dataset, which a tool's `dataset` parameter accepts."""
BASE_CORPUS: tuple[DatasetName, ...] = ("bgl", "openstack")
"""The Datasets a call that names none reads: the Base Corpus. Remote Datasets are named explicitly."""

# Canonical relation names (plan section 2.2). Only this module and the SQL under sql/ name them.
ENTRY_TABLES: Mapping[DatasetName, str] = MappingProxyType(
    {"bgl": "bgl_entry", "openstack": "os_entry", "text_bench": "tb_entry"}
)
ENTRY_INDEXES: Mapping[DatasetName, str] = MappingProxyType(
    {"bgl": "bgl_entry_idx", "openstack": "os_entry_idx"}
)
"""The full inverted index of each Dataset that has one; a remote Dataset has none."""
DEFAULT_MAX_INDEX_ROWS = 1_000_000
"""The agent index cap of a Dataset without its own `max_index_rows` (`index_admin.MAX_SLICE_ROWS`)."""
REMOTE_FACTS = "tb_facts"
"""text_bench's facts (time range, Components, levels), computed once by `make huge-setup`."""
OWNERSHIP = "ownership"
PATTERN_DICT = "pattern_dict"
PATTERNS = "pattern"
INCIDENTS = "incident"
INSTANCES = "os_instance"
CANONICAL_TABLES = frozenset(
    {*ENTRY_TABLES.values(), REMOTE_FACTS, OWNERSHIP, PATTERN_DICT, PATTERNS, INCIDENTS, INSTANCES}
)
CANONICAL_INDEXES = frozenset(ENTRY_INDEXES.values())
TEXT_DICTIONARY = "log_text"
"""The text search dictionary of the `message` column (sql/derive/90_indexes.sql); agent indexes reuse it."""

OWNERSHIP_IS_STUB = True
OWNERSHIP_NOTE = (
    "Teams come from a stub Ownership Table with fictional Teams (TODO(organiser)); "
    "the organisers' Ownership Table replaces it."
)

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

TB_COLUMNS = ("ts", "component", "level", "message", "trace_id", "span_id")
"""The text_bench view's columns: the OTel Timestamp (as UTC), ServiceName, SeverityText, Body,
TraceId and SpanId, renamed to canonical names inside the view. Renaming keeps the view on the
fast path, so an index over it returns real columns (verified on SereneDB 26.10.1). There is no
entry_id: `file_row_number` in the view breaks reading through an index over it on 26.10.1."""

# Concepts that tools use across Datasets, mapped to each Dataset's column. Every canonical column
# also maps to itself. "source" is what a Burst runs on (#1): a BGL node, an OpenStack instance. For
# text_bench it is the service (component): the OTel resource attributes that name a pod or host are
# MAP columns, and distinct traces would read the 3.5 GB TraceId column, which made a cold count_by
# over a 60 s slice take 28 s (past the 15 s query limit).
_COMMON_CONCEPTS = {"time": "ts", "epoch": "epoch_utc", "pattern": "template_id"}
_BGL_CONCEPTS = {**_COMMON_CONCEPTS, "source": "node", "alert_category": "alert_tag"}
_OS_CONCEPTS = {**_COMMON_CONCEPTS, "source": "instance_ref", "instance": "instance_ref"}
_TB_CONCEPTS = {"time": "ts", "source": "component", "trace": "trace_id", "span": "span_id"}


class UnknownDatasetError(ValueError):
    """The Dataset name is not in the registry."""


class UnknownColumnError(ValueError):
    """The Dataset has no column for the requested concept."""


@dataclass(frozen=True)
class RemoteSource:
    """How a remote Dataset is read: in place, one bounded time slice per call."""

    description: str
    """What the Dataset is and how big, for error messages and describe_corpus."""
    max_scan_span: timedelta
    """The longest Data Scope a call may read without an index (the Parquet row groups it overlaps)."""
    example_slice: tuple[str, str]
    """A valid slice (time_from, time_to) for the error messages."""
    index_rows_note: str
    """How long a slice an agent index of `max_index_rows` covers, in words."""


@dataclass(frozen=True)
class Dataset:
    """One Dataset of the registry."""

    name: DatasetName
    title: str
    table: str
    index: str | None
    """The full inverted index; None for a remote Dataset (full text needs an agent index)."""
    table_columns: tuple[str, ...]
    columns: Mapping[str, str]
    time_note: str
    max_index_rows: int | None = None
    """Most Log Entries one agent index (T19) over this Dataset may hold; None for the default cap,
    `index_admin.MAX_SLICE_ROWS`."""
    remote: RemoteSource | None = None

    @property
    def in_base_corpus(self) -> bool:
        return self.name in BASE_CORPUS


def _dataset(
    name: DatasetName,
    title: str,
    table_columns: tuple[str, ...],
    concepts: Mapping[str, str],
    time_note: str,
    **extra: object,
) -> Dataset:
    columns = {column: column for column in table_columns} | dict(concepts)
    return Dataset(
        name=name,
        title=title,
        table=ENTRY_TABLES[name],
        index=ENTRY_INDEXES.get(name),
        table_columns=table_columns,
        columns=MappingProxyType(columns),
        time_note=time_note,
        **extra,  # type: ignore[arg-type]
    )


TEXT_BENCH = RemoteSource(
    description=(
        "text_bench is about 1,000,000,000 OTel Log Entries (2025-09-23 00:00:00 to 00:29:25 UTC, about "
        "567,000 per second) in one 41 GB Parquet file on S3, queried in place without a full index"
    ),
    max_scan_span=timedelta(seconds=60),
    example_slice=("2025-09-23T00:10:00", "2025-09-23T00:10:10"),
    index_rows_note="about 8 seconds of text_bench",
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
        ),
        "openstack": _dataset(
            "openstack",
            "OpenStack",
            OS_COLUMNS,
            _OS_CONCEPTS,
            "ts is the time as logged, which carries no zone and is taken as UTC.",
        ),
        "text_bench": _dataset(
            "text_bench",
            "text_bench (OTel, remote Parquet)",
            TB_COLUMNS,
            _TB_CONCEPTS,
            "ts is the OTel Timestamp in UTC, to the microsecond (the view casts it in the server's time "
            "zone, which is UTC).",
            max_index_rows=5_000_000,
            remote=TEXT_BENCH,
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
        elif not self.index_ident:
            raise ScopeError(_no_index_message(self.dataset))
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
        """`schema.index`, for Evidence notes (which index answered); empty when there is none."""
        return f"{self.schema}.{self.index_ident}" if self.index_ident else ""

    @property
    def facts_table(self) -> sql.Identifier:
        """A remote Dataset's facts: (kind, value, n, first, last), computed by `make huge-setup`."""
        return sql.Identifier(self.schema, REMOTE_FACTS)

    @property
    def ownership_table(self) -> sql.Identifier:
        """The Ownership Table: (dataset, component, team). A stub until T25, see OWNERSHIP_NOTE."""
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

    For a remote Dataset a scope must name a slice of at most `max_scan_span`, or this raises a
    ScopeError that suggests one (statements without a scope bound their own reads).
    """
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown Role {role!r}; valid: {', '.join(ROLES)}")
    entry = dataset(dataset_name)
    schema = (settings if settings is not None else get_settings()).slm_schema
    start, end = _time_range(scope)
    if scope is not None and entry.remote is not None:
        check_remote_slice(entry, start, end)
    agent = agent_indexes.covering(schema, entry.name, start, end)
    return Relation(
        dataset=entry,
        schema=schema,
        table_ident=entry.table,
        index_ident=(entry.index or "") if agent is None else agent.name,
        agent_index=agent,
    )


def check_remote_slice(entry: Dataset, start: datetime | None, end: datetime | None) -> None:
    """ScopeError unless [start, end) is a slice a remote Dataset may be read over in place."""
    remote = entry.remote
    if remote is None:
        return
    limit = _seconds(remote.max_scan_span)
    first, last = remote.example_slice
    example = f"for example time_from='{first}', time_to='{last}'"
    if start is None or end is None:
        raise ScopeError(
            f"{remote.description}. Every call on it needs a time slice of at most {limit}: pass both "
            f"time_from and time_to ({example}, or time_from='last 1min'). For several full-text calls "
            f"on one slice, call create_index(dataset='{entry.name}', time_from=..., time_to=...) first "
            f"(at most {entry.max_index_rows or DEFAULT_MAX_INDEX_ROWS:,} Log Entries, "
            f"{remote.index_rows_note})."
        )
    if end - start > remote.max_scan_span:
        raise ScopeError(
            f"The slice {start.isoformat()} to {end.isoformat()} of {entry.name} is "
            f"{_seconds(end - start)} long; calls read at most {limit} of it in place. "
            f"{remote.description}. Narrow time_from and time_to ({example}), or split the period "
            "into several calls."
        )


def _no_index_message(entry: Dataset) -> str:
    remote = entry.remote
    what = f"{remote.description}" if remote is not None else f"{entry.name} has no full-text index"
    return (
        f"{what}, so full-text matching (query) needs an agent index over the slice. Call "
        f"create_index(dataset='{entry.name}', time_from=..., time_to=...) with this call's time_from "
        f"and time_to (at most {entry.max_index_rows or DEFAULT_MAX_INDEX_ROWS:,} Log Entries"
        + (f", {remote.index_rows_note}" if remote is not None else "")
        + "), then repeat this call; drop_index removes it afterwards. Without query, the call counts "
        "the slice without an index."
    )


def _seconds(span: timedelta) -> str:
    seconds = span.total_seconds()
    return f"{seconds:g} seconds" if seconds < 120 else f"{seconds / 60:g} minutes"


def _time_range(scope: object | None) -> tuple[datetime | None, datetime | None]:
    """The scope's time range as datetimes; None for an open side or a scope without one."""

    def bound(name: str) -> datetime | None:
        value = getattr(scope, name, None)
        return datetime.fromisoformat(value) if isinstance(value, str) else None

    return bound("time_from"), bound("time_to")
