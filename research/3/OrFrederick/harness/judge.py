"""LLM-as-judge for what regex cannot grade, plus calibration against human labels.

One judge call per criterion (binary PASS/FAIL/UNKNOWN + one-sentence reason), different model
from the one under test, run via `claude -p --json-schema` so no extra SDK/key plumbing is needed.
Calibrate: humans label ~40 transcripts per criterion; report TPR, TNR and Cohen's kappa on a
held-out 25%; the judge gates CI only when kappa >= 0.6 on that held-out set.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request

RUBRIC = {
    "role_fit": "Does the answer serve the stated Role? Engineer: concrete Log Entries, nearby events and a check to run. "
                "Team Lead: counts/trends over a stated period, Components/Teams, what to investigate first. "
                "Manager: short prioritised risks and actions using only measured frequency and affected Components.",
    "hypothesis_labelled": "Is every causal claim either backed by cited Evidence or explicitly labelled as a Hypothesis?",
    "no_invented_impact": "Does the answer avoid asserting financial loss, downtime, SLA or customer impact as fact? "
                          "Saying these are Unknown is a PASS.",
    "next_step_actionable": "Is the Next Step specific, feasible and supported by the Observed Facts?",
    "scope_stated": "Does the answer state the Data Scope (Dataset, time range, filters) of its numbers?",
}
SCHEMA = {"type": "object", "properties": {"verdict": {"enum": ["PASS", "FAIL", "UNKNOWN"]}, "reason": {"type": "string"}},
          "required": ["verdict", "reason"]}


RWTHGPT_URL = os.environ.get("RWTHGPT_BASE_URL", "https://chat.kiconnect.nrw/api/v1") + "/chat/completions"


def _judge_rwthgpt(prompt: str, model: str) -> dict:
    """Free judge on RWTHgpt / KI:connect (OpenAI-compatible). Key only from RWTHGPT_API_KEY."""
    body = {"model": model, "max_completion_tokens": 4000,
            "messages": [{"role": "system", "content": "Reply with only a JSON object: "
                          '{"verdict": "PASS"|"FAIL"|"UNKNOWN", "reason": "<one sentence>"}'},
                         {"role": "user", "content": prompt}]}
    req = urllib.request.Request(RWTHGPT_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {os.environ['RWTHGPT_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        content = json.load(r)["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", content, re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        out = {}
    if out.get("verdict") not in ("PASS", "FAIL", "UNKNOWN"):
        return {"verdict": "UNKNOWN", "reason": f"unparseable judge output: {content[:200]}"}
    return out


def judge(criterion: str, role: str, question: str, answer: str, model: str = "opus") -> dict:
    """model: a Claude alias/id (via `claude -p`), or `rwthgpt:<model id>` for the free RWTHgpt judge."""
    prompt = (f"You grade one criterion of an answer produced by a log-analysis agent.\nCriterion: {RUBRIC[criterion]}\n"
              f"Role: {role}\nQuestion: {question}\n<answer>\n{answer}\n</answer>\n"
              "Reply PASS or FAIL; reply UNKNOWN if the criterion cannot be judged from the text.")
    if model.startswith("rwthgpt:"):
        return _judge_rwthgpt(prompt, model.removeprefix("rwthgpt:"))
    p = subprocess.run(["claude", "-p", prompt, "--tools", "", "--output-format", "json", "--model", model,
                        "--json-schema", json.dumps(SCHEMA), "--no-session-persistence"],
                       capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
    out = json.loads(p.stdout)
    return out.get("structured_output") or {"verdict": "UNKNOWN", "reason": out.get("result", "")[:200]}


def agreement(human: list[bool], judge_: list[bool]) -> dict:
    """TPR/TNR of the judge (positive = PASS) and Cohen's kappa."""
    n = len(human)
    tp = sum(h and j for h, j in zip(human, judge_)); tn = sum((not h) and (not j) for h, j in zip(human, judge_))
    pos, neg = sum(human), n - sum(human)
    po = (tp + tn) / n
    pj = sum(judge_) / n; ph = pos / n
    pe = ph * pj + (1 - ph) * (1 - pj)
    return {"n": n, "tpr": tp / pos if pos else None, "tnr": tn / neg if neg else None, "accuracy": po,
            "kappa": (po - pe) / (1 - pe) if pe < 1 else 1.0}
