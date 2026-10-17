"""The demo's beats (T24): the scripted tool calls of `docs/demo/beats.yaml` and how a ledger maps to them.

`docs/demo/script.md` is the run sheet a presenter reads; `docs/demo/beats.yaml` holds the same beats in
a form the scripts can run:

- `scripts/demo_record.py` makes every call of every beat against SereneDB, in order, with a fresh
  Evidence Ledger. That warms the caches, times each call and records the ledger the replay reads.
- `scripts/demo_replay.py` reads a recorded ledger back offline, beat by beat, without SereneDB.

```yaml
title: Core demo v1
budget_s: 300
beats:
  - title: Lines versus Incidents
    role: team_lead                # the Role of every call, unless a call names its own
    seconds: 75                    # the time this beat gets in the run sheet
    prompt: As Team Lead, which BGL alert category is loudest, and is it many problems or one?
    expected: KERNDTLB tops the categories; one node holds nearly all of it on one day.
    calls:
      - tool: count_by
        arguments: {group_by: alert_category, dataset: bgl, top: 5}
      - tool: get_context
        role: engineer
        arguments: {dataset: bgl, entry_id: $entry_id}   # the first entry_id of the previous call
```

The one placeholder, `$entry_id`, stands for the `entry_id` of the first row the previous call of the
same beat returned, so a beat can search and then look around the first hit without pinning an id that
differs between the Loghub sample and the Base Corpus.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from serenedb_log_mcp.config import Role

__all__ = [
    "ENTRY_ID",
    "Beat",
    "BeatMatch",
    "BeatsFileError",
    "Call",
    "DemoBeats",
    "load_beats",
    "match_beats",
    "resolve_arguments",
]

ENTRY_ID = "$entry_id"
"""Placeholder for the `entry_id` of the first row of the previous call in the same beat."""


class Call(BaseModel):
    """One tool call of a beat, as the agent is expected to make it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1, description="The tool name, for example 'count_by'.")
    role: Role | None = Field(default=None, description="The Role of the call; default: the beat's Role.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="The arguments without role; '$entry_id' is resolved at run time."
    )


class Beat(BaseModel):
    """One beat of the run sheet: a prompt, the calls it should cause and the Evidence to expect."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    role: Role | None = Field(
        default=None, description="The Role of the calls; may be left out without calls."
    )
    seconds: int = Field(gt=0, description="The time this beat gets in the run sheet.")
    prompt: str = Field(min_length=1, description="The prompt as the presenter types it.")
    expected: str = Field(min_length=1, description="The Evidence to expect, in one or two sentences.")
    calls: list[Call] = Field(default_factory=list)

    @model_validator(mode="after")
    def _every_call_has_a_role(self) -> Beat:
        if any(call.role is None for call in self.calls) and self.role is None:
            raise ValueError(f"beat {self.title!r} has calls without a Role: set the beat's role")
        return self

    def role_of(self, call: Call) -> Role:
        role = call.role or self.role
        assert role is not None  # the validator guarantees it
        return role


class DemoBeats(BaseModel):
    """The contents of a beats file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    budget_s: int = Field(gt=0, description="The time the whole demo may take, in seconds.")
    beats: list[Beat] = Field(min_length=1)

    @property
    def planned_s(self) -> int:
        return sum(beat.seconds for beat in self.beats)


class BeatsFileError(ValueError):
    """The beats file cannot be read or does not hold the demo's beats."""


def load_beats(path: Path) -> DemoBeats:
    """Read a beats file; raise `BeatsFileError` with a message that names the problem."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BeatsFileError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise BeatsFileError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BeatsFileError(f"{path} must be a mapping with title, budget_s and beats")
    try:
        return DemoBeats.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors()[:3]
        )
        raise BeatsFileError(f"{path} is not a beats file: {problems}") from exc


def resolve_arguments(call: Call, previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """The call's arguments with `$entry_id` replaced from `previous`, the last result of the beat.

    Raises `ValueError` when the placeholder is used and the previous result has no first row with an
    `entry_id` column.
    """
    arguments = dict(call.arguments)
    for name, value in arguments.items():
        if value != ENTRY_ID:
            continue
        entry_id = _first_entry_id(previous)
        if entry_id is None:
            raise ValueError(
                f"{call.tool}: {name}={ENTRY_ID} needs a previous call in the same beat whose first row "
                "has an entry_id, and there is none"
            )
        arguments[name] = entry_id
    return arguments


def _first_entry_id(result: Mapping[str, Any] | None) -> Any:
    if not result:
        return None
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if "entry_id" not in columns or not rows:
        return None
    return rows[0][columns.index("entry_id")]


@dataclass
class BeatMatch:
    """The Evidence records of a ledger that one beat's calls produced."""

    beat: Beat
    records: list[dict[str, Any]] = field(default_factory=list)
    missing: list[Call] = field(default_factory=list)
    loose: set[str] = field(default_factory=set)
    """Evidence ids of records that match a call by tool and Role only, not by its arguments."""


def match_beats(
    beats: DemoBeats, records: Sequence[Mapping[str, Any]]
) -> tuple[list[BeatMatch], list[dict[str, Any]]]:
    """Assign ledger records to the beats' calls, in order; return the matches and the records left over.

    A record matches a call when tool and Role are the same and every argument the call names has the
    same value in the record (a recorded call also carries the defaults it ran with). `$entry_id`
    matches any value. Each record is used once, the earliest unused one first, so a ledger recorded
    by `demo_record.py` maps back exactly.

    A live session rarely makes a call exactly as scripted (it picks its own `top` or `limit`), so a
    second pass gives each call still missing the earliest unused record with the same tool and Role,
    and lists its Evidence id in `loose`. Records left after both passes are returned as left over.
    """
    unused = [dict(record) for record in records]
    matches: list[BeatMatch] = []
    for beat in beats.beats:
        match = BeatMatch(beat)
        for call in beat.calls:
            found = next((r for r in unused if _matches(beat.role_of(call), call, r)), None)
            if found is None:
                match.missing.append(call)
                continue
            unused.remove(found)
            match.records.append(found)
        matches.append(match)
    for match in matches:
        for call in list(match.missing):
            role = match.beat.role_of(call).value
            found = next((r for r in unused if r.get("tool") == call.tool and r.get("role") == role), None)
            if found is None:
                continue
            unused.remove(found)
            match.missing.remove(call)
            match.records.append(found)
            match.loose.add(str(found.get("evidence_id")))
        match.records.sort(key=_ledger_order)
    return matches, unused


def _ledger_order(record: Mapping[str, Any]) -> int:
    evidence_id = str(record.get("evidence_id") or "")
    return int(evidence_id[1:]) if evidence_id[1:].isdigit() else 0


def _matches(role: Role, call: Call, record: Mapping[str, Any]) -> bool:
    if record.get("tool") != call.tool or record.get("role") != role.value:
        return False
    recorded = _plain(record.get("arguments") or {})
    for name, value in call.arguments.items():
        if value == ENTRY_ID:
            if name not in recorded:
                return False
            continue
        if name not in recorded or recorded[name] != _plain(value):
            return False
    return True


def _plain(value: Any) -> Any:
    """`value` as the ledger stores it (JSON), so YAML and recorded arguments compare equal."""
    return json.loads(json.dumps(value, default=str))
