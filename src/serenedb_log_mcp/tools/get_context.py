"""`get_context`: the Log Entries just before and after one entry, on the same node, request or
instance, or in a time window.

Every statement reads the base table (`relation(...).table`), never the inverted index (plan
section 2.4): the anchor is a point lookup by `entry_id`, and its neighbours are the nearest
entries in UTC time order (`epoch_utc`, then `entry_id`) that share its node, request or instance.
On 26.09.1.3 an index over a view dropped the `ts` filter next to a `line_no` predicate (#2); the
base table answers these lookups correctly and fast (about 0.1 s on 4.7 million BGL rows).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, get_args

from mcp.server.mcpserver import MCPServer
from psycopg import sql
from pydantic import Field

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, Relation, relation
from serenedb_log_mcp.db import QueryResult, fetch_all, fetch_limited
from serenedb_log_mcp.evidence import RoleParam
from serenedb_log_mcp.scope import ScopeError, build_scope
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import DatasetName, Q, bound, joined, q
from serenedb_log_mcp.tools._retrieval import ENTRY_COLUMNS, entry_column, entry_values, shape_message

ContextScope = Literal["auto", "node", "request", "instance", "window"]
SCOPES: tuple[ContextScope, ...] = get_args(ContextScope)
DEFAULT_SIDE = 10
MAX_SIDE = 50
DEFAULT_WINDOW_S = 60
MAX_WINDOW_S = 86_400

KEY_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "bgl": ("node",),
    "openstack": ("request_id", "instance_id", "instance_ref"),
}
"""The anchor columns a context scope can follow, per Dataset."""
SCOPE_DATASETS: Mapping[str, str] = {"node": "bgl", "request": "openstack", "instance": "openstack"}
"""The Dataset each source scope exists in; 'window' exists in both."""
PLACEHOLDER_NODES = frozenset({"NULL", "UNKNOWN_LOCATION", "-"})
"""BGL node values that name no node: the Log Entries that carry them share no source."""


@dataclass(frozen=True)
class Anchor:
    """The Log Entry the context is centred on."""

    entry_id: int
    ts: datetime
    epoch: float
    keys: Mapping[str, str | None]


def anchor_statement(rel: Relation, entry_id: int) -> Q:
    """The anchor's time and scope keys: a point lookup by entry_id on the base table."""
    columns = [
        entry_column(rel, name) for name in ("entry_id", "ts", "epoch", *KEY_COLUMNS[rel.dataset.name])
    ]
    return q(
        "SELECT {} FROM {} AS e WHERE {} = {}",
        sql.SQL(", ").join(columns),
        rel.table,
        entry_column(rel, "entry_id"),
        bound(entry_id),
    )


def resolve_scope(dataset: str, requested: str, anchor: Anchor) -> tuple[str, str | None]:
    """The scope to use and the anchor's value for it (None for 'window').

    'auto' takes the narrowest scope the anchor has: the node for BGL; for OpenStack the request,
    else the instance, else a time window.
    """
    keys = anchor.keys
    node = keys.get("node") if keys.get("node") not in PLACEHOLDER_NODES else None
    instance = keys.get("instance_id") or keys.get("instance_ref")
    if requested == "auto":
        if dataset == "bgl":
            requested = "node" if node else "window"
        elif keys.get("request_id"):
            requested = "request"
        else:
            requested = "instance" if instance else "window"
    if requested == "window":
        return "window", None
    home = SCOPE_DATASETS[requested]
    if home != dataset:
        valid = "'node' or 'window'" if dataset == "bgl" else "'request', 'instance' or 'window'"
        raise ScopeError(
            f"scope='{requested}' exists only in {'BGL' if home == 'bgl' else 'OpenStack'}; for "
            f"{dataset} use scope={valid}, or scope='auto'."
        )
    value = {"node": node, "request": keys.get("request_id"), "instance": instance}[requested]
    if not value:
        column = {"node": "node", "request": "request_id", "instance": "instance id"}[requested]
        others = (
            [s for s in ("request", "instance", "window") if s != requested] if home != "bgl" else ["window"]
        )
        raise ScopeError(
            f"The anchor (entry_id={anchor.entry_id}) has no {column}, so scope='{requested}' cannot "
            f"apply. Use scope={' or '.join(repr(s) for s in others)}."
        )
    return requested, value


