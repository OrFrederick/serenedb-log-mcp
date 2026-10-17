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

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from serenedb_log_mcp import fts
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, Relation, relation
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
    WHOLE_DATASET_NOTE,
    catalogue_row,
    vector_literal,
    vector_statement,
)

DEFAULT_TOP = 10
MAX_TOP = 50
RRF_K = 60
"""The Reciprocal Rank Fusion constant of Cormack et al. (SIGIR 2009), as #7 measured it."""
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
        patterns_total, lexical_matches, model, mode, words, synonyms and
        ignored_words; total_matches counts the ranked Patterns.
        """
        normalised = fts.normalise_query(text)
        if normalised is None:
            raise ScopeError(
                "text is empty. Pass the problem in plain words, for example 'memory errors', or read "
                f"the whole catalogue with list_patterns(dataset='{dataset}')."
            )
        try:
            embedder = get_embedder()
            vector = embedder.embed_query(normalised)
        except EmbeddingUnavailable as exc:
            raise ToolError(f"{exc} {SETUP_HINT.format(dataset=dataset)}") from exc
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
            vector_literal(vector),
            embedder.model,
            embedder.dimensions,
            expansion.words if expansion else (),
            expansion.synonyms if expansion else (),
        )
        try:
            result = fetch_all(statement.text, statement.params, role=role)
        except QueryFailed as exc:
            if exc.sqlstate == "42P01":  # undefined_table
                raise ToolError(_missing(rel, embedder.model)) from exc
            raise
        if not result.rows:
            raise ToolError(_missing(rel, embedder.model))
        rows = _ranked([catalogue_row(row) for row in result.rows], mode)
        shown = rows[:top]
        lexical = sum(row[-1] is not None for row in rows)
        summary: dict[str, Any] = {
            "patterns_total": len(rows),
            "lexical_matches": lexical if expansion else None,
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
            notes=_notes(dataset, embedder.model, mode, expansion),
            rows=shown,
            total_matches=len(rows),
            truncation_note=(
                f"Showing the {len(shown)} best of {len(rows):,} ranked Patterns; raise top (at most "
                f"{MAX_TOP}) for more, or read list_patterns(dataset='{dataset}')."
            ),
        )


def _ranked(rows: list[list[Any]], mode: Mode) -> list[list[Any]]:
    """Rows in RESULT_COLUMNS order, best first: the vector rank, the BM25 rank and their fusion."""
    distance, bm25 = SEARCHED, SEARCHED + 1
    by_vector = sorted(rows, key=lambda row: (float(row[distance]), row[0]))
    vector_rank = {row[0]: rank for rank, row in enumerate(by_vector, start=1)}
    matched = [row for row in rows if row[bm25] is not None]
    by_words = sorted(matched, key=lambda row: (-float(row[bm25]), -int(row[3]), row[0]))
    lexical_rank = {row[0]: rank for rank, row in enumerate(by_words, start=1)}
    ranked: list[list[Any]] = []
    for row in rows:
        tid = row[0]
        similarity = round(1.0 - float(row[distance]), 4)
        lexical = lexical_rank.get(tid)
        if mode == "semantic":
            score = similarity
        else:
            fused = 1.0 / (RRF_K + vector_rank[tid]) + (1.0 / (RRF_K + lexical) if lexical else 0.0)
            score = round(fused, 6)
        ranked.append([*row[:SEARCHED], score, similarity, vector_rank[tid], lexical])
    ranked.sort(key=lambda row: (-row[SEARCHED], row[SEARCHED + 2]))
    return ranked


def _missing(rel: Relation, model: str) -> str:
    return (
        f"No Pattern vectors of {model} for {rel.dataset.name} in schema {rel.schema!r}: run "
        "`make embed-patterns` once after `make derive` (and `make access` again when Roles use "
        f"their own logins). {INSTALL_HINT} {SETUP_HINT.format(dataset=rel.dataset.name)}"
    )


def _notes(dataset: str, model: str, mode: Mode, expansion: Expansion | None) -> list[str]:
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
    return [
        method,
        "Vector search always returns its nearest Patterns, even when none is about the question, and "
        "similarity does not tell them apart: #7's 'disk full' controls, for which no Pattern is "
        "relevant, scored 0.37 to 0.45, more than the best match of some genuine paraphrases. Treat "
        "the rows as candidates, not matches.",
        CHOOSE_NOTE,
        WHOLE_DATASET_NOTE,
    ]
