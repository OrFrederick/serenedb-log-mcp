"""Deterministic graders for one eval trial (T17): infra, L1 trajectory and L2 grounding.

Reuses research/3-NoNaim95 `harness/assertions.py`; the L2 answer checks are `verify.check_answer`,
the same code the `verify_answer` tool runs, so the agent's self-check and the grader never disagree.

- infra (not the model's fault): the run itself failed (no result event, our MCP server not
  connected, an authentication error), or a tool call failed for a reason outside the model's
  control (database down, timeout, an unhandled server error). A trial with an infra failure is
  reported as `infra` and left out of the model's pass rate, so a broken database never reads as a
  model that picks the wrong tools (the #3 pilot's hidden tool failures).
- L1 (trajectory): only our tools, must-call, any-of groups, must-not-call, an ordered subsequence
  (never an exact sequence), argument predicates, the Role argument on every call, and budgets.
  A tool error the model caused (a bad argument) is reported by `l1.arg_errors` without gating: the
  tool's message tells the model how to fix the call, and recovering is the intended behaviour.
- L2 (grounding): `verify.check_answer` (sections, citations resolve in the trial's own ledger,
  numbers in the cited Evidence, no rounding, Incidents, Manager rules), golden values in the
  answer, and the question's mention, forbid and Unknowns regexes.

A check with `passed=None` did not apply; a check with `gate=False` is reported but never fails a
trial.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from questions import ArgPredicate, Question
from serenedb_log_mcp.toolkit import CONTRACT_BUG_HINT, RETRY_HINT
from serenedb_log_mcp.verify import INCIDENT_TOOLS, check_answer, numbers_in, split_sections
from transcript import ToolCall, Trial

Layer = Literal["infra", "L1", "L2"]
Verdict = Literal["pass", "fail", "infra"]

INFRA_ERROR = re.compile(
    "|".join(
        [
            # toolkit.py turns an unexpected exception into a ToolError ending in RETRY_HINT, and a
            # broken Evidence contract into one ending in CONTRACT_BUG_HINT.
            re.escape(RETRY_HINT[:40]),
            re.escape(CONTRACT_BUG_HINT[:40]),
            r"Evidence Ledger could not be read or written",
            # The SDK's bare message for an exception that is not a ToolError: no text after the name.
            r"Error executing tool [\w.-]+\s*$",
            r"could not connect|connection (?:refused|reset|closed|failed)|timed? ?out|canceling statement",
            r"server closed the connection|too many connections",
        ]
    ),
    re.IGNORECASE,
)
"""Tool-error texts that are infrastructure failures. Any other tool error (a ToolError naming a bad
argument, an input validation error) is the model's to fix. Every ToolError reaches the client as
`Error executing tool <name>: <message>`, so that prefix alone says nothing."""

MAX_DETAIL = 400


@dataclass(frozen=True)
class Check:
    name: str
    layer: Layer
    passed: bool | None
    detail: str = ""
    gate: bool = True


@dataclass(frozen=True)
class Grade:
    question_id: str
    checks: list[Check] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if any(c.layer == "infra" and c.passed is False for c in self.checks):
            return "infra"
        if all(c.passed is not False for c in self.checks if c.gate):
            return "pass"
        return "fail"

    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.gate and c.passed is False]


def _cut(text: str) -> str:
    return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 3] + "..."


# infra ---------------------------------------------------------------------------------------------


def infra_checks(trial: Trial) -> list[Check]:
    error = trial.run_error()
    out = [Check("infra.run", "infra", error is None, _cut(error or ""))]
    names = {c.id: c.short for c in trial.stream.calls}
    infra = [
        f"{names.get(r.tool_use_id, '?')}: {r.text[:160]}"
        for r in trial.stream.results
        if r.is_error and INFRA_ERROR.search(r.text)
    ]
    out.append(Check("infra.tool_errors", "infra", not infra, _cut("; ".join(infra))))
    return out


# L1 ------------------------------------------------------------------------------------------------


def _lower(value: Any) -> str:
    return str(value).strip().lower()


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def arg_matches(value: Any, predicate: ArgPredicate) -> bool:
    """Whether one call's argument value satisfies the predicate."""
    op, want = predicate.op, predicate.value
    if op == "present":
        return not _empty(value)
    if op == "absent":
        return _empty(value)
    if _empty(value):
        return False
    if op == "equals":
        return _lower(value) == _lower(want)
    if op == "icontains":
        text = " ".join(map(str, value)) if isinstance(value, list) else str(value)
        return _lower(want) in text.lower()
    if op == "contains":
        items = value if isinstance(value, list) else [value]
        return any(_lower(item) == _lower(want) for item in items)
    if op == "in":
        return _lower(value) in {_lower(w) for w in want}
    raise ValueError(f"unknown op {op!r}")  # pragma: no cover - Op is a closed Literal