@dataclass(frozen=True)
class Context:
    """The Log Entries that share the anchor's scope, optionally within `window_s` of the anchor.

    The window bounds `epoch_utc`, not `ts`: BGL's ts is local time, which repeats an hour at the
    daylight-saving fall-back, so a ts window would mix Log Entries an hour apart (#61 review).
    """

    rel: Relation
    anchor: Anchor
    scope: str
    value: str | None
    window_s: int | None = None

    def col(self, concept: str) -> sql.Composable:
        return entry_column(self.rel, concept)

    def predicate(self) -> Q:
        found: list[Q] = []
        if self.scope == "node":
            found.append(q("{} = {}", self.col("node"), bound(self.value)))
        elif self.scope == "request":
            found.append(q("{} = {}", self.col("request_id"), bound(self.value)))
        elif self.scope == "instance":
            # The instance column, or the UUID anywhere in the message (#2): API lines and events name
            # an instance only in their text. The pattern is bound, so its % needs no escaping.
            found.append(
                q(
                    "({} = {} OR {} LIKE {})",
                    self.col("instance_id"),
                    bound(self.value),
                    self.col("message"),
                    bound(f"%{self.value}%"),
                )
            )
        if self.window_s is not None:
            epoch, centre = self.col("epoch"), self.anchor.epoch
            found.append(
                q("{} >= {} AND {} < {}", epoch, bound(centre - self.window_s), epoch, bound(centre + self.window_s))
            )
        if not found:
            raise ValueError("a context needs a source scope or a time window")
        return joined(" AND ", found)

    def count_statement(self) -> Q:
        return q("SELECT count(*) FROM {} AS e WHERE {}", self.rel.table, self.predicate())

    def rows_statement(self, *, before: int, after: int) -> Q:
        """Up to `before` entries before the anchor, then the anchor and up to `after` after it."""
        selected = sql.SQL(", ").join(
            [self.col(name) for name in ENTRY_COLUMNS[self.rel.dataset.name]] + [self.col("message")]
        )
        position = sql.SQL("({}, {})").format(self.col("epoch"), self.col("entry_id"))
        key = (bound(self.anchor.epoch), bound(self.anchor.entry_id))
        return q(
            "SELECT * FROM (\n"
            "    (SELECT {}, {} AS epoch FROM {} AS e WHERE {} AND {} < ({}, {}) "
            "ORDER BY {} DESC, {} DESC LIMIT {})\n"
            "    UNION ALL\n"
            "    (SELECT {}, {} FROM {} AS e WHERE {} AND {} >= ({}, {}) ORDER BY {}, {} LIMIT {})\n"
            ") AS c ORDER BY epoch, entry_id",
            selected,
            self.col("epoch"),
            self.rel.table,
            self.predicate(),
            position,
            *key,
            self.col("epoch"),
            self.col("entry_id"),
            bound(before),
            selected,
            self.col("epoch"),
            self.rel.table,
            self.predicate(),
            position,
            *key,
            self.col("epoch"),
            self.col("entry_id"),
            bound(after + 1),  # the anchor itself comes first in this half
        )


# The tool ----------------------------------------------------------------------------------------------

SCOPE_WORDS = {
    "node": "on node {value}",
    "request": "in request {value}",
    "instance": "of instance {value} (its instance_id, or the UUID in the message)",
    "window": "on every source",
}
HYPOTHESIS_NOTE = (
    "Log Entries before the anchor are candidates, not established causes: state any link between them "
    "as a Hypothesis."
)


