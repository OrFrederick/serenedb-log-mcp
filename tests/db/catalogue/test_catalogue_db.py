"""`list_patterns`, `search_patterns` and the catalogue-first chain against a real SereneDB (T14),
on the Loghub 2k samples derived into a throwaway schema. Every number is checked against
independent SQL on the same tables."""

from __future__ import annotations

import re
import uuid
from typing import Any

import psycopg
import pytest
from aggregation_tools import call, call_error, row_dicts
from catalogue_chain import catalogue_pages, chain_totals, incidents_entries_total, whole_catalogue
from psycopg import sql

from serenedb_log_mcp import config
from serenedb_log_mcp.config import get_settings, load_settings
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.tools import _catalogue
from serenedb_log_mcp.tools._catalogue import COLUMNS, SYNONYM_WEIGHT

DATASETS = ("bgl", "openstack")


def independent(schema: str, query: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    """Run `query` with {pattern}, {bgl} and {os} bound to the tables in `schema`."""
    settings = get_settings().model_copy(update={"slm_schema": schema})
    bgl, os_ = relation("bgl", settings=settings), relation("openstack", settings=settings)
    statement = sql.SQL(query).format(pattern=bgl.patterns_table, bgl=bgl.table, os=os_.table)
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(statement, params).fetchall()]


def entry_table(dataset: str) -> str:
    return "{bgl}" if dataset == "bgl" else "{os}"


# list_patterns ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", DATASETS)
def test_the_catalogue_lists_every_pattern_of_the_dataset_once(sample: str, dataset: str) -> None:
    pages = catalogue_pages(dataset)

    expected = dict(
        independent(sample, "SELECT template_id, entries FROM {pattern} WHERE dataset = %s", [dataset])
    )
    (entries,) = independent(sample, f"SELECT count(*) FROM {entry_table(dataset)}")[0]
    rows = [row for page in pages for row in row_dicts(page)]
    assert len(rows) == len({r["template_id"] for r in rows})
    assert {r["template_id"]: r["entries"] for r in rows} == expected
    for number, result in enumerate(pages, start=1):
        assert result["columns"] == list(COLUMNS)
        assert result["total_matches"] == result["summary"]["patterns_total"] == len(rows)
        assert result["summary"]["entries_total"] == entries
        assert (result["summary"]["page"], result["summary"]["pages_total"]) == (number, len(pages))
        assert result["truncated"] is (len(pages) > 1)
        assert result["data_scope"]["datasets"] == [dataset]


def test_rows_are_grouped_by_component_and_largest_first(sample: str) -> None:
    rows = whole_catalogue("bgl")

    keys = [(r["component"] is None, r["component"] or "", -r["entries"], r["template_id"]) for r in rows]
    assert keys == sorted(keys)
    components = list(dict.fromkeys(r["component"] for r in rows))
    assert list(call("list_patterns", role="engineer", dataset="bgl")["summary"]["components"]) == components


def test_component_and_level_are_the_most_frequent_ones_and_times_are_cut_to_the_second(sample: str) -> None:
    rows = whole_catalogue("openstack")

    top = max(rows, key=lambda r: r["entries"])
    (component, level, first, last) = independent(
        sample, "SELECT component, level, first_seen, last_seen FROM {pattern} WHERE template_id = %s",
        [top["template_id"]],
    )[0]  # fmt: skip
    assert (top["component"], top["level"]) == (component, level)
    assert top["first_seen"] == first.replace(microsecond=0).isoformat()
    assert top["last_seen"] == last.replace(microsecond=0).isoformat()


@pytest.mark.parametrize("dataset", DATASETS)
def test_family_id_is_shown_only_for_families_of_more_than_one_pattern(sample: str, dataset: str) -> None:
    rows = whole_catalogue(dataset)

    shared = {
        family
        for (family,) in independent(
            sample,
            "SELECT family_id FROM {pattern} WHERE dataset = %s GROUP BY family_id HAVING count(*) > 1",
            [dataset],
        )
    }
    families = dict(
        independent(sample, "SELECT template_id, family_id FROM {pattern} WHERE dataset = %s", [dataset])
    )
    for r in rows:
        expected = families[r["template_id"]] if families[r["template_id"]] in shared else None
        assert r["family_id"] == expected, r


def test_a_component_filter_lists_that_components_patterns_in_any_case(sample: str) -> None:
    pages = catalogue_pages("bgl", component="kernel")

    expected = independent(
        sample, "SELECT count(*) FROM {pattern} WHERE dataset = 'bgl' AND component = 'KERNEL'"
    )[0][0]
    rows = [row for page in pages for row in row_dicts(page)]
    assert {r["component"] for r in rows} == {"KERNEL"}
    assert len(rows) == expected > 0
    assert pages[0]["data_scope"]["filters"]["component"] == "KERNEL"
    assert list(pages[0]["summary"]["components"]) == ["KERNEL"]