def is_subsequence(wanted: Sequence[str], called: Sequence[str]) -> bool:
    """Whether `wanted` appears in `called` in order, not necessarily adjacent."""
    it = iter(called)
    return all(any(name == step for name in it) for step in wanted)


def l1_checks(trial: Trial, question: Question) -> list[Check]:
    expect = question.expect
    calls = trial.stream.calls
    ours = [c for c in calls if c.ours]
    called = [c.short for c in ours]
    called_set = set(called)
    out: list[Check] = []

    foreign = sorted({c.name for c in calls if not c.ours})
    out.append(Check("l1.only_our_tools", "L1", not foreign, ", ".join(foreign)))

    missing = [t for t in expect.must_call if t not in called_set]
    out.append(
        Check(
            "l1.must_call",
            "L1",
            not missing if expect.must_call else None,
            f"missing: {missing}" if missing else "",
        )
    )

    unmet = [g for g in expect.any_of if not called_set & set(g)]
    out.append(
        Check(
            "l1.any_of",
            "L1",
            not unmet if expect.any_of else None,
            f"none called of: {unmet}" if unmet else "",
        )
    )

    forbidden = [t for t in expect.must_not_call if t in called_set]
    out.append(
        Check(
            "l1.must_not_call",
            "L1",
            not forbidden if expect.must_not_call else None,
            f"called: {forbidden}" if forbidden else "",
        )
    )

    in_order = is_subsequence(expect.order, called)
    out.append(
        Check(
            "l1.order",
            "L1",
            in_order if expect.order else None,
            "" if in_order else f"wanted {expect.order} in order, called {called}",
        )
    )

    failures = []
    for predicate in expect.args:
        candidates = [c for c in ours if predicate.tool in ("*", c.short)]
        if not any(arg_matches(c.input.get(predicate.arg), predicate) for c in candidates):
            seen = [c.input.get(predicate.arg) for c in candidates]
            failures.append(f"{predicate.describe()}; got {seen}")
    out.append(Check("l1.args", "L1", not failures if expect.args else None, _cut("; ".join(failures))))

    wrong_role = [
        f"{c.short}(role={c.input.get('role')!r})" for c in ours if c.input.get("role") != question.role
    ]
    out.append(Check("l1.role_arg", "L1", not wrong_role if ours else None, _cut(", ".join(wrong_role))))

    out.append(_budget(trial, question, len(calls)))

    names = {c.id: c.short for c in calls}
    model_errors = [
        f"{names.get(r.tool_use_id, '?')}: {r.text[:120]}"
        for r in trial.stream.results
        if r.is_error and not INFRA_ERROR.search(r.text)
    ]
    out.append(Check("l1.arg_errors", "L1", not model_errors, _cut("; ".join(model_errors)), gate=False))
    return out


