"""`describe_corpus`: what data exists, so the agent can scope every later call.

Two statements per Dataset, both cached for the life of the process (`corpus/facts.py`): the
facts (rows, time range, Components, levels, alert categories, each with counts) that count_by and
trend already use to validate filters, and the profile (the Pattern count and the Team of each
Component). The Base Corpus does not change while the server runs, so a later call returns the same
numbers with the statements and query time of the first computation.

first_entry, last_entry and corpus_end are UTC (#73), the clock on which search_logs, count_by and
trend read their time inputs and resolve relative times: the facts' first and last logged ts are
converted with the Dataset's time zone (`clock.utc_range`). A note per Dataset says how its logged ts
relates to UTC.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from serenedb_log_mcp.clock import UTC_ZONE, clock_note, utc_range
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, OWNERSHIP_NOTE
from serenedb_log_mcp.corpus.datasets import dataset as registry_entry
from serenedb_log_mcp.corpus.facts import DatasetFacts, DatasetProfile, dataset_facts, dataset_profile
from serenedb_log_mcp.db import QueryResult
from serenedb_log_mcp.evidence import RoleParam
from serenedb_log_mcp.scope import build_scope
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import NULL_LABELS, DatasetName, iso

COLUMNS = ["dataset", "kind", "value", "entries", "team"]
KINDS = (("component", "components"), ("level", "levels"), ("alert_category", "alert_categories"))

LOG_ENTRIES_NOTE = (
    "Every number here counts Log Entries (lines), not Incidents: one failing node can write thousands "
    "of lines about a single problem."
)
TIME_NOTE = (
    "Relative times such as time_from='last 7d' resolve against each Dataset's corpus_end (its last Log "
    "Entry), not against today; the Datasets are years apart, so scope a time range per Dataset."
)
CLOCK_LEAD = (
    "first_entry, last_entry and corpus_end are UTC, the clock on which search_logs, count_by and trend "
    "read time_from and time_to; Log Entry rows show ts as logged."
)
"""The first sentence of describe_corpus' clock note (`clock.clock_note`)."""
CACHE_NOTE = (
    "These facts are computed once per server process and then served from a cache (the Base Corpus "
    "does not change while the server runs); sql and query_ms are those of the first computation."
)


def register(server: MCPServer) -> None:
    """Register `describe_corpus` on `server`."""

    @evidence_tool(server, title="Describe the Datasets: size, time range, Components, Teams")
    def describe_corpus(
        role: RoleParam,
        dataset: Annotated[
            DatasetName | None,
            Field(description="'bgl' or 'openstack'; omit it to describe the whole Base Corpus (both)."),
        ] = None,
    ) -> ToolOutcome:
        """What data exists: per Dataset its size, time range, corpus end, Components, levels and Patterns.

        Call it first to learn the valid Dataset names, time ranges and filter values; it describes
        and does not filter, so use count_by for counts inside a Data Scope and search_logs for
        example Log Entries.
        Inputs: role, and optional dataset (default: both Datasets of the Base Corpus).
        Outputs: the Evidence envelope, whose rows hold one Component (with its Team from the
        Ownership Table), level or BGL alert category each, in the columns dataset, kind, value,
        entries and team; summary.datasets holds per Dataset entries, first_entry, last_entry and
        corpus_end (UTC, the clock of every time input; corpus_end is what relative times resolve
        against), the numbers of components, levels, alert_categories, teams and patterns (distinct
        Pattern ids), and entries_with_pattern.
        Every count is Log Entries, not Incidents.
        """
        names = [dataset] if dataset else list(DATASET_NAMES)
        results: list[QueryResult] = []
        rows: list[list[Any]] = []
        described: dict[str, Any] = {}
        notes = [LOG_ENTRIES_NOTE, TIME_NOTE, OWNERSHIP_NOTE]
        ranges: dict[str, tuple[datetime, datetime]] = {}
        for name in names:
            found = dataset_facts(name, role)
            profile = dataset_profile(name, role)
            results += [part for part in (found.result, profile.result) if part is not None]
            rows += _rows(found, profile)
            described[name] = _summary(found, profile)
            if found.first is not None and found.last is not None:
                ranges[name] = utc_range(name, found.first, found.last)
            if not found.entries:
                notes.append(f"{name} has no Log Entries in this database.")
            elif not profile.patterns:
                notes.append(
                    f"No Pattern ids are assigned in {name} yet (the Pattern dictionary has not been "
                    "built), so pattern_ids filters match nothing there."
                )
        notes.append(clock_note(ranges, lead=CLOCK_LEAD, on_epoch=None))
        notes += [f"{name}: {registry_entry(name).time_note}" for name in names if name not in ranges]
        notes.append(CACHE_NOTE)
        scope = build_scope(names, known_datasets=DATASET_NAMES).model_copy(update={"time_zone": UTC_ZONE})
        return ToolOutcome(
            result=QueryResult.combine(*results, total=len(rows), rows=rows),
            scope=scope,
            columns=COLUMNS,
            summary={"datasets": described, "entries_total": sum(d["entries"] for d in described.values())},
            notes=notes,
            rows=rows,
        )


def _rows(found: DatasetFacts, profile: DatasetProfile) -> list[list[Any]]:
    """Components (with Teams), levels and alert categories, each largest first."""
    out: list[list[Any]] = []
    for kind, attribute in KINDS:
        counts: dict[str, int] = getattr(found, attribute)
        for value, n in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            team = profile.teams.get(value, NULL_LABELS["team"]) if kind == "component" else None
            out.append([found.dataset, kind, value, n, team])
    return out


def _summary(found: DatasetFacts, profile: DatasetProfile) -> dict[str, Any]:
    """One Dataset's summary; its first and last Log Entry and corpus end in UTC."""
    teams = {profile.teams[c] for c in found.components if c in profile.teams}
    first = last = None
    if found.first is not None and found.last is not None:
        first, last = utc_range(found.dataset, found.first, found.last)
    return {
        "title": registry_entry(found.dataset).title,
        "entries": found.entries,
        "first_entry": iso(first),
        "last_entry": iso(last),
        "corpus_end": iso(last),
        "components": len(found.components),
        "levels": len(found.levels),
        "alert_categories": len(found.alert_categories),
        "teams": len(teams),
        "patterns": profile.patterns,
        "entries_with_pattern": profile.entries_with_pattern,
    }
