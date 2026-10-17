"""`search_logs`: find example Log Entries by full-text query or Pattern ids, with an exact total.

Three statements, all on the Dataset's inverted index with one WHERE (`Search`): the exact count,
at most `limit` rows in the chosen order (`db.fetch_limited`, which skips the rows when nothing
matched) and the facets over every match. The SereneDB syntax was checked against the docs and the
26.10.1 dev instance: `websearch_to_tsquery`, `phraseto_tsquery`, a bare string (OR),
`ts_levenshtein`, `ts_highlight(e.message, ...)`, `BM25(e.tableoid)` and `::score(NULL)` filters
(`ts_any`, `ts_ge`, `ts_lt`), in both binding modes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from mcp.server.mcpserver import MCPServer
from psycopg import sql
from pydantic import Field

from serenedb_log_mcp import fts
from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, Relation, relation
from serenedb_log_mcp.corpus.facts import dataset_facts
from serenedb_log_mcp.db import QueryResult, fetch_all, fetch_limited
from serenedb_log_mcp.evidence import DataScope, RoleParam
from serenedb_log_mcp.scope import ScopeError, build_scope, time_bounds, validate_choices
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import (
    ComponentsParam,
    DatasetName,
    FamilyIdParam,
    LevelsParam,
    PatternIdsParam,
    Q,
    SourcesParam,
    TimeFromParam,
    TimeToParam,
    iso,
    joined,
    q,
)
from serenedb_log_mcp.tools._retrieval import (
    ENTRY_COLUMNS,
    HIGHLIGHT_OPTIONS,
    entry_column,
    entry_values,
    shape_message,
)

Sort = Literal["time", "newest", "relevance"]
SORTS: tuple[Sort, ...] = get_args(Sort)

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
TOP_FACETS = 5
"""How many Components and levels the facets list, largest first."""


@dataclass(frozen=True)
class Search:
    """One validated search over one Dataset, and the three statements it runs.

    Every statement reads the inverted index (`relation(...).index`) with the same WHERE, so the
    count, the rows and the facets describe the same Log Entries. The text predicate is the only
    one that scores; every filter is a `::score(NULL)` TSQUERY on a verbatim-indexed column, so a
    filter never changes the BM25 order (a plain SQL filter adds +2.0 to the score, #2).
    """

    rel: Relation
    text: str | None = None
    mode: fts.QueryMode = "all_words"
    start: datetime | None = None
    end: datetime | None = None
    components: tuple[str, ...] = ()
    levels: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    alert_categories: tuple[str, ...] = ()
    pattern_ids: tuple[str, ...] = ()
    family_id: str | None = None
    sort: Sort = "time"

    def col(self, concept: str) -> sql.Composable:
        return entry_column(self.rel, concept)

    @property
    def source(self) -> Q:
        return q("{} AS e", self.rel.index)

    @property
    def columns(self) -> list[str]:
        """The output columns of a row, in order."""
        return [
            *ENTRY_COLUMNS[self.rel.dataset.name],
            "message",
            *(["score"] if self.sort == "relevance" else []),
        ]

    def where(self) -> Q:
        """The predicates every statement shares: the text query first, then the filters."""
        found: list[Q] = []
        if self.text:
            found.append(_q(fts.text_match(self.col("message"), self.text, self.mode)))
        if self.start is not None:
            found.append(_q(fts.filter_at_or_after(self.col("time"), self.start)))
        if self.end is not None:
            found.append(_q(fts.filter_before(self.col("time"), self.end)))
        for concept, values in (
            ("component", self.components),
            ("level", self.levels),
            ("source", self.sources),
            ("alert_category", self.alert_categories),
            ("pattern", self.pattern_ids),
        ):
            if values:
                found.append(_q(fts.filter_any(self.col(concept), values)))
        if self.family_id:
            found.append(_q(fts.filter_any(self.col("family_id"), [self.family_id])))
        if not found:
            raise ValueError("a search needs a text query or a filter")
        return joined(" AND ", found)

    def count_statement(self) -> Q:
        return q("SELECT count(*) FROM {} WHERE {}", self.source, self.where())

    def rows_statement(self) -> Q:
        """The rows in the chosen order; the last placeholder is the LIMIT (`db.fetch_limited`)."""
        selected = [self.col(name) for name in ENTRY_COLUMNS[self.rel.dataset.name]]
        selected.append(self.col("message"))
        if self.text:
            selected.append(
                sql.SQL("ts_highlight({}, {}) AS snippet").format(
                    self.col("message"), sql.Literal(HIGHLIGHT_OPTIONS)
                )
            )
        if self.sort == "relevance":
            selected.append(sql.SQL("BM25(e.tableoid) AS score"))
        return q(
            "SELECT {} FROM {} WHERE {} ORDER BY {} LIMIT %s",
            sql.SQL(", ").join(selected),
            self.source,
            self.where(),
            self._order(),
        )

    def _order(self) -> sql.Composable:
        if self.sort == "relevance":
            # The score is the only key: a tie-break key after it crashes SereneDB 26.10.1 (#2 round 2).
            return sql.SQL("BM25(e.tableoid) DESC")
        direction = sql.SQL(" DESC" if self.sort == "newest" else "")
        return sql.SQL("{}{}, {}{}").format(self.col("epoch"), direction, self.col("entry_id"), direction)

    def facets_statement(self) -> Q:
        """Over every match: distinct sources and racks, first and last seen, Components, levels."""
        rack = self.col("rack") if "rack" in self.rel.columns else sql.SQL("CAST(NULL AS VARCHAR)")
        return q(
            """WITH m AS (
    SELECT {} AS component, {} AS level, {} AS src, {} AS rack, {} AS ts FROM {} WHERE {}
)
SELECT 'all' AS kind, CAST(NULL AS VARCHAR) AS value, count(*) AS n, count(DISTINCT src) AS sources,
       count(DISTINCT rack) AS racks, min(ts) AS first_seen, max(ts) AS last_seen FROM m
UNION ALL
SELECT 'component', component, count(*), NULL, NULL, NULL, NULL FROM m GROUP BY component
UNION ALL
SELECT 'level', level, count(*), NULL, NULL, NULL, NULL FROM m GROUP BY level""",
            self.col("component"),
            self.col("level"),
            self.col("source"),
            rack,
            self.col("time"),
            self.source,
            self.where(),
        )


def _q(built: tuple[sql.Composed, list[object]]) -> Q:
    predicate, params = built
    return Q(predicate, tuple(params))


# The tool ----------------------------------------------------------------------------------------------

ENTRIES_NOTE = (
    "total_matches is the exact number of matching Log Entries, not Incidents: one failing node can "
    "write thousands of lines about one problem. The rows are examples; use count_by or trend for how "
    "often, and get_context(entry_id) for what happened just before and after one of them."
)
HIGHLIGHT_NOTE = (
    "Matched words are marked **like this**; a message is capped at 240 characters, and … marks text "
    "that was left out."
)
RELEVANCE_NOTE = (
    "Sorted by BM25 relevance, highest score first. Log Entries with equal scores come in no fixed "
    "order, so another call can show other entries with the same score; total_matches does not change."
)
SOURCE_NOTES = {
    "bgl": "distinct_sources counts BGL nodes, distinct_racks their racks.",
    "openstack": (
        "distinct_sources counts OpenStack instances (instance_ref); matches that name no instance are "
        "not counted, and OpenStack has no racks."
    ),
}

QueryParam = Annotated[
    str | None,
    Field(
        description="Words to find in the message. Words match whole and case-insensitively, without "
        "stemming ('fail' does not match 'failed'). With mode='all_words' every word must appear in any "
        "order; put a phrase in double quotes, OR between alternatives and -word to exclude a word. "
        "AND is not an operator; all words are already required. A written AND is searched as the "
        "word 'and', which matches almost nothing. "
        "Optional when pattern_ids, family_id or alert_categories is given.",
        max_length=fts.MAX_QUERY_CHARS,
    ),
]
ModeParam = Annotated[
    fts.QueryMode,
    Field(
        description="How query matches: all_words (default, web-search syntax), phrase (the words next "
        "to each other, in order), any_words (at least one of the words) or fuzzy (every word, each "
        "allowing one typo; words of up to 3 characters must match exactly; at most 8 words)."
    ),
]
SortParam = Annotated[
    Sort,
    Field(
        description="time (default, oldest first), newest (newest first) or relevance (BM25 score, "
        "highest first; needs query)."
    ),
]
LimitParam = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_LIMIT,
        description=f"How many Log Entries to return (default {DEFAULT_LIMIT}, at most {MAX_LIMIT}); "
        "total_matches is exact either way.",
    ),
]
AlertCategoriesParam = Annotated[
    list[str] | None,
    Field(
        description="BGL only: only these alert categories, for example ['KERNMNTF']. It is a Pattern "
        "filter, so query may then be omitted.",
        max_length=50,
    ),
]


def register(server: MCPServer) -> None:
    """Register `search_logs` on `server`."""

    @evidence_tool(server, title="Search Log Entries by text or Pattern")
    def search_logs(
        role: RoleParam,
        dataset: Annotated[
            DatasetName,
            Field(description="'bgl' or 'openstack': one Dataset per call; call twice to search both."),
        ],
        query: QueryParam = None,
        mode: ModeParam = "all_words",
        pattern_ids: PatternIdsParam = None,
        family_id: FamilyIdParam = None,
        time_from: TimeFromParam = None,
        time_to: TimeToParam = None,
        components: ComponentsParam = None,
        levels: LevelsParam = None,
        sources: SourcesParam = None,
        alert_categories: AlertCategoriesParam = None,
        sort: SortParam = "time",
        limit: LimitParam = DEFAULT_LIMIT,
    ) -> ToolOutcome:
        """Find example Log Entries in one Dataset by full-text query or by Pattern ids, with filters.

        It does not answer "how often": total_matches is the exact number of matching Log Entries but
        the rows are only a sample, so use count_by or trend for frequency and change over time, and
        get_context(entry_id) for what happened just before and after one entry.
        Inputs: role, dataset, and query (mode all_words, phrase, any_words or fuzzy) or a Pattern
        filter (pattern_ids, family_id, alert_categories), plus optional time_from, time_to,
        components, levels, sources, sort (time, newest, relevance) and limit (default 20, at most 50).
        Outputs: the Evidence envelope, whose rows hold entry_id, ts, the node (BGL) or request_id and
        instance_ref (OpenStack), component, level, alert_tag (BGL), template_id, message (at most 240
        characters, matched words marked **like this**) and, with sort='relevance', score; summary
        holds entries_total and facets over every match: distinct_sources, distinct_racks,
        first_seen, last_seen, top_components and top_levels.
        No match returns a hint in notes, not an error.
        """
        search, scope = prepare(
            role,
            dataset,
            query=query,
            mode=mode,
            pattern_ids=pattern_ids,
            family_id=family_id,
            time_from=time_from,
            time_to=time_to,
            components=components,
            levels=levels,
            sources=sources,
            alert_categories=alert_categories,
            sort=sort,
        )
        return run(search, scope, limit, role)


def prepare(
    role: Role,
    dataset: str,
    *,
    query: str | None,
    mode: fts.QueryMode,
    pattern_ids: Sequence[str] | None,
    family_id: str | None,
    time_from: str | None,
    time_to: str | None,
    components: Sequence[str] | None,
    levels: Sequence[str] | None,
    sources: Sequence[str] | None,
    alert_categories: Sequence[str] | None,
    sort: Sort,
) -> tuple[Search, DataScope]:
    """Validate the inputs against the Dataset's facts; return the search and its Data Scope."""
    text = fts.normalise_query(query)
    if alert_categories and dataset != "bgl":
        raise ScopeError(
            f"alert_categories exist only in BGL; {dataset} Log Entries have none. Pass dataset='bgl', "
            "or filter OpenStack by query, pattern_ids, components or levels."
        )
    if not text and not (pattern_ids or (family_id and family_id.strip()) or alert_categories):
        raise ScopeError(
            "Pass query (words in the message) or a Pattern filter (pattern_ids, family_id or, for BGL, "
            "alert_categories): search_logs finds example Log Entries. To count Log Entries by "
            "Component, level or time, use count_by or trend instead."
        )
    if sort == "relevance" and not text:
        raise ScopeError(
            "sort='relevance' ranks by how well the message matches query, so it needs query. Use "
            "sort='time' or sort='newest' for a Pattern filter alone."
        )
    found = dataset_facts(dataset, role)
    if found.last is None:
        raise ScopeError(
            f"{dataset} has no Log Entries in this database. Call describe_corpus for the Datasets "
            "that are loaded."
        )
    components_ = validate_choices("components", components, found.components)
    levels_ = validate_choices("levels", levels, found.levels)
    alerts_ = validate_choices("alert_categories", alert_categories, found.alert_categories)
    sources_ = list(dict.fromkeys(s.strip() for s in sources or () if s.strip())) or None
    scope = build_scope(
        dataset,
        time_from=time_from,
        time_to=time_to,
        corpus_end={dataset: found.last},
        known_datasets=DATASET_NAMES,
        pattern_ids=pattern_ids,
        family_id=family_id,
        query=text,
        mode=mode if text else None,
        components=components_,
        levels=levels_,
        sources=sources_,
        alert_categories=alerts_,
    )
    start, end = time_bounds(scope)
    search = Search(
        relation(dataset, role, scope),
        text=text,
        mode=mode,
        start=start,
        end=end,
        components=tuple(components_ or ()),
        levels=tuple(levels_ or ()),
        sources=tuple(sources_ or ()),
        alert_categories=tuple(alerts_ or ()),
        pattern_ids=tuple(scope.filters.pattern_ids or ()),
        family_id=scope.filters.family_id,
        sort=sort,
    )
    search.where()  # a fuzzy query with too many words fails here, before any statement runs
    return search, scope


def run(search: Search, scope: DataScope, limit: int, role: Role) -> ToolOutcome:
    """Count, fetch the rows and, when anything matched, the facets."""
    count, rows = search.count_statement(), search.rows_statement()
    found = fetch_limited(count.text, rows.text, count.params, limit, role=role)
    results = [found]
    summary: dict[str, Any] = {"entries_total": found.total}
    notes = [ENTRIES_NOTE]
    if found.total:
        facets = search.facets_statement()
        facet_result = fetch_all(facets.text, facets.params, role=role)
        results.append(facet_result)
        summary.update(read_facets(facet_result, found.total, has_racks="rack" in search.rel.columns))
        notes.append(SOURCE_NOTES[search.rel.dataset.name])
        if search.text:
            notes.append(HIGHLIGHT_NOTE)
        if search.sort == "relevance":
            notes.append(RELEVANCE_NOTE)
    else:
        notes.append(no_match_hint(search))
    shaped = [shape_row(search, row) for row in found.rows]
    return ToolOutcome(
        result=QueryResult.combine(*results, total=found.total, rows=shaped),
        scope=scope,
        columns=search.columns,
        summary=summary,
        notes=notes,
        rows=shaped,
    )


def shape_row(search: Search, row: Sequence[Any]) -> list[Any]:
    """A row as the agent reads it: ISO times, the shaped message and a rounded score."""
    width = len(ENTRY_COLUMNS[search.rel.dataset.name])
    values = entry_values(row[:width])
    message = row[width]
    rest = list(row[width + 1 :])
    snippet = rest.pop(0) if search.text else None
    shaped = [*values, shape_message(message, snippet)]
    if search.sort == "relevance":
        shaped.append(None if rest[0] is None else round(float(rest[0]), 4))
    return shaped


def read_facets(result: QueryResult, total: int, *, has_racks: bool) -> dict[str, Any]:
    """The facets summary from `Search.facets_statement` rows; distinct_racks is None without racks."""
    out: dict[str, Any] = {}
    groups: dict[str, list[tuple[str, int]]] = {"component": [], "level": []}
    for kind, value, n, sources, racks, first, last in result.rows:
        if kind == "all":
            if int(n) != total:
                raise RuntimeError(
                    f"the facets counted {n} Log Entries and the count {total}; "
                    "the tables changed between them"
                )
            out.update(
                distinct_sources=int(sources),
                distinct_racks=int(racks) if has_racks else None,
                first_seen=iso(first),
                last_seen=iso(last),
            )
        elif kind in groups:
            groups[kind].append(("(none)" if value is None else str(value), int(n)))
    for kind, name in (("component", "components"), ("level", "levels")):
        ranked = sorted(groups[kind], key=lambda item: (-item[1], item[0]))
        out[f"top_{name}"] = dict(ranked[:TOP_FACETS])
        out[f"{name}_total"] = len(ranked)
    return out


def no_match_hint(search: Search) -> str:
    """What to try when nothing matched: a hint, not an error."""
    hints = []
    if search.text:
        hints.append(
            "words match whole and without stemming, so try fewer or other words, mode='any_words' (any "
            "one word) or mode='fuzzy' (one typo per word)"
        )
    if search.pattern_ids or search.family_id or search.alert_categories:
        hints.append(
            "check the Pattern filter: count_by(group_by='pattern') lists the Pattern ids in a Data Scope"
        )
    hints.append(
        "widen the time range or drop a filter (describe_corpus lists the time range and the values)"
    )
    return f"No Log Entries matched in {search.rel.dataset.name}. To find some: {'; '.join(hints)}."
