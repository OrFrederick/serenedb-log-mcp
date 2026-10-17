"""The ground-truth leak check (T15): where a tool result says "abnormal".

The OpenStack ground truth is the file name `openstack_abnormal.log`; only scripts/ground_truth.py
may read labels, so no tool result may contain the word.

One exemption, the same as T4's canonical-table check (docs/overnight-decisions.md, 00:55 UTC): the
BGL MASABNORM Log Entries' own text, "mmcs_server exited abnormally due to signal: ...", is a Log
Entry, not a label. In Evidence it is exempt only inside message-text cells (`message`,
`template_text`); in a result that is not Evidence only the Log Entry's phrase "exited abnormally due
to signal" is exempt, so a rendered report can quote it. SQL, parameters, arguments, notes, summary,
Data Scope and error texts get no exemption.
"""

from __future__ import annotations

import json
import re

from contract_session import Outcome

from serenedb_log_mcp.evidence import Evidence

LEAK = re.compile("abnormal", re.IGNORECASE)
MESSAGE_TEXT_COLUMNS = frozenset({"message", "template_text"})
CELL_EXEMPTION = re.compile("exited abnormally", re.IGNORECASE)
TEXT_EXEMPTION = re.compile("exited abnormally due to signal", re.IGNORECASE)
EVIDENCE_FIELDS = frozenset(Evidence.model_fields)


def leaks(outcome: Outcome) -> list[str]:
    """The places in one result that say "abnormal", after the exemption."""
    if outcome.error is not None:
        return [f"error text: {outcome.error[:200]}"] if LEAK.search(outcome.error) else []
    data = outcome.data or {}
    if set(data) != EVIDENCE_FIELDS:
        text = TEXT_EXEMPTION.sub("", json.dumps(data, ensure_ascii=False))
        return [f"result: ...{text[max(0, m.start() - 80) : m.end() + 80]}..." for m in LEAK.finditer(text)]
    found = []
    for row in data["rows"]:
        for column, cell in zip(data["columns"], row, strict=True):
            value = json.dumps(cell, ensure_ascii=False)
            if column in MESSAGE_TEXT_COLUMNS:
                value = CELL_EXEMPTION.sub("", value)
            if LEAK.search(value):
                found.append(f"rows[{column}]: {value[:200]}")
    for field in sorted(EVIDENCE_FIELDS - {"rows"}):
        value = json.dumps(data[field], ensure_ascii=False)
        if LEAK.search(value):
            found.append(f"{field}: {value[:200]}")
    return found
