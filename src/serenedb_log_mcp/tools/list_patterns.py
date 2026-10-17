"""`list_patterns`: the Pattern catalogue of one Dataset, read first to choose Patterns (#7, T14).

One statement over the catalogue table (`relation(...).patterns_table`, built by
sql/derive/20_patterns.sql): every Pattern of the Dataset with its Component, level, Log Entry count,
first and last seen, Pattern Family and template text. The catalogue is small (465 BGL and 73
OpenStack Patterns on the Base Corpus), so the statement returns all of it and the tool pages it in
Python (`_catalogue.paginate`); the Evidence still names the statement behind every row.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, relation
from serenedb_log_mcp.corpus.facts import dataset_facts
from serenedb_log_mcp.db import QueryFailed, fetch_all
from serenedb_log_mcp.evidence import RoleParam
from serenedb_log_mcp.scope import ScopeError, build_scope, validate_choice
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import DatasetName, FamilyIdParam, PatternIdsParam
from serenedb_log_mcp.tools._catalogue import (
    CHOOSE_NOTE,
    COLUMNS,
    NO_MESSAGE,
    PAGE_TOKENS,
    WHOLE_DATASET_NOTE,
    Page,
    catalogue_row,
    catalogue_statement,
    component_index,
    missing_catalogue,
    paginate,
)

MAX_PAGE = 100

FAMILY_NOTE = (
    "family_id is set where a Pattern Family holds more than one Pattern: the same message with a "
    "known wording drift, such as a trailing 'over <*> seconds'. Pass it as family_id to count_by or "
    "trend to count both wordings together."
)
NO_MESSAGE_NOTE = f"'{NO_MESSAGE}' is the Pattern of the Log Entries whose message is empty."


def register(server: MCPServer) -> None:
    """Register `list_patterns` on `server`."""

    @evidence_tool(server, title="Pattern catalogue of a Dataset: read it first to choose Patterns")
    def list_patterns(
        role: RoleParam,
        dataset: Annotated[
            DatasetName,
            Field(description="'bgl' or 'openstack': one catalogue per call."),
        ],
        component: Annotated[
            str | None,
            Field(
                description="Optional: only the Patterns whose most frequent Component is this one, for "
                "example 'KERNEL' or 'nova.compute.manager'. summary.components lists them.",
                max_length=200,
            ),
        ] = None,
        pattern_ids: PatternIdsParam = None,
        family_id: FamilyIdParam = None,
        page: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_PAGE,
                description="Which page of a catalogue too large for one result (default 1); "
                "summary.pages_total says how many there are.",
            ),
        ] = 1,
    ) -> ToolOutcome:
        """The Pattern catalogue of one Dataset: each Pattern's id, Component, level, count and text.

        Read the catalogue first, before counting or searching: choose the template_id values whose
        text is direct evidence for the question and pass them as pattern_ids to count_by, trend and
        search_logs, which count and fetch by them exactly; if no Pattern is direct evidence, choose
        none and state that as an Unknown instead of stretching a near match.
        Use list_patterns to choose Patterns for a concept, search_patterns when the catalogue is too
        long to read (BGL comes in several pages), and top_patterns where available (otherwise
        count_by with group_by='pattern') to rank Patterns by frequency within a Data Scope.
        A catalogue larger than about 8k tokens is paged by Component: summary.components says which
        page holds each Component, so read the pages you need or pass component.
        Inputs: role, dataset, and optional component, pattern_ids or family_id (look up given
        Patterns, for example ids from count_by) and page (default 1).
        Outputs: the Evidence envelope, whose rows hold template_id, component, level (the most
        frequent ones), entries, first_seen, last_seen (whole Dataset, ts as logged), family_id (only
        when the Pattern Family has more than one Pattern) and template_text, grouped by Component and
        largest first; summary holds patterns_total, entries_total, page, pages_total,
        page_components and components; total_matches counts Patterns, not Log Entries.
        """
        component_ = None
        if component is not None and component.strip():
            known = dataset_facts(dataset, role).components
            component_ = validate_choice("component", component.strip(), known)
        scope = build_scope(
            dataset,
            known_datasets=DATASET_NAMES,
            pattern_ids=pattern_ids,
            family_id=family_id,
            component=component_,
        )
        rel = relation(dataset, role, scope)
        statement = catalogue_statement(
            rel,
            component=component_,
            pattern_ids=scope.filters.pattern_ids or (),
            family_id=scope.filters.family_id,
        )
        try:
            result = fetch_all(statement.text, statement.params, role=role)
        except QueryFailed as exc:
            if (missing := missing_catalogue(exc, rel)) is not None:
                raise missing from exc
            raise
        rows = [catalogue_row(row) for row in result.rows]
        pages = paginate(rows)
        if page > len(pages):
            raise ScopeError(
                f"page={page} is past the last page: this catalogue has {len(pages)} "
                f"page{'s' if len(pages) != 1 else ''}. Pass page between 1 and {len(pages)}."
            )
        shown = pages[page - 1]
        summary: dict[str, Any] = {
            "patterns_total": len(rows),
            "entries_total": sum(int(row[3]) for row in rows),
            "page": page,
            "pages_total": len(pages),
            "page_components": shown.components,
            "components": component_index(pages),
        }
        return ToolOutcome(
            result=result,
            scope=scope,
            columns=COLUMNS,
            summary=summary,
            notes=_notes(dataset, rel.dataset.time_note, shown, rows, component_),
            rows=[list(row) for row in shown.rows],
            total_matches=len(rows),
            truncation_note=_paging_note(dataset, shown, len(pages), len(rows)),
        )


def _notes(
    dataset: str, time_note: str, shown: Page, rows: list[list[Any]], component: str | None
) -> list[str]:
    if not rows:
        return [_no_match(dataset, component)]
    notes = [CHOOSE_NOTE, WHOLE_DATASET_NOTE, f"first_seen and last_seen are cut to the second; {time_note}"]
    if any(row[6] is not None for row in shown.rows):
        notes.append(FAMILY_NOTE)
    if any(row[7] == NO_MESSAGE for row in shown.rows):
        notes.append(NO_MESSAGE_NOTE)
    return notes


def _no_match(dataset: str, component: str | None) -> str:
    if component is not None:
        return (
            f"No Pattern of {dataset} has {component!r} as its most frequent Component; its Log Entries "
            f"belong to Patterns listed under other Components. Call list_patterns(dataset='{dataset}') "
            "without component, or count_by(group_by='pattern') with components=[...] for that "
            "Component's Patterns in a Data Scope."
        )
    return (
        f"No Pattern of {dataset} matched the pattern_ids or family_id given. Check the ids: they are the "
        f"template_id and family_id values that list_patterns(dataset='{dataset}') returns. If no "
        "Pattern is direct evidence for the question, state that as an Unknown."
    )


def _paging_note(dataset: str, shown: Page, pages: int, patterns: int) -> str:
    """The note for a result that shows one page of several (the toolkit drops it otherwise)."""
    if shown.number < pages:
        other = f"list_patterns(dataset='{dataset}', page={shown.number + 1}) for the next page"
    else:
        other = f"list_patterns(dataset='{dataset}', page=N) with N from 1 to {pages - 1} for the others"
    return (
        f"Page {shown.number} of {pages}: {len(shown.rows)} of {patterns:,} Patterns, Components "
        f"{', '.join(shown.components)}. The catalogue is paged by Component to stay near "
        f"{PAGE_TOKENS:,} tokens; summary.components says which pages hold each Component. Call "
        f"{other}, pass component for one Component, or use search_patterns to find Patterns by words."
    )
