"""`find_patterns`: semantic search over the Pattern catalogue with local vectors (T26, #7).

Optional. The template texts of the catalogue are embedded once with fastembed by
`scripts/embed_patterns.py` (`make embed-patterns`) and stored in SereneDB (`pattern_vector`,
FLOAT[384]); this tool embeds only the question, in the server process, from the model cache, with
no API key. One statement (`_catalogue.vector_statement`) returns every embedded Pattern of the
Dataset with its exact cosine distance to the question (`<=>`) and, in mode 'hybrid', its BM25
score from search_patterns' lexical match (stemming plus the curated synonyms). The tool ranks both
sides itself and fuses them by Reciprocal Rank Fusion (k = 60): a catalogue holds a few hundred
Patterns, and a BM25 sort with a tie-break key crashes SereneDB 26.10.1 (#2 round 2).

Without fastembed, its model or the stored vectors, every call is a ToolError that says how to set
it up and points to list_patterns and search_patterns, which need none of it.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from serenedb_log_mcp import fts
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, PATTERN_VECTORS, Relation, relation
from serenedb_log_mcp.corpus.embeddings import INSTALL_HINT, EmbeddingUnavailable, get_embedder
from serenedb_log_mcp.corpus.synonyms import Expansion, expand
from serenedb_log_mcp.db import QueryFailed, fetch_all
from serenedb_log_mcp.evidence import RoleParam
from serenedb_log_mcp.scope import ScopeError, build_scope
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import DatasetName
from serenedb_log_mcp.tools._catalogue import (
    CHOOSE_NOTE,
    COLUMNS,
    NO_MESSAGE,
    WHOLE_DATASET_NOTE,
    catalogue_row,
    missing_catalogue,
    vector_literal,
    vector_statement,
)

DEFAULT_TOP = 10
MAX_TOP = 50
RRF_K = 60
"""The Reciprocal Rank Fusion constant of Cormack et al. (SIGIR 2009), as #7 measured it."""
_MISSING_TABLE = re.compile(r"with name (\w+) does not exist")
Mode = Literal["hybrid", "semantic"]
SEARCHED = len(COLUMNS)
"""Index of the first score column of a statement row: distance, then the BM25 score."""
RESULT_COLUMNS = (*COLUMNS, "score", "similarity", "vector_rank", "lexical_rank")
SETUP_HINT = (
    "Meanwhile, read the catalogue with list_patterns(dataset='{dataset}') and choose by meaning, or "
    "use search_patterns(dataset='{dataset}', query=...) for words; neither needs vectors."
)


