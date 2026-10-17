"""scripts/ground_truth.py: the only reader of the ground-truth labels (plan T10, T11)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import ground_truth
from ground_truth import Ranked, parse_labels, replace_section, score

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