def register(server: MCPServer) -> None:
    """Register `get_context` on `server`."""

    @evidence_tool(server, title="Log Entries just before and after one entry")
    def get_context(
        role: RoleParam,
        dataset: Annotated[DatasetName, Field(description="'bgl' or 'openstack': the Dataset of entry_id.")],
        entry_id: Annotated[
            int,
            Field(
                ge=0, description="The anchor Log Entry: an entry_id from a search_logs row of this Dataset."
            ),
        ],
        scope: Annotated[
            ContextScope,
            Field(
                description="Which neighbours: auto (default: BGL the anchor's node; OpenStack its "
                "request, else its instance, else a window), node (BGL), request or instance "
                "(OpenStack; instance also matches the UUID in the message), or window (every source "
                "within window_s seconds)."
            ),
        ] = "auto",
        before: Annotated[
            int,
            Field(
                ge=0,
                le=MAX_SIDE,
                description=f"Log Entries before the anchor (default {DEFAULT_SIDE}, at most {MAX_SIDE}).",
            ),
        ] = DEFAULT_SIDE,
        after: Annotated[
            int,
            Field(
                ge=0,
                le=MAX_SIDE,
                description=f"Log Entries after the anchor (default {DEFAULT_SIDE}, at most {MAX_SIDE}).",
            ),
        ] = DEFAULT_SIDE,
        window_s: Annotated[
            int | None,
            Field(
                ge=1,
                le=MAX_WINDOW_S,
                description=f"Seconds on each side of the anchor. With scope='window' it is the window "
                f"(default {DEFAULT_WINDOW_S}); with another scope it limits the neighbours to that window. "
                "Omit it there to get the nearest entries however far apart they are.",
            ),
        ] = None,
    ) -> ToolOutcome:
        """The Log Entries just before and after one anchor entry, in time order, with the anchor flagged.

        Use it to see what happened around one failure, on the same BGL node, OpenStack request or
        instance, or on every source within a time window, once search_logs gave you its entry_id; use
        count_by or trend instead for how often something happens.
        Inputs: role, dataset, entry_id, and optional scope (auto, node, request, instance, window),
        before and after (default 10, at most 50 each) and window_s.
        Outputs: the Evidence envelope, whose rows hold entry_id, ts, offset_s (seconds from the
        anchor, negative before it), the node (BGL) or request_id and instance_ref (OpenStack),
        component, level, alert_tag (BGL), template_id, message (at most 240 characters) and anchor
        (true on the anchor row); summary holds anchor_entry_id, anchor_ts, scope, scope_value,
        window_s, entries_in_scope, before_returned, after_returned, first_ts and last_ts (of the
        returned rows).
        total_matches and entries_in_scope count the Log Entries in the whole context scope, not
        only the returned ones.
        """
        return run(role, dataset, entry_id, scope, before, after, window_s)


