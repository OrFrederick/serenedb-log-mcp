"""The Evidence contract: the Role enum, the Data Scope and the Evidence envelope.

Every Evidence tool answers with one `Evidence` record (plan section 2.3). It travels as
`structuredContent`, with the same JSON mirrored in one text block, and a copy of it is appended to
the Evidence Ledger so the answer can cite it as `[E<n>]` and so `get_evidence`, `verify_answer`,
`export_investigation` and the ledger replay can find it again.

The field descriptions are part of the product: they are what the agent reads in the output schema,
and `docs/tools.md` (T8) is generated from them.
"""

from __future__ import annotations

from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The Role names are defined once, in config; the Evidence contract re-exports the enum so that
# tools import Role and RoleParam from one place.
from serenedb_log_mcp.config import Role

__all__ = [
    "EVIDENCE_ID_PATTERN",
    "ROLE_DESCRIPTION",
    "DataScope",
    "Evidence",
    "Role",
    "RoleParam",
    "ScopeFilters",
]

EVIDENCE_ID_PATTERN = r"^E[1-9][0-9]*$"

ROLE_DESCRIPTION = (
    "Who is asking: 'engineer' investigates one failure and needs example Log Entries and nearby "
    "events; 'team_lead' needs recurring problems with counts and trends per Component and Team, and "
    "receives at most 3 sample Log Entries from a search; 'manager' needs measured frequency and "
    "affected Components only, and receives no raw Log Entries (totals and facets, no rows). Every "
    "result carries the answer_frame for this Role; get_role_brief(role) has the full playbook."
)

RoleParam = Annotated[Role, Field(description=ROLE_DESCRIPTION)]
"""The `role` parameter every Evidence tool declares: `def search_logs(role: RoleParam, ...)`."""


class ScopeFilters(BaseModel):
    """The filters a calculation applied.

    `pattern_ids` and `family_id` are the shared filters every Evidence tool accepts (plan section
    2.3). Tools add their own filters, for example `query`, `components`, `levels` or `group_by`,
    through `ScopeFilters.of(...)`; they are kept as given so the Data Scope stays a complete record
    of what was filtered.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    pattern_ids: list[str] | None = Field(
        default=None,
        description="Pattern ids the calculation was restricted to (16-hex template ids from "
        "list_patterns); null when no Pattern filter was applied.",
    )
    family_id: str | None = Field(
        default=None,
        description="Pattern family the calculation was restricted to; null when no family filter "
        "was applied.",
    )

    @classmethod
    def of(cls, **filters: Any) -> ScopeFilters:
        """Build filters from keyword arguments, including tool-specific ones."""
        return cls.model_validate(filters)


class DataScope(BaseModel):
    """The Datasets, time range and filters one calculation covered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    datasets: list[str] = Field(
        min_length=1,
        description="The Datasets the calculation covered, for example ['bgl'] or ['bgl', "
        "'openstack']. Never empty: every number is scoped to named Datasets.",
    )
    time_from: str | None = Field(
        default=None,
        description="Start of the covered time range, inclusive, as ISO-8601; null means the "
        "Dataset's first Log Entry.",
    )
    time_to: str | None = Field(
        default=None,
        description="End of the covered time range, exclusive, as ISO-8601; null means the "
        "Dataset's last Log Entry.",
    )
    time_input: str | None = Field(
        default=None,
        description="The time range as the caller asked for it, for example 'last 7d', before it "
        "was resolved; null when the caller gave absolute timestamps.",
    )
    resolved_against: str | None = Field(
        default=None,
        description="What a relative time_input was resolved against, for example 'bgl corpus end "
        "2006-01-04T00:00:00'. Relative times resolve against the corpus end date, not today.",
    )
    time_zone: str | None = Field(
        default=None,
        description="The clock of time_from, time_to, resolved_against and every time the tool "
        "computed (buckets, first and last seen, baseline periods), for example 'UTC'; null when "
        "the tool reports each Dataset's ts as logged (BGL's ts is US Pacific local time).",
    )
    time_columns: dict[str, str] | None = Field(
        default=None,
        description="Per Dataset, the column the time range, buckets and baselines were computed on, "
        "for example {'bgl': 'epoch_utc', 'openstack': 'ts'}: BGL's ts is local time, so its time "
        "arithmetic runs on epoch_utc, while OpenStack's ts is UTC. null when the tool did no time "
        "arithmetic of its own.",
    )
    filters: ScopeFilters = Field(
        default_factory=ScopeFilters,
        description="The filters the calculation applied, including the shared pattern_ids and family_id.",
    )
    agent_indexes: dict[str, str] | None = Field(
        default=None,
        description="Per Dataset, the agent index that answered the full-text matching instead of the "
        "Dataset's full index, for example {'bgl': 'slm.agent_idx_bgl_0a1b2c3d'}: create_index built it "
        "over a time slice that contains this Data Scope, so the counts are the same. null when every "
        "Dataset used its full index.",
    )


