"""find_patterns (T26) with the real embedding model over the full Base Corpus in SLM_SCHEMA.

These tests need the optional vectors extra, its cached model and the Pattern vectors of that model
in SLM_SCHEMA (`make embed-patterns`), so they carry the `embedding_model` marker and skip without
them. They pin what docs/results/find-patterns.md reports: where the vectors find Patterns that
search_patterns' words miss, and that the synonym cases of tests/corpus/test_catalogue_corpus.py
still come out on top.
"""

from __future__ import annotations

import psycopg
import pytest
from aggregation_tools import call, row_dicts
from psycopg import sql

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import get_settings, load_settings
from serenedb_log_mcp.corpus import facts
from serenedb_log_mcp.corpus.embeddings import MODEL

pytestmark = [pytest.mark.corpus, pytest.mark.embedding_model]


@pytest.fixture(autouse=True)
def corpus_settings(slm_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=slm_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [slm_schema, "pattern_vector"],
        ).fetchone()
        count = 0
        if exists and exists[0]:
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE model = %s").format(
                    sql.Identifier(slm_schema, "pattern_vector")
                ),
                [MODEL],
            ).fetchone()
            count = row[0] if row else 0
    if not count:
        pytest.skip(f"no Pattern vectors of {MODEL} in {slm_schema}: run `make embed-patterns`")


def texts(tool: str, dataset: str, words: str, top: int = 5) -> list[str]:
    key = "text" if tool == "find_patterns" else "query"
    result = call(tool, role="engineer", dataset=dataset, top=top, **{key: words})
    return [str(r["template_text"]).lower() for r in row_dicts(result)]


def test_a_paraphrase_finds_the_temperature_pattern_that_words_miss() -> None:
    found = texts("find_patterns", "bgl", "nodes overheating", top=3)
    by_words = texts("search_patterns", "bgl", "nodes overheating")

    assert any("temperature" in text for text in found), found
    assert not any("temperature" in text for text in by_words), by_words


def test_power_supply_failure_finds_more_power_patterns_than_words() -> None:
    found = texts("find_patterns", "bgl", "power supply failure")
    by_words = texts("search_patterns", "bgl", "power supply failure")

    assert sum("power" in text for text in found) >= 3, found
    assert sum("power" in text for text in found) > sum("power" in text for text in by_words)


def test_ram_errors_finds_memory_patterns() -> None:
    found = texts("find_patterns", "bgl", "RAM errors")

    assert all("memory" in text or "edram" in text or "ddr" in text for text in found), found


def test_the_synonym_cases_stay_on_top() -> None:
    storage = texts("find_patterns", "bgl", "storage problems")
    assert any("lustre" in text for text in storage), storage
    assert any("mount" in text for text in storage), storage

    authentication = texts("find_patterns", "openstack", "authentication", top=3)
    assert set(authentication) == {
        "bad response code while validating token: <*>",
        "unable to validate token: failed to fetch token data from identity server",
        "identity response: <*>",
    }


def test_disk_full_still_returns_candidates_with_a_warning() -> None:
    # No BGL Pattern is about a full disk (#7 control): the vectors still return neighbours.
    result = call("find_patterns", role="engineer", dataset="bgl", text="disk full", top=5)

    assert result["returned"] == 5
    assert any("treat the rows as candidates, not matches" in note.lower() for note in result["notes"])