def test_a_misspelt_component_gets_a_did_you_mean(sample: str) -> None:
    assert "Did you mean 'KERNEL'" in call_error(
        "list_patterns", role="engineer", dataset="bgl", component="KERNAL"
    )


def test_pattern_ids_look_up_given_patterns(sample: str) -> None:
    ids = [r["template_id"] for r in whole_catalogue("openstack")][:2]

    result = call("list_patterns", role="engineer", dataset="openstack", pattern_ids=[ids[1], ids[0].upper()])

    assert sorted(r["template_id"] for r in row_dicts(result)) == sorted(ids)
    assert result["data_scope"]["filters"]["pattern_ids"] == [ids[1], ids[0]]


def test_unknown_pattern_ids_return_a_hint_not_an_error(sample: str) -> None:
    result = call("list_patterns", role="engineer", dataset="bgl", pattern_ids=["0" * 16])

    assert (result["total_matches"], result["rows"]) == (0, [])
    assert any("No Pattern of bgl matched" in note for note in result["notes"])


def test_a_large_catalogue_is_paged_by_component(sample: str, monkeypatch: pytest.MonkeyPatch) -> None:
    whole = whole_catalogue("bgl")
    monkeypatch.setattr(_catalogue, "ROW_BUDGET_CHARS", 1_500)

    first = call("list_patterns", role="engineer", dataset="bgl")
    pages = first["summary"]["pages_total"]
    seen: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        result = call("list_patterns", role="engineer", dataset="bgl", page=page)
        assert result["summary"]["page"] == page
        assert result["total_matches"] == len(whole)
        assert result["truncated"] is True
        assert f"Page {page} of {pages}" in result["notes"][0]
        seen += row_dicts(result)
    assert pages > 1
    assert seen == whole
    for component, entry in first["summary"]["components"].items():
        assert entry["patterns"] == sum(1 for r in whole if (r["component"] or "(no component)") == component)
    assert "page=2" in first["notes"][0]


def test_a_page_past_the_last_is_an_error_that_names_the_last(sample: str) -> None:
    text = call_error("list_patterns", role="engineer", dataset="openstack", page=3)

    assert "page=3 is past the last page" in text
    assert "1 page" in text


def test_a_schema_without_the_catalogue_gets_an_actionable_error(
    sample: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = f"scratch_t14_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(empty)))
    try:
        monkeypatch.setattr(config, "_active", load_settings(slm_schema=empty))
        for tool, arguments in (("list_patterns", {}), ("search_patterns", {"query": "error"})):
            text = call_error(tool, role="engineer", dataset="bgl", **arguments)
            assert "make derive" in text, text
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(empty)))


# search_patterns -------------------------------------------------------------------------------------


def test_search_finds_patterns_by_a_stemmed_word(sample: str) -> None:
    result = call("search_patterns", role="engineer", dataset="openstack", query="spawning")

    rows = row_dicts(result)
    assert rows, "the OpenStack sample has spawn Patterns"
    assert all("spawn" in r["template_text"].lower() for r in rows)
    expected = independent(
        sample,
        "SELECT count(*) FROM {pattern} "
        "WHERE dataset = 'openstack' AND lower(template_text) LIKE '%%spawn%%'",
    )[0][0]
    assert result["summary"]["patterns_total"] == expected
    assert result["columns"] == [*COLUMNS, "score"]


def test_search_ranks_by_score_then_entries_then_id(sample: str) -> None:
    rows = row_dicts(call("search_patterns", role="engineer", dataset="bgl", query="error", top=50))

    keys = [(-r["score"], -r["entries"], r["template_id"]) for r in rows]
    assert keys == sorted(keys)
    assert all(r["score"] > 0 for r in rows)


def test_synonyms_find_patterns_the_query_words_miss(sample: str) -> None:
    result = call("search_patterns", role="engineer", dataset="openstack", query="boot")

    rows = row_dicts(result)
    assert result["summary"]["words"] == ["boot"]
    assert "spawn" in result["summary"]["synonyms"]["boot"]
    assert rows
    assert not any("boot" in r["template_text"].lower() for r in rows)
    assert any("Synonyms added: boot -> " in note for note in result["notes"])
    assert result["data_scope"]["filters"]["synonyms"] == result["summary"]["synonyms"]["boot"]


