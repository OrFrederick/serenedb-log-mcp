"""`create_index` and `drop_index`: the agent-managed index over one time slice (T19).

These are the only tools that are not read-only. The work happens in `index_admin.py`, on a separate
write connection; routing a later call to the agent index happens in the resolver
(`corpus.datasets.relation`), so no other tool knows about agent indexes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, DatasetName
from serenedb_log_mcp.corpus.facts import dataset_facts
from serenedb_log_mcp.evidence import DataScope, RoleParam
from serenedb_log_mcp.index_admin import MAX_SLICE_ROWS, IndexReport, create_agent_index, drop_agent_index
from serenedb_log_mcp.scope import ScopeError, build_scope, time_bounds
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool

BUILDS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
"""create_index adds an index and never removes anything; the same slice again reuses the index."""
DROPS = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
"""drop_index removes an index (never data); dropping it again changes nothing more."""

CREATE_COLUMNS = (
    "index",
    "dataset",
    "time_from",
    "time_to",
    "rows",
    "build_ms",
    "create_ms",
    "refresh_ms",
    "status",
)
DROP_COLUMNS = ("index", "dataset", "time_from", "time_to", "rows", "drop_ms", "status")


def register(server: MCPServer) -> None:
    """Register `create_index` and `drop_index` on `server`."""

    @evidence_tool(server, title="Build an agent index over one time slice", annotations=BUILDS)
    def create_index(
        role: RoleParam,
        dataset: Annotated[DatasetName, Field(description="'bgl' or 'openstack': the Dataset to index.")],
        time_from: Annotated[
            str,
            Field(
                description="Start of the slice, inclusive: ISO-8601 without a time zone, for example "
                "'2005-08-22', or a relative range such as 'last 7d', which ends at the Dataset's last "
                "Log Entry."
            ),
        ],
        time_to: Annotated[
            str | None,
            Field(
                description="End of the slice, exclusive, ISO-8601 without a time zone. Omit it to end "
                "after the Dataset's last Log Entry, and always omit it with a relative time_from."
            ),
        ] = None,
    ) -> ToolOutcome:
        """Build an agent index: an inverted index over one time slice of one Dataset, so the
        full-text calls that follow on that slice do less work.

        Purpose: before several count_by, trend or search_logs calls with a query over the same
        period, for example while drilling into one failure wave. It costs build_ms once, which grows
        with the Log Entries in the slice; every later call whose Data Scope lies inside the slice
        reads it automatically and names it in its notes and data_scope.agent_indexes. The counts
        are the same as from the Dataset's full index; only the time changes, so it does not pay off
        for one or two queries. It pays off fastest for full-text counts grouped by node, Component
        or Pattern over a small slice of a large Dataset, and not for fetching example Log Entries.
        Not read-only: it adds an index to the database. At most one agent
        index per Dataset and at most 1,000,000 Log Entries per slice; asking for the same slice
        again reuses the index. Call drop_index when you are done with the slice.
        Inputs: role, dataset, time_from, and optional time_to.
        Outputs: the Evidence envelope. rows: one row with columns index (the name to pass to
        drop_index), dataset, time_from, time_to, rows (Log Entries in the slice), build_ms
        (create_ms plus refresh_ms: until the index is usable), create_ms, refresh_ms and status
        (built or reused). summary: the same values plus qualified_name (schema.index) and
        max_rows. sql: every statement that ran, including the CREATE INDEX.
        """
        facts = dataset_facts(dataset, role)
        if facts.last is None:
            raise ScopeError(
                f"{dataset} has no Log Entries in this database, so there is nothing to index. Call "
                "describe_corpus for the Datasets that are loaded."
            )
        scope = build_scope(
            [dataset],
            time_from=time_from,
            time_to=time_to,
            corpus_end={dataset: facts.last},
            known_datasets=DATASET_NAMES,
        )
        start, end = time_bounds(scope)
        if start is None:  # unreachable: time_from is required and always resolves to a start
            raise ScopeError("Pass time_from, the start of the slice to index, for example '2005-08-22'.")
        if end is None:
            end = facts.last.replace(microsecond=0) + timedelta(seconds=1)
            scope = scope.model_copy(update={"time_to": end.isoformat()})
        report = create_agent_index(dataset, start, end)
        return _created(report, scope)

    @evidence_tool(server, title="Drop an agent index", annotations=DROPS)
    def drop_index(
        role: RoleParam,
        name: Annotated[
            str,
            Field(
                description="The agent index to drop, exactly as create_index returned it, for "
                "example 'agent_idx_bgl_0a1b2c3d' (a schema prefix such as 'slm.' is allowed).",
                max_length=200,
            ),
        ],
    ) -> ToolOutcome:
        """Drop an agent index that create_index built.

        Purpose: clean up when you are done with a slice; calls on that slice then read the
        Dataset's full index again, with the same counts. Not read-only. It refuses every index
        that create_index did not build, including the Datasets' full indexes.
        Inputs: role, name.
        Outputs: the Evidence envelope. rows: one row with columns index, dataset, time_from,
        time_to, rows (Log Entries the index held), drop_ms and status ('dropped'). summary: the
        same values plus qualified_name.
        """
        report = drop_agent_index(name)
        index = report.index
        scope = DataScope(
            datasets=[index.dataset], time_from=index.time_from.isoformat(), time_to=index.time_to.isoformat()
        )
        values = _values(report, DROP_COLUMNS, drop_ms=round(report.drop_ms, 1))
        return ToolOutcome(
            result=report.result,
            scope=scope,
            columns=DROP_COLUMNS,
            rows=[list(values.values())],
            total_matches=1,
            summary={**values, "qualified_name": index.qualified_name},
            notes=[
                f"Dropped the agent index {index.qualified_name} ({index.rows:,} Log Entries of "
                f"{index.dataset} from {index.time_from.isoformat()} to {index.time_to.isoformat()}). "
                "Calls on that slice read the Dataset's full index again; the counts stay the same."
            ],
        )


def _created(report: IndexReport, scope: DataScope) -> ToolOutcome:
    index = report.index
    values = _values(
        report,
        CREATE_COLUMNS,
        build_ms=round(report.build_ms, 1),
        create_ms=round(report.create_ms, 1),
        refresh_ms=round(report.refresh_ms, 1),
    )
    span = (
        f"{index.rows:,} Log Entries of {index.dataset} from {index.time_from.isoformat()} "
        f"to {index.time_to.isoformat()}"
    )
    if report.status == "reused":
        first = (
            f"The agent index {index.qualified_name} over the {span} already existed, so nothing was built."
        )
    else:
        first = (
            f"Built the agent index {index.qualified_name} over the {span} in {values['build_ms']} ms "
            f"(CREATE INDEX {values['create_ms']} ms, VACUUM (REFRESH_TABLE) {values['refresh_ms']} ms)."
        )
    return ToolOutcome(
        result=report.result,
        scope=scope,
        columns=CREATE_COLUMNS,
        rows=[list(values.values())],
        total_matches=1,
        summary={**values, "qualified_name": index.qualified_name, "max_rows": MAX_SLICE_ROWS},
        notes=[
            first,
            "count_by, trend and search_logs calls whose Data Scope lies inside this slice now read this "
            "index instead of the full index, and say so in their notes and data_scope.agent_indexes; the "
            "counts stay the same. Baselines that reach outside the slice still read the full index.",
            f"Call drop_index(name='{index.name}') when you are done with this slice; there is at most "
            "one agent index per Dataset.",
        ],
    )


def _values(report: IndexReport, columns: tuple[str, ...], **times: float) -> dict[str, Any]:
    """One output row as a dict in column order."""
    index = report.index
    known: dict[str, Any] = {
        "index": index.name,
        "dataset": index.dataset,
        "time_from": index.time_from.isoformat(),
        "time_to": index.time_to.isoformat(),
        "rows": index.rows,
        "status": report.status,
        **times,
    }
    return {column: known[column] for column in columns}
