#!/usr/bin/env python3
"""Grade an eval run and write its report (T17): pass rate, pass^k and Wilson CIs per question.

Usage (DATABASE_URL and SLM_SCHEMA from the environment, for the golden SQL):
    uv run python eval/report.py eval/runs/haiku-k3
    uv run python eval/report.py eval/runs/haiku-k3 --results docs/results/eval.md --no-golden

Reads `<run>/run.json` and `<run>/trials/*.json` (eval/driver.py), grades every trial with
eval/graders.py, writes `<run>/grades.jsonl` and `<run>/report.md`, and with `--results` puts the
report into its own section of that file, between `<!-- BEGIN <label> -->` and `<!-- END <label> -->`,
leaving the other sections (other models, other k) as they are.

Statistics (#3): a trial with an infra failure is counted separately and left out of every pass
rate. Per question: passes out of graded trials, the Wilson 95% interval on that rate, and pass^k
(all k trials pass, the unbiased estimator). Per group (Role, adversarial, split, overall): the
trial pass rate, the mean pass^k, and the share of questions whose every trial passed, with its
Wilson interval over questions, not over trials, which would treat repeats of one question as
independent and give a falsely narrow interval.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from graders import Grade, grade_trial, jaccard, tool_signature
from questions import DEFAULT_PATH, Question, QuestionError, QuestionSet, load
from serenedb_log_mcp.config import ConfigError, Role, load_settings
from stats import pass_hat_k, wilson
from transcript import Trial

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ("engineer", "team_lead", "manager", "adversarial")
MAX_FAILURES = 60
PENDING = "pending"
TITLE = (
    "# Eval results\n\n"
    "L1 (tool choice) and L2 (grounding) results of `make eval`, one section per model and k, written by "
    "`eval/report.py`. Method: `eval/driver.py`, `eval/graders.py`, `eval/questions.yaml`.\n"
)


class ReportError(RuntimeError):
    """The run directory cannot be graded."""


@dataclass(frozen=True)
class Graded:
    trial: Trial
    question: Question
    grade: Grade


# Loading and grading --------------------------------------------------------------------------------


def load_trials(run_dir: Path) -> list[Trial]:
    paths = sorted((run_dir / "trials").glob("*__t*.json"))
    paths = [p for p in paths if not p.name.endswith(".mcp.json")]
    if not paths:
        raise ReportError(f"no trials in {run_dir / 'trials'}; run eval/driver.py first")
    trials = []
    for path in paths:
        try:
            trials.append(Trial.from_json(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ReportError(f"{path} is not a trial record: {exc}") from exc
    return trials


def golden_values(questions: Sequence[Question], *, enabled: bool = True) -> dict[str, float | None]:
    """Run every golden SQL once against SLM_SCHEMA; None for each when disabled or unreachable."""
    goldens = {g.name: g.sql for q in questions for g in q.expect.golden}
    if not enabled or not goldens:
        return dict.fromkeys(goldens)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"report: golden SQL skipped: {exc}", file=sys.stderr)
        return dict.fromkeys(goldens)
    schema = settings.slm_schema  # validated as a plain lowercase identifier
    values: dict[str, float | None] = {}
    try:
        with psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5) as conn:
            for name, sql in goldens.items():
                query = sql.replace("{bgl}", f'"{schema}".bgl_entry').replace("{os}", f'"{schema}".os_entry')
                row = conn.execute(query.encode()).fetchone()
                values[name] = None if row is None or row[0] is None else float(row[0])
    except psycopg.Error as exc:
        print(f"report: golden SQL skipped, the database did not answer: {exc}".strip(), file=sys.stderr)
        return dict.fromkeys(goldens)
    return values


def grade_run(trials: Sequence[Trial], qs: QuestionSet, golden: Mapping[str, float | None]) -> list[Graded]:
    by_id = qs.by_id()
    out = []
    for trial in trials:
        question = by_id.get(trial.question_id)
        if question is None:
            raise ReportError(
                f"trial {trial.question_id} t{trial.repeat} has no question in the question file"
            )
        out.append(Graded(trial, question, grade_trial(trial, question, golden_values=golden)))
    return out


# Aggregation ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuestionStats:
    question: Question
    trials: int
    graded: int
    passed: int
    infra: int
    pass_hat_k: float | None
    failed_checks: Counter[str]

    @property
    def rate(self) -> float | None:
        return self.passed / self.graded if self.graded else None

    @property
    def all_passed(self) -> bool:
        return self.graded > 0 and self.passed == self.graded


def per_question(graded: Sequence[Graded], k: int) -> list[QuestionStats]:
    by_question: dict[str, list[Graded]] = defaultdict(list)
    for g in graded:
        by_question[g.question.id].append(g)
    out = []
    for items in by_question.values():
        verdicts = [g.grade.verdict for g in items]
        n = sum(v != "infra" for v in verdicts)
        c = verdicts.count("pass")
        failed: Counter[str] = Counter(
            ch.name for g in items for ch in g.grade.failed() if ch.layer != "infra"
        )
        out.append(
            QuestionStats(
                question=items[0].question,
                trials=len(items),
                graded=n,
                passed=c,
                infra=verdicts.count("infra"),
                pass_hat_k=pass_hat_k(n, c, k) if n else None,
                failed_checks=failed,
            )
        )
    order = {g: i for i, g in enumerate(GROUPS)}
    return sorted(out, key=lambda s: (order[s.question.group], s.question.id))


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _ci(successes: int, n: int) -> str:
    if n == 0:
        return "-"
    low, high = wilson(successes, n)
    return f"[{low * 100:.1f}%, {high * 100:.1f}%]"


def group_row(name: str, stats: Sequence[QuestionStats]) -> str:
    graded = sum(s.graded for s in stats)
    passed = sum(s.passed for s in stats)
    infra = sum(s.infra for s in stats)
    rated = [s for s in stats if s.graded]
    hats = [s.pass_hat_k for s in rated if s.pass_hat_k is not None]
    all_pass = sum(s.all_passed for s in rated)
    mean_hat = statistics.fmean(hats) if hats else None
    return (
        f"| {name} | {len(stats)} | {graded} | {infra} | {_pct(passed / graded if graded else None)} | "
        f"{_pct(mean_hat)} | {all_pass}/{len(rated)} | {_ci(all_pass, len(rated))} |"
    )


def check_table(graded: Sequence[Graded]) -> list[str]:
    tally: dict[tuple[str, str, bool], list[int]] = defaultdict(lambda: [0, 0])
    for g in graded:
        for ch in g.grade.checks:
            if ch.passed is None:
                continue
            cell = tally[(ch.name, ch.layer, ch.gate)]
            cell[0] += ch.passed
            cell[1] += 1
    lines = ["| Check | Layer | Gates | Passed | Applicable | Rate |", "|---|---|---|---|---|---|"]
    layers = {"infra": 0, "L1": 1, "L2": 2}
    for (name, layer, gate), (ok, n) in sorted(tally.items(), key=lambda i: (layers[i[0][1]], i[0][0])):
        lines.append(f"| {name} | {layer} | {'yes' if gate else 'no'} | {ok} | {n} | {_pct(ok / n)} |")
    return lines


def triple_table(graded: Sequence[Graded], qs: QuestionSet) -> list[str]:
    signatures: dict[str, frozenset[str]] = {}
    for g in graded:
        if g.grade.verdict != "infra":
            before = signatures.get(g.question.id, frozenset())
            signatures[g.question.id] = before | tool_signature(g.trial.stream.calls)
    lines = [
        "| Triple | Engineer | Team Lead | Manager | J(E,TL) | J(E,M) | J(TL,M) |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, members in sorted(qs.triples().items()):
        sig = {role: signatures.get(q.id) for role, q in members.items()}
        e, t, m = sig[Role.ENGINEER], sig[Role.TEAM_LEAD], sig[Role.MANAGER]
        lines.append(
            f"| {name} | {_tools(e)} | {_tools(t)} | {_tools(m)} | "
            f"{_overlap(e, t)} | {_overlap(e, m)} | {_overlap(t, m)} |"
        )
    return lines


def _tools(signature: frozenset[str] | None) -> str:
    if signature is None:
        return "not run"
    return ", ".join(sorted(signature)) or "no tools"


def _overlap(a: frozenset[str] | None, b: frozenset[str] | None) -> str:
    return "-" if a is None or b is None else f"{jaccard(a, b):.2f}"


def _commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
        )
    except OSError:
        return "unknown"
    return proc.stdout.strip() or "unknown"


def render(
    graded: Sequence[Graded],
    qs: QuestionSet,
    meta: Mapping[str, Any],
    *,
    label: str,
    golden_ran: bool,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    k = int(meta.get("k", 1))
    stats = per_question(graded, k)
    trials = [g.trial for g in graded]
    costs = [t.stream.cost_usd for t in trials if t.stream.cost_usd is not None]
    walls = [t.wall_s for t in trials]
    calls = [len(t.stream.calls) for t in trials]
    infra = sum(s.infra for s in stats)
    lines = [
        f"## {label}",
        "",
        f"- Model `{meta.get('model')}`, k = {k}, Role prompt delivered as "
        f"`{meta.get('role_delivery', 'slash')}`, graded {now():%Y-%m-%d %H:%M} UTC at commit `{_commit()}`.",
        f"- {len(stats)} questions, {len(trials)} trials, {infra} with an infra failure "
        "(left out of every rate).",
        f"- Cost ${sum(costs):.4f} in total, ${statistics.fmean(costs) if costs else 0:.4f} per trial; "
        f"wall time median {statistics.median(walls) if walls else 0:.1f} s; tool calls per trial mean "
        f"{statistics.fmean(calls) if calls else 0:.1f}.",
        "- Golden SQL "
        + ("ran against SLM_SCHEMA." if golden_ran else "was not run (no database): l2.golden is n/a."),
        "",
        "### By group",
        "",
        "Pass^k is the mean over questions of the chance that all k trials pass. The interval is the Wilson "
        "95% interval of the share of questions whose every trial passed, over questions.",
        "",
        "| Group | Questions | Graded trials | Infra | Trial pass rate | Mean pass^k | All trials pass "
        "| 95% CI |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for group in GROUPS:
        members = [s for s in stats if s.question.group == group]
        if members:
            lines.append(group_row(group, members))
    for split in ("tune", "holdout"):
        members = [s for s in stats if s.question.split == split]
        if members:
            lines.append(group_row(f"split: {split}", members))
    lines.append(group_row("**overall**", stats))
    lines += [
        "",
        "### Per question",
        "",
        "| Question | Group | Split | Passed / graded | Infra | Pass rate | 95% CI (Wilson) | pass^k "
        "| Failed checks |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        failed = ", ".join(f"{name} x{n}" for name, n in sorted(s.failed_checks.items())) or "-"
        lines.append(
            f"| {s.question.id} | {s.question.group} | {s.question.split} | {s.passed}/{s.graded} "
            f"| {s.infra} | {_pct(s.rate)} | {_ci(s.passed, s.graded)} | {_pct(s.pass_hat_k)} | {failed} |"
        )
    lines += ["", "### Per check", "", *check_table(graded)]
    lines += [
        "",
        "### Cross-Role triples",
        "",
        "The (tool, group_by) set each Role used over its trials, and the Jaccard overlap between Roles: "
        "a low overlap means the Roles asked different questions of the data.",
        "",
        *triple_table(graded, qs),
    ]
    failures = [
        f"- {g.question.id} t{g.trial.repeat}: **{ch.name}** {ch.detail}".rstrip()
        for g in graded
        for ch in g.grade.checks
        if ch.passed is False and (ch.gate or ch.layer == "infra")
    ]
    lines += ["", "### Failures", ""]
    lines += failures[:MAX_FAILURES] or ["None."]
    if len(failures) > MAX_FAILURES:
        lines.append(f"- ... and {len(failures) - MAX_FAILURES} more in grades.jsonl")
    return "\n".join(lines) + "\n"


def _section(label: str) -> re.Pattern[str]:
    return re.compile(rf"<!-- BEGIN {re.escape(label)} -->.*?<!-- END {re.escape(label)} -->\n*", re.DOTALL)


def write_section(path: Path, label: str, body: str) -> None:
    """Put `body` between the BEGIN and END markers of `label` in `path`, keeping other sections.

    The `pending` section (the note that no run is recorded yet) is removed by the first real one.
    """
    block = f"<!-- BEGIN {label} -->\n{body.rstrip()}\n<!-- END {label} -->\n"
    text = path.read_text(encoding="utf-8") if path.exists() else TITLE
    text = _section(PENDING).sub("", text)
    pattern = _section(label)
    found = pattern.search(text)
    text = pattern.sub(lambda _: block, text) if found else text.rstrip("\n") + "\n\n" + block
    path.write_text(text, encoding="utf-8")


def grades_jsonl(graded: Sequence[Graded]) -> str:
    rows = []
    for g in graded:
        rows.append(
            json.dumps(
                {
                    "question_id": g.question.id,
                    "repeat": g.trial.repeat,
                    "model": g.trial.model,
                    "verdict": g.grade.verdict,
                    "cost_usd": g.trial.stream.cost_usd,
                    "wall_s": g.trial.wall_s,
                    "tools": [c.short for c in g.trial.stream.calls],
                    "checks": [
                        {
                            "name": c.name,
                            "layer": c.layer,
                            "passed": c.passed,
                            "gate": c.gate,
                            "detail": c.detail,
                        }
                        for c in g.grade.checks
                    ],
                }
            )
        )
    return "\n".join(rows) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report.py", description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("run_dir", type=Path, help="the run directory eval/driver.py wrote")
    parser.add_argument("--questions", type=Path, default=DEFAULT_PATH, help="the question file")
    parser.add_argument("--results", type=Path, help="also write the report into this file's section")
    parser.add_argument("--label", help="the section label (default: <model>-k<k>)")
    parser.add_argument("--no-golden", action="store_true", help="do not run the golden SQL")
    args = parser.parse_args(argv)
    try:
        meta = json.loads((args.run_dir / "run.json").read_text(encoding="utf-8"))
        qs = load(args.questions)
        trials = load_trials(args.run_dir)
        asked = {q.id: q for q in qs.questions}
        golden = golden_values(
            [asked[t.question_id] for t in trials if t.question_id in asked], enabled=not args.no_golden
        )
        graded = grade_run(trials, qs, golden)
        label = args.label or f"{meta.get('model')}-k{meta.get('k')}"
        body = render(graded, qs, meta, label=label, golden_ran=any(v is not None for v in golden.values()))
        (args.run_dir / "grades.jsonl").write_text(grades_jsonl(graded), encoding="utf-8")
        (args.run_dir / "report.md").write_text(body, encoding="utf-8")
        if args.results:
            write_section(args.results, label, body)
    except (ReportError, QuestionError, OSError, json.JSONDecodeError) as exc:
        print(f"report: {exc}", file=sys.stderr)
        return 2
    print(body)
    print(f"written to {args.run_dir / 'report.md'}" + (f" and {args.results}" if args.results else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
