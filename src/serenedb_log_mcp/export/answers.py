"""The answers of an investigation: what `export_investigation` takes and the answers file holds.

An answers file is YAML (JSON is valid YAML too) with the report title, the answers in the order the
investigation was handed on, and optionally the Evidence ids the report is restricted to:

```yaml
title: Start-up path failures
evidence_ids: [E1, E2, E3]        # optional; default: every record in the ledger
answers:
  - role: engineer
    question: What failed on the /p/gb1 mount path?
    answer: |
      ## Observed Facts
      ...
```

`scripts/export_report.py` and `scripts/replay_ledger.py` read it with `load_investigation`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from serenedb_log_mcp.evidence import EVIDENCE_ID_PATTERN, Role

__all__ = ["Answer", "AnswersFileError", "Investigation", "load_investigation"]


class Answer(BaseModel):
    """One question a Role asked and the final answer it got."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role = Field(description="The Role that asked: engineer, team_lead or manager.")
    question: str = Field(min_length=1, description="The question as the Role asked it.")
    answer: str = Field(
        min_length=1,
        description="The final answer in Markdown, exactly as it was sent: the four sections Observed "
        "Facts, Hypotheses, Unknowns and Next Step, with [E<n>] next to every number.",
    )


EvidenceIdList = list[Annotated[str, Field(pattern=EVIDENCE_ID_PATTERN)]]


class Investigation(BaseModel):
    """The contents of an answers file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    answers: list[Answer] = Field(min_length=1)
    evidence_ids: EvidenceIdList | None = None


class AnswersFileError(ValueError):
    """The answers file cannot be read or does not hold an investigation."""


def load_investigation(path: Path) -> Investigation:
    """Read an answers file; raise `AnswersFileError` with a message that names the problem."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AnswersFileError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise AnswersFileError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise AnswersFileError(f"{path} must be a mapping with title and answers, not {type(data).__name__}")
    try:
        return Investigation.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors()[:3]
        )
        raise AnswersFileError(f"{path} is not an answers file: {problems}") from exc
