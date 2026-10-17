"""Golden tests for the Pattern catalogue tools and the catalogue-first chain over the full Base
Corpus in SLM_SCHEMA (default slm), with the Patterns of T9 (465 BGL and 73 OpenStack Patterns).

Every total is compared with independent SQL on the same canonical tables. The chain runs on one
non-alert BGL Pattern and one OpenStack Pattern; its list_incidents step skips until T27 registers
that tool.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
from aggregation_tools import call, row_dicts
from catalogue_chain import catalogue_pages, chain_totals, incidents_entries_total, whole_catalogue
from psycopg import sql

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import get_settings, load_settings
from serenedb_log_mcp.corpus import facts
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.corpus.synonyms import load_synonyms
from serenedb_log_mcp.tools._catalogue import NO_MESSAGE

pytestmark = pytest.mark.corpus

BGL_ROWS = 4_747_963
OPENSTACK_ROWS = 207_820
BGL_PATTERNS = 465
OPENSTACK_PATTERNS = 73
PAGE_CHARS = 17_000
"""Most characters of JSON one list_patterns result may have: about 8.5k tokens at the measured 2
characters per token, below Claude Code's 10k-token warning."""

BGL_CHAIN_TEXT = "instruction cache parity error corrected"
OPENSTACK_CHAIN_TEXT = "[instance: <*>] Took <*> seconds to spawn the instance on the hypervisor."
EDRAM_TEXT = "<*> L3 <*> error(s) (dcr <*>) detected and corrected"


