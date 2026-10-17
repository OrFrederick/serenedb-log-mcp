"""The eval question set (T17): `eval/questions.yaml`, loaded and validated.

Each question names its Role, its split (tune or holdout), optional slash-command arguments for the
Role prompt, an optional cross-Role triple, and what a good trial does:

- L1 (trajectory): `must_call`, `any_of` (groups, one tool of each group), `must_not_call`, `order`
  (an ordered subsequence, so extra exploratory calls are fine), `args` (predicates over the
  arguments of at least one call of a tool) and `budget` (calls, turns, cost).
- L2 (grounding): `golden` (scalar SQL run live; its value must appear in the answer), `mention`
  (regexes the answer must match), `forbid` (regexes it must not match) and `unknowns` (regexes the
  Unknowns section must match). `verify.check_answer` always runs on top, for every question.

Tool names are short (`count_by`); the grader matches `mcp__serenedb-log__<name>`. A tool that the
server does not offer yet may appear in an `any_of` group next to one it does, never alone in
`must_call`, so the question set does not fail on a missing tool.

Golden SQL uses `{bgl}` and `{os}` for the canonical tables in SLM_SCHEMA, so a re-load with the
organisers' loader updates the expected values automatically (#3).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from serenedb_log_mcp.config import Role

DEFAULT_PATH = Path(__file__).resolve().parent / "questions.yaml"

Op = Literal["equals", "icontains", "contains", "in", "present", "absent"]
"""equals (case-insensitive), icontains (substring, case-insensitive), contains (a list argument
holds the value), in (the argument is one of a list), present (set and not empty), absent (not set)."""

Split = Literal["tune", "holdout"]
Group = Literal["engineer", "team_lead", "manager", "adversarial"]

_TOOL_RE = re.compile(r"^(?:\*|[a-z][a-z0-9_]*)$")
_GOLDEN_TABLES = re.compile(r"\{(?:bgl|os)\}")
_PROMPT_ARGUMENTS: dict[Role, tuple[str, ...]] = {
    Role.ENGINEER: ("dataset", "focus"),
    Role.TEAM_LEAD: ("dataset", "period"),
    Role.MANAGER: ("dataset", "period"),
}
"""The Role prompts' arguments in declaration order (roles/prompts.py); Claude Code fills them
positionally from the whitespace-separated words after the slash command."""


class QuestionError(ValueError):
    """The question file is not a valid question set."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArgPredicate(_Strict):
    """At least one call of `tool` ('*' for any tool) must satisfy `arg op value`."""

    tool: str
    arg: str
    op: Op
    value: Any = None

    @field_validator("tool")
    @classmethod
    def _tool_name(cls, value: str) -> str:
        if not _TOOL_RE.fullmatch(value):
            raise ValueError(f"{value!r} is not a short tool name or '*'")
        return value

    @model_validator(mode="after")
    def _value_fits(self) -> ArgPredicate:
        if self.op in ("present", "absent"):
            if self.value is not None:
                raise ValueError(f"op {self.op!r} takes no value")
        elif self.value is None:
            raise ValueError(f"op {self.op!r} needs a value")
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError("op 'in' needs a list value")
        return self

    def describe(self) -> str:
        shown = "" if self.value is None else f" {self.value!r}"
        return f"{self.tool}.{self.arg} {self.op}{shown}"


class Budget(_Strict):
    """Limits for one trial; a trial over any of them fails `l1.budget`."""

    max_calls: int = Field(default=15, ge=1)
    max_turns: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, gt=0)


class Golden(_Strict):
    """A scalar golden value: `sql` returns one number, which must appear in the answer."""

    name: str
    sql: str

    @field_validator("sql")
    @classmethod
    def _uses_tables(cls, value: str) -> str:
        if not _GOLDEN_TABLES.search(value):
            raise ValueError("golden SQL must read {bgl} or {os}")
        return value


class Expect(_Strict):
    must_call: list[str] = Field(default_factory=list)
    any_of: list[list[str]] = Field(default_factory=list)
    must_not_call: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    args: list[ArgPredicate] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    golden: list[Golden] = Field(default_factory=list)
    mention: list[str] = Field(default_factory=list)
    forbid: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    @field_validator("mention", "forbid", "unknowns")
    @classmethod
    def _regexes(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"{value!r} is not a regex: {exc}") from exc
        return values

    @model_validator(mode="after")
    def _tools(self) -> Expect:
        names = [*self.must_call, *self.must_not_call, *self.order, *(t for g in self.any_of for t in g)]
        for name in names:
            if not _TOOL_RE.fullmatch(name) or name == "*":
                raise ValueError(f"{name!r} is not a short tool name")
        both = set(self.must_call) & set(self.must_not_call)
        if both:
            raise ValueError(f"tools both required and forbidden: {sorted(both)}")
        if any(not group for group in self.any_of):
            raise ValueError("an any_of group is empty")
        return self