def _budget(trial: Trial, question: Question, n_calls: int) -> Check:
    budget = question.expect.budget
    over = []
    if n_calls > budget.max_calls:
        over.append(f"{n_calls} calls > {budget.max_calls}")
    turns = trial.stream.num_turns
    if budget.max_turns is not None and turns is not None and turns > budget.max_turns:
        over.append(f"{turns} turns > {budget.max_turns}")
    cost = trial.stream.cost_usd
    if budget.max_cost_usd is not None and cost is not None and cost > budget.max_cost_usd:
        over.append(f"${cost:.4f} > ${budget.max_cost_usd:.4f}")
    return Check("l1.budget", "L1", not over, "; ".join(over))


# L2 ------------------------------------------------------------------------------------------------


def _unknowns_text(answer: str) -> str:
    sections = split_sections(answer)
    return sections.get("Unknowns", "")


def l2_checks(
    trial: Trial,
    question: Question,
    ledger: Sequence[Mapping[str, Any]],
    golden_values: Mapping[str, float | None] | None = None,
) -> list[Check]:
    answer = trial.stream.answer
    expect = question.expect
    out: list[Check] = []

    offered = {c.removeprefix("mcp__serenedb-log__") for c in trial.stream.tools}
    report = check_answer(question.role, answer, ledger, incident_tools=bool(INCIDENT_TOOLS & offered))
    problems = "; ".join(f"{p.kind} [{p.section}]: {p.text[:100]}" for p in report.problems)
    out.append(Check("l2.verify", "L2", report.ok, _cut(problems)))

    if expect.golden:
        values = golden_values or {}
        stated = [n.value for n in numbers_in(answer) if not n.percent]
        missing, unknown = [], []
        for golden in expect.golden:
            value = values.get(golden.name)
            if value is None:
                unknown.append(golden.name)
            elif not any(abs(v - value) < 1e-9 for v in stated):
                missing.append(f"{golden.name}={value:g}")
        if unknown and not missing:
            out.append(Check("l2.golden", "L2", None, f"not evaluated (no database): {unknown}"))
        else:
            out.append(
                Check("l2.golden", "L2", not missing, f"not in the answer: {missing}" if missing else "")
            )
    else:
        out.append(Check("l2.golden", "L2", None))

    absent = [p for p in expect.mention if not re.search(p, answer)]
    out.append(
        Check(
            "l2.mention",
            "L2",
            not absent if expect.mention else None,
            f"no match: {absent}" if absent else "",
        )
    )

    present = [m.group(0) for p in expect.forbid if (m := re.search(p, answer))]
    out.append(
        Check(
            "l2.forbid", "L2", not present if expect.forbid else None, f"found: {present}" if present else ""
        )
    )

    unknowns = _unknowns_text(answer)
    unflagged = [p for p in expect.unknowns if not re.search(p, unknowns)]
    out.append(
        Check(
            "l2.unknowns",
            "L2",
            not unflagged if expect.unknowns else None,
            f"not in Unknowns: {unflagged}" if unflagged else "",
        )
    )
    return out


# a trial -------------------------------------------------------------------------------------------


def grade_trial(
    trial: Trial,
    question: Question,
    ledger: Sequence[Mapping[str, Any]] | None = None,
    golden_values: Mapping[str, float | None] | None = None,
) -> Grade:
    """Grade one trial. An infra failure of the run skips L1 and L2: there is nothing to grade."""
    checks = infra_checks(trial)
    if checks[0].passed is False:
        return Grade(question.id, checks)
    records = trial.ledger() if ledger is None else ledger
    checks += l1_checks(trial, question)
    checks += l2_checks(trial, question, records, golden_values)
    return Grade(question.id, checks)


def tool_signature(calls: Sequence[ToolCall]) -> frozenset[str]:
    """The (tool, group_by) set of a trial, for comparing the Roles of a cross-Role triple."""
    out = set()
    for call in calls:
        if not call.ours or call.short in ("get_role_brief", "verify_answer", "get_evidence"):
            continue
        group_by = call.input.get("group_by")
        out.add(f"{call.short}:{group_by}" if group_by else call.short)
    return frozenset(out)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)
