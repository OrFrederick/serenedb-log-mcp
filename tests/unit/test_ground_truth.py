"""scripts/ground_truth.py: the only reader of the ground-truth labels (plan T10, T11)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import ground_truth
from ground_truth import MetricResult, Ranked, parse_labels, render_openstack, replace_section, score

ROOT = Path(__file__).resolve().parents[2]
LABEL_FILE_TEXT = """The following VM instances have injected anomalies as observed in openstack_abnormal.log.

544fd51c-4edc-4780-baae-ba1d80a0acfc
ae651dff-c7ad-43d6-ac96-bbcd820ccca8
a445709b-6ad0-40ec-8860-bec60b6ca0c2
1643649d-2f42-4303-bfcd-7798baec19f9"""


def test_labels_are_the_uuids_of_the_label_file_in_order() -> None:
    assert parse_labels(LABEL_FILE_TEXT) == [
        "544fd51c-4edc-4780-baae-ba1d80a0acfc",
        "ae651dff-c7ad-43d6-ac96-bbcd820ccca8",
        "a445709b-6ad0-40ec-8860-bec60b6ca0c2",
        "1643649d-2f42-4303-bfcd-7798baec19f9",
    ]


def test_a_label_file_without_uuids_is_an_error() -> None:
    with pytest.raises(ground_truth.GroundTruthError, match="no instance ids"):
        parse_labels("nothing here")


def test_score_counts_flagged_labelled_and_ranks() -> None:
    # The flag is the tool's own decision (z >= min_z on the unrounded z), not recomputed here.
    ranked = [Ranked("a", True), Ranked("x", True), Ranked("b", True), Ranked("y", False), Ranked("c", False)]

    result = score(ranked, labels={"a", "b", "c"}, min_z=5.0)

    assert result.flagged == 3
    assert result.true_positives == 2
    assert result.recall == (2, 3)
    assert result.precision == (2, 3)
    assert result.label_ranks == {"a": 1, "b": 3, "c": 5}
    assert result.unlabelled_flagged == ["x"]


def test_a_label_outside_the_ranked_rows_has_no_rank() -> None:
    result = score([Ranked("a", True)], labels={"a", "z"}, min_z=5.0)

    assert result.label_ranks == {"a": 1, "z": None}
    assert result.recall == (1, 2)


def test_replace_section_swaps_only_its_own_block() -> None:
    document = (
        "# Ground truth\n\n<!-- BEGIN bgl -->\nBGL\n<!-- END bgl -->\n\n"
        "<!-- BEGIN openstack -->\nold\n<!-- END openstack -->\n"
    )

    updated = replace_section(document, "openstack", "new\nlines")

    assert "<!-- BEGIN openstack -->\nnew\nlines\n<!-- END openstack -->" in updated
    assert "<!-- BEGIN bgl -->\nBGL\n<!-- END bgl -->" in updated
    assert "old" not in updated


def test_replace_section_appends_a_missing_block() -> None:
    updated = replace_section("# Ground truth\n\nintro\n", "openstack", "body")

    assert updated.startswith("# Ground truth\n\nintro\n")
    assert updated.rstrip().endswith("<!-- BEGIN openstack -->\nbody\n<!-- END openstack -->")


def test_only_ground_truth_py_reads_the_label_file() -> None:
    readers = []
    for path in [
        *(ROOT / "src").rglob("*.py"),
        *(ROOT / "scripts").glob("*.py"),
        *(ROOT / "sql").rglob("*.sql"),
    ]:
        if re.search(r"anomaly_labels", path.read_text()):
            readers.append(path.relative_to(ROOT).as_posix())
    # fetch_data.py downloads the file into SLM_DATA_DIR and checks its size; it never opens it.
    assert sorted(readers) == ["scripts/fetch_data.py", "scripts/ground_truth.py"]


# ----------------------------------------------------------------- the OpenStack report

LABELS = ["a", "b"]


def metric_result(metric: str, flags: dict[float, list[str]]) -> MetricResult:
    """A MetricResult whose ranking is a, b, x, y and whose flags per min_z are given."""
    order = ["a", "b", "x", "y"]
    scores = [
        score([Ranked(i, i in flagged) for i in order], set(LABELS), min_z)
        for min_z, flagged in flags.items()
    ]
    rows: list[dict[str, Any]] = [
        {
            "instance_id": i,
            "source_id": "os-1",
            "spawn_s": 30.0 - n,
            "spawn_z": 20.0 - n,
            "image_s": 20.0 - n,
            "image_z": 10.0 - n,
            "spawn_ts": "2017-05-14T21:00:00",
            "flagged": i in flags[ground_truth.OPENSTACK_DEFAULT_MIN_Z],
        }
        for n, i in enumerate(order)
    ]
    summary = {"reference_instances": 4, "median_s": 19.7, "mad_s": 0.4}
    return MetricResult(metric=metric, rows=rows, summary=summary, query_ms=1.0, scores=scores)


def render(flags: dict[float, list[str]]) -> str:
    return render_openstack(
        labels=LABELS,
        results=[metric_result("spawn", flags), metric_result("build", flags)],
        entries=[("a", 28, 0), ("b", 28, 0)],
        derive_s=0.1,
        schema="slm",
        version="SereneDB 26.10.1",
        generated=datetime(2026, 10, 18, tzinfo=UTC),
    )


def test_the_report_says_the_default_cut_was_chosen_with_the_labels_in_view() -> None:
    flags = {3.5: ["a", "b", "x", "y"], 5.0: ["a", "b", "x"], 8.0: ["a", "b"]}

    text = render(flags)

    assert "research that had already seen the labels" in text
    assert (
        "Recall is 2 of 2 at all 3 thresholds reported (3.5, 5, 8), so it does not depend on the cut" in text
    )
    assert "precision does: 2/4 at 3.5, 2/3 at 5, 2/2 at 8" in text


def test_the_report_says_when_recall_depends_on_the_cut() -> None:
    flags = {3.5: ["a", "b"], 5.0: ["a"], 8.0: ["a"]}

    text = render(flags)

    assert "research that had already seen the labels" in text
    assert "Recall depends on the cut: 2 of 2 at 3.5, 1 of 2 at 5, 1 of 2 at 8" in text


def test_main_dispatches_on_the_dataset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called: list[Path] = []
    monkeypatch.setitem(ground_truth.RUNNERS, "openstack", lambda output: called.append(output) or "body")

    assert ground_truth.main(["openstack", "--output", str(tmp_path / "gt.md")]) == 0
    assert called == [tmp_path / "gt.md"]


def test_the_cli_offers_exactly_the_datasets_with_a_runner() -> None:
    with pytest.raises(SystemExit):
        ground_truth.main(["nosuchdataset"])
    assert set(ground_truth.RUNNERS) == {"openstack"}
