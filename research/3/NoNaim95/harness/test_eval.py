"""Grade recorded LLM runs with pytest (offline, deterministic): RUNS_DIR=runs_pilot pytest test_eval.py
Gate checks fail the test; style checks are reported as xfail-style warnings only."""
import glob
import json
import os
import sys
import warnings
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import assertions as A  # noqa: E402
from grade import golden_values  # noqa: E402

GATE = {"tools.only_mcp", "tools.required", "tools.any_of", "tools.forbidden", "tools.args", "ledger.one_entry_per_call",
        "numbers.golden_in_answer", "numbers.grounded_or_derived", "structure.sections", "citations.resolve",
        "citations.numbers_in_cited_block", "manager.no_invented_impact", "unknowns.flagged", "answer.forbid_regex",
        "glossary.no_entry_count_as_incidents"}
QS = {q["id"]: q for q in yaml.safe_load(open(HERE / "questions.yaml"))["questions"]}
RUNS = sorted(p for p in glob.glob(os.path.join(os.environ.get("RUNS_DIR", str(HERE / "runs_pilot")), "*.json"))
              if not p.endswith(".mcp.json"))


@pytest.mark.parametrize("path", RUNS, ids=[Path(p).stem for p in RUNS])
def test_run(path):
    run = json.load(open(path))
    q = QS[run["id"]]
    res = A.grade(run, q, golden_values(q))
    for c in res:
        if c.passed is False and c.name not in GATE:
            warnings.warn(f"style {c.name}: {c.detail}")
    bad = {c.name: c.detail for c in res if c.passed is False and c.name in GATE}
    assert not bad, bad