def register(server: MCPServer) -> None:
    """Register `find_patterns` on `server`."""

    @evidence_tool(server, title="Find Patterns by meaning (local vectors, optional; fused with BM25)")
    def find_patterns(
        role: RoleParam,
        dataset: Annotated[
            DatasetName,
            Field(description="'bgl' or 'openstack': one catalogue per call."),
        ],
        text: Annotated[
            str,
            Field(
                description="The problem in plain words, for example 'RAM errors' or 'VM could not "
                "boot'. It is embedded as a whole, so a paraphrase finds Patterns that share no word "
                "with it.",
                max_length=fts.MAX_QUERY_CHARS,
            ),
        ],
        top: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_TOP,
                description=f"How many of the best Patterns to return (default {DEFAULT_TOP}, at most "
                f"{MAX_TOP}); patterns_total counts every ranked Pattern.",
            ),
        ] = DEFAULT_TOP,
        mode: Annotated[
            Mode,
            Field(
                description="'hybrid' (default): the vector ranking fused with search_patterns' BM25 "
                "ranking (stemming and synonyms) by Reciprocal Rank Fusion; 'semantic': the vector "
                "ranking alone, score = similarity."
            ),
        ] = "hybrid",
    ) -> ToolOutcome:
        """Find Patterns of one Dataset by meaning: cosine similarity of local text embeddings.

        Optional: it needs the vectors extra (fastembed, no API key) and `make embed-patterns`;
        without them it fails with the setup steps. It differs from the other catalogue tools:
        list_patterns returns the whole catalogue to read and choose from (the first choice, and
        the most accurate), search_patterns matches words (BM25, stemming, synonyms) and finds
        nothing when no word matches, and find_patterns ranks every Pattern by meaning, so a
        paraphrase with no shared word still finds candidates. It always returns its top Patterns,
        related or not: read each template_text and keep only the ids that are direct evidence
        (similarity does not separate related from unrelated), then pass them as pattern_ids to
        count_by, trend and search_logs; if none is, state that as an Unknown.
        Inputs: role, dataset, text (the problem in plain words), top (default 10, at most 50) and
        mode ('hybrid' default, or 'semantic').
        Outputs: the Evidence envelope, whose rows hold the list_patterns columns (template_id,
        component, level, entries, first_seen, last_seen, family_id, template_text) plus score (the
        fused RRF score, or the similarity in mode 'semantic'), similarity (cosine, -1 to 1),
        vector_rank and lexical_rank (null without a word match), best first; summary holds
        patterns_total, lexical_matches, patterns_without_vector, model, mode, words, synonyms and
        ignored_words; total_matches counts the ranked Patterns.
        """
        try:
            normalised = fts.normalise_query(text)
        except ScopeError as exc:
            # fts names its parameter `query`; this tool's is `text`.
            raise ScopeError(str(exc).replace("query", "text", 1)) from exc
        if normalised is None:
            raise ScopeError(
                "text is empty. Pass the problem in plain words, for example 'memory errors', or read "
                f"the whole catalogue with list_patterns(dataset='{dataset}')."
            )
        try:
            embedder = get_embedder()
            vector = vector_literal(embedder.embed_query(normalised))
        except EmbeddingUnavailable as exc:
            raise ToolError(f"{exc} {SETUP_HINT.format(dataset=dataset)}") from exc
        except ValueError as exc:
            raise ToolError(
                f"The embedding model {embedder.model} returned an unusable vector for this text ({exc}). "
                f"Rephrase the text. {SETUP_HINT.format(dataset=dataset)}"
            ) from exc
        expansion = expand(normalised) if mode == "hybrid" else None
        scope = build_scope(
            dataset,
            known_datasets=DATASET_NAMES,
            query=normalised,
            synonyms=list(expansion.synonyms) if expansion else None,
        )
        rel = relation(dataset, role, scope)
        statement = vector_statement(
            rel,
            vector,
            embedder.model,
            embedder.dimensions,
            expansion.words if expansion else (),
            expansion.synonyms if expansion else (),
        )
        try:
            result = fetch_all(statement.text, statement.params, role=role)
        except QueryFailed as exc:
            if exc.sqlstate == "42P01" and _missing_table(exc) == PATTERN_VECTORS:
                raise ToolError(_missing(rel, embedder.model)) from exc
            if (missing := missing_catalogue(exc, rel)) is not None:
                raise missing from exc
            raise
        found = [catalogue_row(row) for row in result.rows]
        if not any(row[SEARCHED] is not None for row in found):
            raise ToolError(_missing(rel, embedder.model))
        unembedded = sum(row[SEARCHED] is None and row[7] != NO_MESSAGE for row in found)
        rows = _ranked(found, mode)
        shown = rows[:top]
        lexical = sum(row[-1] is not None for row in rows)
        summary: dict[str, Any] = {
            "patterns_total": len(rows),
            "lexical_matches": lexical if expansion else None,
            "patterns_without_vector": unembedded,
            "model": embedder.model,
            "mode": mode,
            "words": list(expansion.words) if expansion else [],
            "synonyms": {w: list(added) for w, added in expansion.applied.items()} if expansion else {},
            "ignored_words": list(expansion.ignored) if expansion else [],
        }
        return ToolOutcome(
            result=result,
            scope=scope,
            columns=list(RESULT_COLUMNS),
            summary=summary,
            notes=_notes(dataset, embedder.model, mode, expansion, unembedded),
            rows=shown,
            total_matches=len(rows),
            truncation_note=(
                f"Showing the {len(shown)} best of {len(rows):,} ranked Patterns; raise top (at most "
                f"{MAX_TOP}) for more, or read list_patterns(dataset='{dataset}')."
            ),
        )