class Question(_Strict):
    id: str = Field(pattern=r"^(?:eng|tl|mgr|adv)-[0-9]{2}$")
    role: Role
    split: Split
    question: str = Field(min_length=10)
    prompt_args: dict[str, str] = Field(default_factory=dict)
    triple: str | None = None
    expect: Expect = Field(default_factory=Expect)

    @property
    def group(self) -> Group:
        """The report group: the Role, or 'adversarial' for adv-* questions."""
        if self.id.startswith("adv-"):
            return "adversarial"
        return self.role.value

    @model_validator(mode="after")
    def _consistent(self) -> Question:
        prefix = {Role.ENGINEER: "eng", Role.TEAM_LEAD: "tl", Role.MANAGER: "mgr"}[self.role]
        if not (self.id.startswith(prefix + "-") or self.id.startswith("adv-")):
            raise ValueError(
                f"{self.id}: an id starting {self.id[:3]!r} cannot have role {self.role.value!r}"
            )
        allowed = _PROMPT_ARGUMENTS[self.role]
        unknown = set(self.prompt_args) - set(allowed)
        if unknown:
            raise ValueError(
                f"{self.id}: prompt_args {sorted(unknown)} are not arguments of the {self.role} prompt"
            )
        if any(not v or any(c.isspace() for c in v) for v in self.prompt_args.values()):
            raise ValueError(f"{self.id}: every prompt argument is one non-empty token")
        if self.id.startswith("adv-") and self.triple:
            raise ValueError(f"{self.id}: adversarial questions are not part of a cross-Role triple")
        return self

    def slash_command(self, server: str) -> str:
        """The Role prompt as a Claude Code slash command, with positional arguments.

        An argument that is set after an unset one needs a placeholder, which the prompts do not
        accept, so a later argument requires every earlier one (checked by `load`).
        """
        words = [f"/mcp__{server}__{self.role.value}"]
        for name in _PROMPT_ARGUMENTS[self.role]:
            if name not in self.prompt_args:
                break
            words.append(self.prompt_args[name])
        return " ".join(words)


class QuestionSet(_Strict):
    version: int
    questions: list[Question]

    def by_id(self) -> dict[str, Question]:
        return {q.id: q for q in self.questions}

    def select(self, ids: Sequence[str] = (), split: Split | None = None) -> list[Question]:
        """The questions with these ids (all when empty) in file order, optionally of one split."""
        known = self.by_id()
        missing = [i for i in ids if i not in known]
        if missing:
            raise QuestionError(f"unknown question ids: {', '.join(missing)}")
        chosen = [q for q in self.questions if not ids or q.id in ids]
        return [q for q in chosen if split is None or q.split == split]

    def triples(self) -> dict[str, dict[Role, Question]]:
        """Cross-Role triples: the same topic asked by each Role."""
        out: dict[str, dict[Role, Question]] = {}
        for q in self.questions:
            if q.triple:
                out.setdefault(q.triple, {})[q.role] = q
        return out


def load(path: Path = DEFAULT_PATH) -> QuestionSet:
    """Load and validate a question file; every problem is a `QuestionError` naming the question."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QuestionError(f"{path}: {exc}") from exc
    try:
        qs = QuestionSet.model_validate(raw)
    except ValidationError as exc:
        raise QuestionError(f"{path}: {exc}") from exc
    duplicates = [i for i, n in Counter(q.id for q in qs.questions).items() if n > 1]
    if duplicates:
        raise QuestionError(f"{path}: duplicate question ids: {', '.join(sorted(duplicates))}")
    for q in qs.questions:
        names = _PROMPT_ARGUMENTS[q.role]
        given = [name in q.prompt_args for name in names]
        if given != sorted(given, reverse=True):
            raise QuestionError(
                f"{path}: {q.id}: prompt argument {names[given.index(True, given.index(False))]!r} "
                f"needs every earlier argument ({', '.join(names)}) to be set too"
            )
    for name, members in qs.triples().items():
        if set(members) != set(Role):
            missing = sorted(r.value for r in set(Role) - set(members))
            raise QuestionError(f"{path}: triple {name!r} has no question for {', '.join(missing)}")
    counts = Counter(q.triple for q in qs.questions if q.triple)
    over = [t for t, n in counts.items() if n != len(Role)]
    if over:
        raise QuestionError(
            f"{path}: triples with more than one question per Role: {', '.join(sorted(over))}"
        )
    return qs