@pytest.fixture(autouse=True)
def corpus_settings(slm_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=slm_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()


def independent(schema: str, query: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    """Run `query` with {pattern}, {pattern_idx}, {bgl} and {os} bound to the tables in `schema`."""
    settings = get_settings().model_copy(update={"slm_schema": schema})
    bgl, os_ = relation("bgl", settings=settings), relation("openstack", settings=settings)
    statement = sql.SQL(query).format(
        pattern=bgl.patterns_table, pattern_idx=bgl.patterns_index, bgl=bgl.table, os=os_.table
    )
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(statement, params).fetchall()]


def within_timeout(result: dict[str, Any]) -> None:
    assert result["query_ms"] < get_settings().query_timeout_s * 1000


def by_text(rows: list[dict[str, Any]], text: str) -> dict[str, Any]:
    (found,) = [row for row in rows if row["template_text"] == text]
    return found


# list_patterns ---------------------------------------------------------------------------------------


def test_the_bgl_catalogue_is_paged_by_component_and_covers_every_pattern(slm_schema: str) -> None:
    pages = catalogue_pages("bgl")

    rows = [row for page in pages for row in row_dicts(page)]
    expected = dict(
        independent(slm_schema, "SELECT template_id, entries FROM {pattern} WHERE dataset = 'bgl'")
    )
    assert len(rows) == BGL_PATTERNS == len(expected)
    assert {r["template_id"]: r["entries"] for r in rows} == expected
    assert sum(r["entries"] for r in rows) == BGL_ROWS == pages[0]["summary"]["entries_total"]
    assert len(pages) > 1
    for page in pages:
        assert len(json.dumps(page, ensure_ascii=False)) <= PAGE_CHARS
        within_timeout(page)
    index = pages[0]["summary"]["components"]
    assert len(index["KERNEL"]["pages"]) > 1, "315 KERNEL Patterns do not fit one page"
    for name, entry in index.items():
        if len(entry["pages"]) > 1:  # a Component split over pages has those pages to itself
            for number in entry["pages"]:
                assert pages[number - 1]["summary"]["page_components"] == [name]


def test_the_openstack_catalogue_is_one_page(slm_schema: str) -> None:
    result = call("list_patterns", role="engineer", dataset="openstack")

    assert (result["summary"]["pages_total"], result["truncated"]) == (1, False)
    assert result["total_matches"] == result["returned"] == OPENSTACK_PATTERNS
    assert result["summary"]["entries_total"] == OPENSTACK_ROWS
    assert len(json.dumps(result, ensure_ascii=False)) <= PAGE_CHARS


def test_the_pattern_of_empty_messages_is_labelled(slm_schema: str) -> None:
    (template_id, entries) = independent(
        slm_schema,
        "SELECT template_id, entries FROM {pattern} WHERE dataset = 'bgl' AND template_source = 'empty'",
    )[0]

    result = call("list_patterns", role="engineer", dataset="bgl", pattern_ids=[template_id])

    assert row_dicts(result) == [dict(row_dicts(result)[0], template_text=NO_MESSAGE, entries=entries)]
    assert entries == 34_470
    assert any(NO_MESSAGE in note for note in result["notes"])


def test_family_ids_mark_the_eight_bgl_drift_pairs(slm_schema: str) -> None:
    bgl = whole_catalogue("bgl")
    openstack = whole_catalogue("openstack")

    families = [r["family_id"] for r in bgl if r["family_id"] is not None]
    assert len(families) == 16
    assert len(set(families)) == 8
    assert all(r["family_id"] is None for r in openstack)


def test_a_component_page_holds_exactly_that_components_patterns(slm_schema: str) -> None:
    rows = whole_catalogue("openstack", component="nova.compute.manager")

    expected = independent(
        slm_schema,
        "SELECT count(*) FROM {pattern} WHERE dataset = 'openstack' AND component = 'nova.compute.manager'",
    )[0][0]
    assert len(rows) == expected > 0
    assert {r["component"] for r in rows} == {"nova.compute.manager"}


# search_patterns -------------------------------------------------------------------------------------


def test_search_finds_the_lustre_mount_failures(slm_schema: str) -> None:
    result = call("search_patterns", role="engineer", dataset="bgl", query="lustre mount failed")

    top = [r["template_text"] for r in row_dicts(result)[:3]]
    assert sum("Lustre mount FAILED" in text for text in top) == 2, top
    within_timeout(result)


def test_synonyms_find_storage_patterns_that_never_say_storage(slm_schema: str) -> None:
    result = call("search_patterns", role="engineer", dataset="bgl", query="storage problems")

    assert {"lustre", "nfs", "mount"} <= set(result["summary"]["synonyms"]["storage"])
    top = [r["template_text"].lower() for r in row_dicts(result)[:5]]
    assert any("lustre" in text for text in top), top
    assert any("nfs" in text for text in top), top


def test_synonyms_find_the_openstack_token_failures(slm_schema: str) -> None:
    result = call("search_patterns", role="engineer", dataset="openstack", query="authentication")

    texts = {r["template_text"] for r in row_dicts(result)}
    assert texts == {
        "Bad response code while validating token: <*>",
        "Unable to validate token: Failed to fetch token data from identity server",
        "Identity response: <*>",
    }


def test_no_match_is_a_hint_not_an_error(slm_schema: str) -> None:
    result = call("search_patterns", role="manager", dataset="openstack", query="xyzzyplugh")

    assert (result["total_matches"], result["rows"]) == (0, [])
    assert any("That is a hint, not an error" in note for note in result["notes"])


def test_every_synonym_occurs_in_a_template_text(slm_schema: str) -> None:
    # The terms the catalogue's index holds, and each synonym as its dictionary stems it
    # (sql/derive/20_patterns.sql names the dictionary pattern_text).
    terms = {
        term
        for (term,) in independent(
            slm_schema, "SELECT unnest(ts_dict_agg(p.template_text)) FROM {pattern_idx} AS p"
        )
    }
    values = sorted({value for values in load_synonyms().values() for value in values})
    # Literals, not bound parameters: SereneDB 26.10.1 answers this statement with bound ones with a
    # malformed row ("unexpected field count in D message").
    lexize = sql.SQL("SELECT v, ts_lexize({}, v) FROM unnest({}::VARCHAR[]) AS t(v)").format(
        sql.Literal(f"{slm_schema}.pattern_text"), sql.Literal(values)
    )
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        stems = conn.execute(lexize).fetchall()

    assert len(stems) == len(values)
    missing = [value for value, stemmed in stems if not set(stemmed) & terms]
    assert missing == [], "synonyms.yaml lists words no Pattern template uses"


# The catalogue-first chain ---------------------------------------------------------------------------


def chain_patterns(slm_schema: str) -> list[tuple[str, str]]:
    """One non-alert BGL Pattern and one OpenStack Pattern, picked from the catalogue by their text."""
    bgl = by_text(whole_catalogue("bgl", role="team_lead", component="KERNEL"), BGL_CHAIN_TEXT)["template_id"]
    alerts = independent(slm_schema, "SELECT count(alert_tag) FROM {bgl} WHERE template_id = %s", [bgl])[0][0]
    assert alerts == 0, "the BGL chain Pattern must be a non-alert Pattern"
    openstack = by_text(whole_catalogue("openstack", role="team_lead"), OPENSTACK_CHAIN_TEXT)["template_id"]
    return [("bgl", bgl), ("openstack", openstack)]


def test_search_patterns_offers_the_chain_patterns(slm_schema: str) -> None:
    for (dataset, template_id), query in zip(
        chain_patterns(slm_schema), ("instruction cache parity", "spawn instance hypervisor"), strict=True
    ):
        rows = row_dicts(call("search_patterns", role="team_lead", dataset=dataset, query=query, top=3))
        assert template_id in [r["template_id"] for r in rows], dataset


def test_the_chain_totals_agree_from_the_catalogue_to_the_log_entries(slm_schema: str) -> None:
    for dataset, template_id in chain_patterns(slm_schema):
        totals = chain_totals(dataset, template_id)

        table = "{bgl}" if dataset == "bgl" else "{os}"
        expected = independent(
            slm_schema, f"SELECT count(*) FROM {table} WHERE template_id = %s", [template_id]
        )
        assert totals.catalogue == totals.count_by == totals.search_logs == expected[0][0] > 1000, dataset


def test_the_chain_reaches_list_incidents(slm_schema: str) -> None:
    for dataset, template_id in chain_patterns(slm_schema):
        totals = chain_totals(dataset, template_id)

        assert incidents_entries_total(dataset, template_id) == totals.count_by, dataset


# count_by and trend on the template-keyed data -------------------------------------------------------


def test_count_by_pattern_matches_the_catalogue(slm_schema: str) -> None:
    result = call("count_by", role="team_lead", group_by="pattern", dataset="bgl", top=100)

    entries = dict(
        independent(slm_schema, "SELECT template_id, entries FROM {pattern} WHERE dataset = 'bgl'")
    )
    rows = [r for r in row_dicts(result) if r["pattern"] != "(other)"]
    assert len(rows) == 100
    assert all(r["count"] == entries[r["pattern"]] for r in rows)
    assert result["summary"]["groups_total"] == BGL_PATTERNS
    assert result["summary"]["total"] == BGL_ROWS
    within_timeout(result)


def test_trend_by_pattern_follows_a_pattern_family_across_its_wording_change(slm_schema: str) -> None:
    bgl = whole_catalogue("bgl", role="team_lead", component="KERNEL")
    family_id = by_text(bgl, EDRAM_TEXT)["family_id"]
    members = {r["template_id"]: r["entries"] for r in bgl if r["family_id"] == family_id}

    result = call(
        "trend", role="team_lead", dataset="bgl", bucket="week", group_by="pattern", family_id=family_id
    )

    series = {s["series"]: s for s in result["summary"]["series"]}
    assert len(members) == 2
    assert {name: s["count"] for name, s in series.items()} == members
    assert all("handoff" in s for s in series.values())
    assert result["summary"]["total"] == sum(members.values())
    within_timeout(result)