def _ranked(rows: list[list[Any]], mode: Mode) -> list[list[Any]]:
    """Rows in RESULT_COLUMNS order, best first: the vector rank, the BM25 rank and their fusion.

    A Pattern without a vector has no vector_rank or similarity; in mode 'hybrid' it is still ranked
    by its word match, and a Pattern with neither is left out. BM25 scores are rounded to four
    decimals before ranking, as search_patterns rounds them, so lexical_rank is its order.
    """
    distance, bm25 = SEARCHED, SEARCHED + 1
    embedded = [row for row in rows if row[distance] is not None]
    by_vector = sorted(embedded, key=lambda row: (float(row[distance]), row[0]))
    vector_rank = {row[0]: rank for rank, row in enumerate(by_vector, start=1)}
    words = {row[0]: round(float(row[bm25]), 4) for row in rows if row[bm25] is not None}
    matched = [row for row in rows if row[0] in words]
    by_words = sorted(matched, key=lambda row: (-words[row[0]], -int(row[3]), row[0]))
    lexical_rank = {row[0]: rank for rank, row in enumerate(by_words, start=1)}
    ranked: list[list[Any]] = []
    for row in rows:
        tid = row[0]
        vector, lexical = vector_rank.get(tid), lexical_rank.get(tid)
        if vector is None and (mode == "semantic" or lexical is None):
            continue
        similarity = None if vector is None else round(1.0 - float(row[distance]), 4)
        if mode == "semantic":
            score = similarity
        else:
            score = round(sum(1.0 / (RRF_K + rank) for rank in (vector, lexical) if rank), 6)
        ranked.append([*row[:SEARCHED], score, similarity, vector, lexical])
    no_rank = len(rows) + 1
    ranked.sort(key=lambda row: (-row[SEARCHED], row[SEARCHED + 2] or no_rank, row[SEARCHED + 3] or no_rank))
    return ranked


def _missing_table(exc: QueryFailed) -> str | None:
    """The table a 42P01 error names ('Table with name pattern_vector does not exist!'), if any."""
    match = _MISSING_TABLE.search(str(exc))
    return match.group(1) if match else None


def _missing(rel: Relation, model: str) -> str:
    return (
        f"No Pattern vectors of {model} for {rel.dataset.name} in schema {rel.schema!r}: run "
        "`make embed-patterns` once after `make derive` (and `make access` again when Roles use "
        f"their own logins). {INSTALL_HINT} {SETUP_HINT.format(dataset=rel.dataset.name)}"
    )


def _notes(
    dataset: str, model: str, mode: Mode, expansion: Expansion | None, unembedded: int = 0
) -> list[str]:
    method = (
        f"Every embedded Pattern of {dataset} is ranked by the cosine similarity of its template text "
        f"to the question ({model}, embedded locally, exact scan in SereneDB)"
    )
    if mode == "hybrid" and expansion is not None:
        method += (
            f", fused by Reciprocal Rank Fusion (k = {RRF_K}) with search_patterns' BM25 ranking "
            "of the same words and synonyms; a Pattern without a word match has lexical_rank null."
        )
        if expansion.applied:
            method += " Synonyms added: " + "; ".join(
                f"{w} -> {', '.join(s)}" for w, s in expansion.applied.items()
            )
            method += "."
    else:
        method += "; score is that similarity."
    if unembedded:
        method += (
            f" {unembedded} Pattern{'s' if unembedded != 1 else ''} of {dataset} "
            f"{'have' if unembedded != 1 else 'has'} no vector (added after the last "
            "`make embed-patterns`): "
            + (
                "left out here; use mode='hybrid' or search_patterns for them."
                if mode == "semantic"
                else "ranked by the word match alone, with similarity and vector_rank null."
            )
        )
    return [
        method,
        "Vector search always returns its nearest Patterns, even when none is about the question, and "
        "similarity does not tell them apart: #7's 'disk full' controls, for which no Pattern is "
        "relevant, scored 0.37 to 0.45, more than the best match of some genuine paraphrases. Treat "
        "the rows as candidates, not matches.",
        CHOOSE_NOTE,
        WHOLE_DATASET_NOTE,
    ]