class Evidence(BaseModel):
    """The result of one executed tool call: its query, parameters, Data Scope, counts and rows.

    Cite it as `[E<n>]` next to every number taken from it. `total_matches` and `truncated` say
    whether the rows are the whole answer or a sample; a sample is never a frequency.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(
        pattern=EVIDENCE_ID_PATTERN,
        description="Id of this Evidence in the Evidence Ledger, for example 'E3'. Cite it as [E3] "
        "next to every number taken from this result, and pass it to get_evidence to fetch it "
        "again later.",
    )
    tool: str = Field(description="The tool that produced this Evidence.")
    role: Role = Field(description="The Role this call was made for.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="The parameters this tool ran with, including the defaults that applied, "
        "without role. Together with tool it is the call that produced this result, so it can be "
        "repeated or reported.",
    )
    sql: list[str] = Field(
        default_factory=list,
        description="The SQL statements that actually ran, in order, with %s where parameters were "
        "bound. These are the queries behind every number in this result.",
    )
    params: list[list[Any]] = Field(
        default_factory=list,
        description="The bound parameters, one list per statement in sql, in the same order.",
    )
    data_scope: DataScope = Field(
        description="The Datasets, time range and filters this result covered. State it next to every number."
    )
    total_matches: int = Field(
        ge=0,
        description="Exact number of units that matched in the Data Scope, counted by SereneDB: "
        "Log Entries for a search, groups for count_by, buckets for trend, Incidents for "
        "list_incidents. It is the frequency; the returned rows are not.",
    )
    returned: int = Field(
        ge=0,
        description="How many of the matching units this result returned. Lower than total_matches "
        "means you are looking at a sample.",
    )
    truncated: bool = Field(
        description="True when returned < total_matches, so matching units exist that this result "
        "does not show. Never read a truncated row count as a frequency; use count_by or trend."
    )
    query_ms: float = Field(
        ge=0,
        description="Wall-clock time SereneDB took for the statements in sql, in milliseconds.",
    )
    columns: list[str] = Field(
        default_factory=list,
        description="Column names for the values in rows, in order.",
    )
    rows: list[list[Any]] = Field(
        default_factory=list,
        description="The returned rows, each a list of values in the order of columns. Empty when "
        "nothing matched, or when the Role does not receive raw Log Entries.",
    )
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Values SereneDB computed for this result, so they never have to be added up "
        "by hand: usually scalars, or small lists and maps such as facets, for example "
        "entries_total, groups_total, share_pct, top_n_sum, other_count, incidents_total and the "
        "top Components or levels of a search.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Notes about this result: truncation, retries after a database restart, "
        "fallbacks, and hints on what to call next.",
    )
    answer_frame: list[str] = Field(
        default_factory=list,
        description="The sections the answer for this Role must have, for example Observed Facts, "
        "Hypotheses, Unknowns, Next Step.",
    )
    not_measured: list[str] = Field(
        default_factory=list,
        description="Things these logs do not measure, so they must not be claimed from this "
        "Evidence, for example financial loss, downtime or customer impact.",
    )

    @model_validator(mode="after")
    def _truncated_matches_the_counts(self) -> Self:
        expected = self.returned < self.total_matches
        if self.truncated != expected:
            raise ValueError(
                f"truncated={self.truncated} contradicts returned={self.returned} and "
                f"total_matches={self.total_matches}; set truncated to {expected}"
            )
        return self