def run(
    role: Role, dataset: str, entry_id: int, scope: str, before: int, after: int, window_s: int | None
) -> ToolOutcome:
    rel = relation(dataset, role)
    lookup = anchor_statement(rel, entry_id)
    found = fetch_all(lookup.text, lookup.params, role=role)
    if not found.rows:
        raise ScopeError(
            f"No {dataset} Log Entry has entry_id={entry_id}. Take entry_id from a search_logs row of "
            f"dataset='{dataset}'; entry ids are per Dataset."
        )
    anchor = read_anchor(dataset, found.rows[0])
    chosen, value = resolve_scope(dataset, scope, anchor)
    window = DEFAULT_WINDOW_S if chosen == "window" and window_s is None else window_s
    start = end = None
    if window is not None:
        # The Data Scope states the window in logged time, like every other time range; the filter
        # itself is on epoch_utc, which differs from it only inside BGL's repeated fall-back hour.
        start, end = anchor.ts - timedelta(seconds=window), anchor.ts + timedelta(seconds=window)
    filters: dict[str, Any] = {"anchor_entry_id": anchor.entry_id, "context": chosen}
    if value is not None:
        filters[_SCOPE_FILTER[chosen]] = value
    filters["window_s"] = window
    data_scope = build_scope(
        dataset,
        time_from=None if start is None else start.isoformat(),
        time_to=None if end is None else end.isoformat(),
        known_datasets=DATASET_NAMES,
        **filters,
    )
    context = Context(relation(dataset, role, data_scope), anchor, chosen, value, window)
    count, rows = context.count_statement(), context.rows_statement(before=before, after=after)
    neighbours = fetch_limited(
        count.text, rows.text, count.params, before + after + 1, role=role, rows_params=rows.params
    )
    shaped = [shape_row(context, row) for row in neighbours.rows]
    # The rows are in (epoch_utc, entry_id) order with the anchor among them, so its position is the
    # number of entries before it (offset_s is rounded, so it cannot tell sub-millisecond neighbours).
    before_n = next(i for i, row in enumerate(shaped) if row[-1])
    after_n = len(shaped) - before_n - 1
    summary: dict[str, Any] = {
        "anchor_entry_id": anchor.entry_id,
        "anchor_ts": anchor.ts.isoformat(),
        "scope": chosen,
        "scope_value": value,
        "window_s": window,
        "entries_in_scope": neighbours.total,
        "before_returned": before_n,
        "after_returned": after_n,
        "first_ts": shaped[0][1] if shaped else None,
        "last_ts": shaped[-1][1] if shaped else None,
    }
    where = SCOPE_WORDS[chosen].format(value=value)
    if window is not None:
        where += f" within {window} s of the anchor in UTC time"
    notes = [
        f"Context: Log Entries {where}, in time order (UTC epoch, then entry_id); offset_s is the "
        "seconds from the anchor.",
        HYPOTHESIS_NOTE,
    ]
    if before_n < before:
        notes.append(f"Nothing earlier in this context: {before_n} Log Entries precede the anchor here.")
    if after_n < after:
        notes.append(f"Nothing later in this context: {after_n} Log Entries follow the anchor here.")
    truncation = (
        f"Showing {before_n} Log Entries before and {after_n} after the anchor, of {neighbours.total:,} "
        f"in this context; raise before or after (at most {MAX_SIDE} each) or set window_s to see others, "
        "and use count_by or trend for how often."
    )
    return ToolOutcome(
        result=QueryResult.combine(found, neighbours, total=neighbours.total, rows=shaped),
        scope=data_scope,
        columns=columns(dataset),
        summary=summary,
        notes=notes,
        rows=shaped,
        truncation_note=truncation,
    )


_SCOPE_FILTER = {"node": "node", "request": "request_id", "instance": "instance"}


def columns(dataset: str) -> list[str]:
    entry = list(ENTRY_COLUMNS[dataset])
    return [entry[0], entry[1], "offset_s", *entry[2:], "message", "anchor"]


def read_anchor(dataset: str, row: Sequence[Any]) -> Anchor:
    entry_id, ts, epoch, *keys = row
    return Anchor(int(entry_id), ts, float(epoch), dict(zip(KEY_COLUMNS[dataset], keys, strict=True)))


def shape_row(context: Context, row: Sequence[Any]) -> list[Any]:
    """entry_id, ts, offset_s, the other entry columns, the capped message and the anchor flag."""
    width = len(ENTRY_COLUMNS[context.rel.dataset.name])
    values = entry_values(row[:width])
    message, epoch = row[width], row[width + 1]
    offset = round(float(epoch) - context.anchor.epoch, 3) + 0.0  # + 0.0 turns -0.0 into 0.0
    is_anchor = int(values[0]) == context.anchor.entry_id
    return [values[0], values[1], offset, *values[2:], shape_message(message), is_anchor]
