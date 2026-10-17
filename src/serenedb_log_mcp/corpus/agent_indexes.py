"""Agent indexes (T19): inverted indexes the agent builds over a time slice of one Dataset.

`create_index` builds a partial inverted index over the Dataset's canonical table
(`CREATE INDEX … USING inverted (…) WHERE ts >= … AND ts < …`, verified on SereneDB 26.10.1 and,
in the PR #59 review, on 26.09.1.3) and
`drop_index` removes it; both live in `index_admin.py` and run on a separate write connection. This
module is the pure part both sides and the resolver share, with no database access:

- the names (`agent_idx_<dataset>_<8 hex>`, generated server-side) and the catalog marker, a
  `COMMENT ON INDEX` written in the same transaction as the index. An index counts as an agent index
  only when its name has the generated form and its comment carries a valid marker, so `drop_index`
  can never drop a canonical index or one somebody made by hand;
- the process registry, at most one agent index per schema and Dataset, which
  `corpus.datasets.relation()` reads to route a Data Scope inside a slice to that index. It is filled
  by `create_index` (and by adopting existing agent indexes from the catalog) and emptied by
  `drop_index`; another server process sees an agent index only after its own `create_index` call
  adopted it;
- use capture: `Relation.index` records an agent index when a statement reads it, and
  `toolkit.evidence_tool` turns the records into a note and `DataScope.agent_indexes`, so every
  tool says which index answered without a change in any tool module.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = [
    "MARKER",
    "NAME_RE",
    "AgentIndex",
    "capture_use",
    "clear",
    "covering",
    "is_agent_name",
    "new_name",
    "record_use",
    "register",
    "registered",
    "registered_all",
    "unregister",
]

_DATASETS = ("bgl", "openstack")
NAME_RE = re.compile(r"^agent_idx_(bgl|openstack)_[0-9a-f]{8}$")
"""The only names `create_index` generates and `drop_index` accepts."""
MARKER = "serenedb-log-mcp agent index "
"""Prefix of the index comment that marks an index as built by `create_index`; JSON follows."""


@dataclass(frozen=True)
class AgentIndex:
    """One agent index: a partial inverted index over `time_from` (inclusive) to `time_to` (exclusive)."""

    schema: str
    name: str
    dataset: str
    time_from: datetime
    time_to: datetime
    rows: int

    @property
    def qualified_name(self) -> str:
        """`schema.name`, for notes and the Data Scope."""
        return f"{self.schema}.{self.name}"

    def covers(self, start: datetime | None, end: datetime | None) -> bool:
        """Does the range [start, end) lie inside the slice? An open side (None) never does."""
        if start is None or end is None:
            return False
        return self.time_from <= start and end <= self.time_to

    def marker(self) -> str:
        """The index comment that marks this index as an agent index."""
        payload = {
            "dataset": self.dataset,
            "time_from": self.time_from.isoformat(),
            "time_to": self.time_to.isoformat(),
            "rows": self.rows,
        }
        return MARKER + json.dumps(payload, sort_keys=True)

    @classmethod
    def from_marker(cls, schema: str, name: str, comment: str | None) -> AgentIndex | None:
        """The agent index an index comment describes; None unless name and marker are both valid."""
        match = NAME_RE.fullmatch(name)
        if match is None or not comment or not comment.startswith(MARKER):
            return None
        try:
            payload: Any = json.loads(comment[len(MARKER) :])
            index = cls(
                schema=schema,
                name=name,
                dataset=str(payload["dataset"]),
                time_from=datetime.fromisoformat(str(payload["time_from"])),
                time_to=datetime.fromisoformat(str(payload["time_to"])),
                rows=int(payload["rows"]),
            )
        except (ValueError, TypeError, KeyError):
            return None
        return index if index.dataset == match.group(1) else None

    def routing_note(self) -> str:
        """The note for Evidence that this index answered."""
        return (
            f"Full-text matching on {self.dataset} read the agent index {self.qualified_name} instead "
            f"of the Dataset's full index, because this Data Scope lies inside its slice "
            f"({self.time_from.isoformat()} to {self.time_to.isoformat()}, {self.rows:,} Log Entries). "
            "It holds exactly the Log Entries of that slice, so the counts are the same as from the "
            f"full index; only the query time differs. drop_index(name='{self.name}') removes it."
        )


def new_name(dataset: str, token: Callable[[], str] = lambda: secrets.token_hex(4)) -> str:
    """A fresh agent index name for `dataset`, for example `agent_idx_bgl_0a1b2c3d`."""
    if dataset not in _DATASETS:
        raise ValueError(f"unknown Dataset {dataset!r}; valid: {', '.join(_DATASETS)}")
    name = f"agent_idx_{dataset}_{token()}"
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"generated agent index name {name!r} does not match {NAME_RE.pattern}")
    return name


def is_agent_name(name: str) -> bool:
    """Has `name` the form `create_index` generates? (The catalog marker decides the rest.)"""
    return NAME_RE.fullmatch(name) is not None


# Registry -------------------------------------------------------------------------------------------

_registry: dict[tuple[str, str], AgentIndex] = {}
_lock = threading.Lock()


def register(index: AgentIndex) -> None:
    """Make `index` the agent index of its schema and Dataset (replacing any earlier one)."""
    with _lock:
        _registry[(index.schema, index.dataset)] = index


def unregister(schema: str, name: str) -> AgentIndex | None:
    """Forget the agent index `schema.name`; returns it, or None when it was not registered."""
    with _lock:
        for key, index in list(_registry.items()):
            if index.schema == schema and index.name == name:
                return _registry.pop(key)
    return None


def registered(schema: str, dataset: str) -> AgentIndex | None:
    """The agent index of one schema and Dataset, if any."""
    with _lock:
        return _registry.get((schema, dataset))


def registered_all() -> list[AgentIndex]:
    """Every registered agent index."""
    with _lock:
        return list(_registry.values())


def clear() -> None:
    """Forget every agent index (tests; the indexes themselves stay in the database)."""
    with _lock:
        _registry.clear()


def covering(schema: str, dataset: str, start: datetime | None, end: datetime | None) -> AgentIndex | None:
    """The agent index whose slice holds the whole range [start, end), if one is registered."""
    index = registered(schema, dataset)
    return index if index is not None and index.covers(start, end) else None


# Use capture ----------------------------------------------------------------------------------------

_used: ContextVar[list[AgentIndex] | None] = ContextVar("agent_indexes_used", default=None)


@contextmanager
def capture_use() -> Iterator[list[AgentIndex]]:
    """Collect the agent indexes that statements read inside this block, each once, in order.

    The SDK runs a sync tool in a worker thread with a copy of the caller's context, so each tool
    call has its own capture and concurrent calls never see each other's records.
    """
    used: list[AgentIndex] = []
    token = _used.set(used)
    try:
        yield used
    finally:
        _used.reset(token)


def record_use(index: AgentIndex) -> None:
    """Note that a statement reads `index`; ignored outside `capture_use()`."""
    used = _used.get()
    if used is not None and index not in used:
        used.append(index)
