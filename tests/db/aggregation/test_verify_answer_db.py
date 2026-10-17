"""`verify_answer` on Evidence the real tools produced, on the synthetic corpus (T12).

The drafts are written from the numbers count_by and search_logs actually returned, so these tests
show that a correct answer over real Evidence passes and that one changed number fails, with the
Evidence Ledger in memory and at a LEDGER_PATH shared by two sessions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aggregation_corpus import STORM
from aggregation_tools import call, row_dicts

from serenedb_log_mcp import config, ledger
from serenedb_log_mcp.config import load_settings

pytestmark = pytest.mark.db


def draft(counts: dict[str, Any], search: dict[str, Any] | None = None) -> str:
    """An answer built from the returned numbers: E1 is the count_by result, E2 the search."""
    top = row_dicts(counts)[0]
    summary = counts["summary"]
    lines = [
        "## Observed Facts",
        f"- {top['component']} wrote {top['count']:,} of the {summary['total']:,} BGL Log Entries, "
        f"{top['share_pct']}% [E1].",
        f"- The other groups hold {summary['other_count']:,} Log Entries [E1].",
    ]
    if search is not None:
        lines.append(f"- A search for TLB error matched {search['total_matches']:,} Log Entries [E2].")
    lines += [
        "",
        "## Hypotheses",
        "- One node may be looping on the same fault.",
        "",
        "## Unknowns",
        "- Cost, downtime and customer impact: these logs do not measure them.",
        "",
        "## Next Step",
        "- Ask the Kernel Team to review the KERNEL Log Entries first.",
    ]
    return "\n".join(lines)


def test_a_correct_answer_over_real_evidence_passes(corpus: str) -> None:
    counts = call("count_by", role="engineer", dataset="bgl", group_by="component", top=2)
    search = call("search_logs", role="engineer", dataset="bgl", query="TLB error")
    assert search["total_matches"] == STORM

    result = call("verify_answer", role="engineer", draft=draft(counts, search))

    assert result["ok"] is True, result["problems"]
    assert result["cited_evidence"] == ["E1", "E2"]
    assert all(n["status"] == "in_evidence" for n in result["checked_numbers"])


def test_one_changed_number_fails_and_names_the_true_value(corpus: str) -> None:
    counts = call("count_by", role="engineer", dataset="bgl", group_by="component", top=2)
    true = row_dicts(counts)[0]["count"]
    text = draft(counts).replace(f"wrote {true:,} of", f"wrote {true + 1:,} of")

    result = call("verify_answer", role="engineer", draft=text)

    assert result["ok"] is False
    assert [p["kind"] for p in result["problems"]] == ["number_not_in_evidence"]
    assert f"{true:,}" in result["problems"][0]["hint"]


def test_the_manager_may_not_cite_the_search(corpus: str) -> None:
    counts = call("count_by", role="manager", dataset="bgl", group_by="component", top=2)
    search = call("search_logs", role="manager", dataset="bgl", query="TLB error")

    result = call("verify_answer", role="manager", draft=draft(counts, search))

    kinds = [p["kind"] for p in result["problems"]]
    assert kinds == ["manager_raw_line_evidence"]
    assert "E2" in result["problems"][0]["hint"]
    assert any("list_incidents" in note for note in result["notes"])  # no Incident tool yet


def test_evidence_from_an_earlier_session_on_the_ledger_path_resolves(
    corpus: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=corpus, ledger_path=path))
    ledger.reset_ledger()
    counts = call("count_by", role="team_lead", dataset="bgl", group_by="component", top=2)

    ledger.reset_ledger()  # a new session: a fresh server process reads the ledger from the file
    result = call("verify_answer", role="team_lead", draft=draft(counts))

    assert result["ok"] is True, result["problems"]
    assert len(path.read_text().splitlines()) == 1  # verifying added nothing