def test_a_synonym_match_scores_half_of_the_same_word_asked_for(sample: str) -> None:
    by_synonym = row_dicts(
        call("search_patterns", role="engineer", dataset="openstack", query="boot", top=50)
    )
    asked = {
        r["template_id"]: r["score"]
        for r in row_dicts(
            call("search_patterns", role="engineer", dataset="openstack", query="started", top=50)
        )
    }

    others = ("spawn", "build", "creat", "image")
    only_started = [
        r
        for r in by_synonym
        if "started" in re.findall(r"\w+", r["template_text"].lower())
        and not any(word.startswith(others) for word in re.findall(r"\w+", r["template_text"].lower()))
    ]
    assert only_started, "the OpenStack sample has 'VM Started (Lifecycle Event)'"
    for r in only_started:
        assert r["score"] == pytest.approx(asked[r["template_id"]] * SYNONYM_WEIGHT, abs=2e-4), r


def test_stopwords_are_dropped_and_reported(sample: str) -> None:
    result = call("search_patterns", role="engineer", dataset="openstack", query="why did the instance spawn")

    assert result["summary"]["words"] == ["instance", "spawn"]
    assert result["summary"]["ignored_words"] == ["why", "did", "the"]


def test_top_limits_the_rows_and_the_total_counts_every_match(sample: str) -> None:
    result = call("search_patterns", role="engineer", dataset="bgl", query="error", top=2)

    assert result["returned"] == 2
    assert result["total_matches"] == result["summary"]["patterns_total"] > 2
    assert result["truncated"] is True
    assert any("Showing the 2 best of" in note for note in result["notes"])


def test_no_match_is_a_hint_not_an_error(sample: str) -> None:
    result = call("search_patterns", role="engineer", dataset="bgl", query="xyzzyplugh")

    assert (result["total_matches"], result["rows"]) == (0, [])
    assert any("That is a hint, not an error" in note and "list_patterns" in note for note in result["notes"])


def test_a_query_without_words_is_an_error(sample: str) -> None:
    assert "no words to search for" in call_error(
        "search_patterns", role="engineer", dataset="bgl", query="?!"
    )


# The catalogue-first chain ---------------------------------------------------------------------------


def non_alert_bgl_pattern(sample: str) -> str:
    """The non-alert BGL Pattern with the most Log Entries, as list_patterns shows it."""
    (template_id,) = independent(
        sample,
        "SELECT template_id FROM {bgl} WHERE template_id IS NOT NULL GROUP BY template_id "
        "HAVING count(alert_tag) = 0 AND count(*) > 1 ORDER BY count(*) DESC, template_id LIMIT 1",
    )[0]
    catalogue = {r["template_id"] for r in whole_catalogue("bgl", role="team_lead")}
    assert template_id in catalogue
    return str(template_id)


def largest_openstack_pattern() -> str:
    rows = whole_catalogue("openstack", role="team_lead")
    return str(max(rows, key=lambda r: (r["entries"], r["template_id"]))["template_id"])


@pytest.fixture
def chain_patterns(sample: str) -> list[tuple[str, str]]:
    return [("bgl", non_alert_bgl_pattern(sample)), ("openstack", largest_openstack_pattern())]


def test_the_chain_totals_agree_from_the_catalogue_to_the_log_entries(
    sample: str, chain_patterns: list[tuple[str, str]]
) -> None:
    for dataset, template_id in chain_patterns:
        totals = chain_totals(dataset, template_id)

        (expected,) = independent(
            sample, f"SELECT count(*) FROM {entry_table(dataset)} WHERE template_id = %s", [template_id]
        )[0]
        assert totals.catalogue == totals.count_by == totals.search_logs == expected > 1, dataset


def failing_openstack_pattern(sample: str) -> str | None:
    """The largest OpenStack Pattern whose Log Entries are all ERROR or CRITICAL, or None.

    OpenStack Incidents chain only Request Failures (T27), so a Pattern of ordinary Log Entries has
    none; one whose every Log Entry is a failure must reach list_incidents with all of them.
    """
    rows = independent(
        sample,
        f"SELECT template_id, count(*) FROM {entry_table('openstack')} GROUP BY 1 "
        "HAVING count(*) FILTER (WHERE level NOT IN ('ERROR', 'CRITICAL')) = 0 ORDER BY 2 DESC, 1 LIMIT 1",
        [],
    )
    return str(rows[0][0]) if rows else None


def test_the_chain_reaches_list_incidents(sample: str, chain_patterns: list[tuple[str, str]]) -> None:
    failing = failing_openstack_pattern(sample)
    patterns = [(dataset, template_id) for dataset, template_id in chain_patterns if dataset == "bgl"]
    if failing is not None:
        patterns.append(("openstack", failing))
    for dataset, template_id in patterns:
        totals = chain_totals(dataset, template_id)

        assert incidents_entries_total(dataset, template_id) == totals.count_by, dataset
