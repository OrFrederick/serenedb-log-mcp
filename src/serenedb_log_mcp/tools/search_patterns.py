"""`search_patterns`: lexical search over the Pattern catalogue, the fallback of list_patterns (#7, T14).

The query's words, without stopwords, plus their synonyms from the curated map
(`corpus/synonyms.yaml`, applied by `corpus.synonyms.expand`) are matched against the catalogue's
inverted index (`relation(...).patterns_index`), whose dictionary lower-cases and stems English
words. One statement scores every matching Pattern with BM25, the synonyms at half weight; the tool
sorts the matches itself (score, then Log Entries, then template_id), because a catalogue holds a
few hundred Patterns and a BM25 sort with a tie-break key crashes SereneDB 26.10.1 (#2 round 2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from serenedb_log_mcp import fts
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, relation
from serenedb_log_mcp.corpus.synonyms import expand
from serenedb_log_mcp.db import QueryFailed, fetch_all
from serenedb_log_mcp.evidence import RoleParam
from serenedb_log_mcp.scope import ScopeError, build_scope
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import DatasetName
from serenedb_log_mcp.tools._catalogue import (
    CHOOSE_NOTE,
    COLUMNS,
    SYNONYM_WEIGHT,
    WHOLE_DATASET_NOTE,
    catalogue_row,
    missing_catalogue,
    search_statement,
)

DEFAULT_TOP = 10
MAX_TOP = 50
SCORE = len(COLUMNS)
"""Index of the score in a result row."""


def register(server: MCPServer) -> None:
    """Register `search_patterns` on `server`."""

    @evidence_tool(server, title="Search the Pattern catalogue by words (BM25, stemming, synonyms)")
    def search_patterns(
        role: RoleParam,
        dataset: Annotated[
            DatasetName,
            Field(description="'bgl' or 'openstack': one catalogue per call."),
        ],
        query: Annotated[
            str,
            Field(
                description="Words for the problem, for example 'memory errors' or 'instance failed to "
                "spawn'. Words are lower-cased and stemmed ('failing' matches 'failed'), stopwords such "
                "as 'the' or 'why' are dropped, and a Pattern matches when its template text holds any "
                "remaining word or one of its synonyms.",
                max_length=fts.MAX_QUERY_CHARS,
            ),
        ],
        top: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_TOP,
                description=f"How many of the best Patterns to return (default {DEFAULT_TOP}, at most "
                f"{MAX_TOP}); patterns_total counts every match.",
            ),
        ] = DEFAULT_TOP,
    ) -> ToolOutcome:
        """Find Patterns of one Dataset by words: BM25 with stemming over template texts, plus synonyms.

        It is the lexical fallback of the catalogue: use list_patterns to choose Patterns for a
        concept, search_patterns when the catalogue is too long to read (BGL's has several pages),
        and top_patterns where available (otherwise count_by with group_by='pattern') to rank
        Patterns by frequency within a Data Scope.
        A match only means the template shares words with the query, so read each template_text,
        keep only the ids that are direct evidence and pass them as pattern_ids to count_by, trend
        and search_logs; if none is, state that as an Unknown.
        Inputs: role, dataset, query (words) and top (default 10, at most 50).
        Outputs: the Evidence envelope, whose rows hold the list_patterns columns (template_id,
        component, level, entries, first_seen, last_seen, family_id, template_text) plus score, best
        first; summary holds patterns_total, words, synonyms (each query word with the synonyms it
        added) and ignored_words; total_matches counts Patterns. No match returns a hint in notes,
        not an error.
        """
        text = fts.normalise_query(query)
        if text is None:
            raise ScopeError(
                "query is empty. Pass the words for the problem, for example 'memory errors', or read "
                f"the whole catalogue with list_patterns(dataset='{dataset}')."
            )
        expansion = expand(text)
        scope = build_scope(
            dataset, known_datasets=DATASET_NAMES, query=text, synonyms=list(expansion.synonyms)
        )
        rel = relation(dataset, role, scope)
        statement = search_statement(rel, expansion.words, expansion.synonyms)
        try:
            result = fetch_all(statement.text, statement.params, role=role)
        except QueryFailed as exc:
            if (missing := missing_catalogue(exc, rel)) is not None:
                raise missing from exc
            raise
        rows = [catalogue_row(row) for row in result.rows]
        for row in rows:
            row[SCORE] = round(float(row[SCORE] or 0.0), 4)
        rows.sort(key=lambda row: (-row[SCORE], -row[3], row[0]))
        shown = rows[:top]
        summary: dict[str, Any] = {
            "patterns_total": len(rows),
            "words": list(expansion.words),
            "synonyms": {word: list(added) for word, added in expansion.applied.items()},
            "ignored_words": list(expansion.ignored),
        }
        return ToolOutcome(
            result=result,
            scope=scope,
            columns=[*COLUMNS, "score"],
            summary=summary,
            notes=_notes(dataset, expansion.applied, bool(rows)),
            rows=shown,
            total_matches=len(rows),
            truncation_note=(
                f"Showing the {len(shown)} best of {len(rows):,} matching Patterns; raise top (at most "
                f"{MAX_TOP}) for more, or read list_patterns(dataset='{dataset}')."
            ),
        )


def _notes(dataset: str, applied: Mapping[str, tuple[str, ...]], matched: bool) -> list[str]:
    method = (
        f"BM25 over the stemmed template texts of {dataset}: Patterns that share more and rarer words "
        f"with the query score higher, synonym matches count {SYNONYM_WEIGHT:g} times as much, and "
        "equal scores are ordered by entries, then template_id."
    )
    if applied:
        method += " Synonyms added: " + "; ".join(f"{w} -> {', '.join(s)}" for w, s in applied.items()) + "."
    if not matched:
        return [
            method,
            f"No Pattern of {dataset} holds these words or their synonyms in its template text. That is "
            f"a hint, not an error: read the catalogue with list_patterns(dataset='{dataset}') and "
            "choose by meaning; if no Pattern is direct evidence, state that as an Unknown.",
        ]
    return [method, CHOOSE_NOTE, WHOLE_DATASET_NOTE]
