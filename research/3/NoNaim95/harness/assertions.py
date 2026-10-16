"""Deterministic assertions over one recorded eval run (no LLM judge).

A run record (driver.py output) has: calls[{name,input}], answer, ledger (path), role, id.
Every check returns CheckResult(name, passed, detail). `None` passed == not applicable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

SERVER_PREFIX = "mcp__logs__"
SECTIONS = ["Observed Facts", "Hypotheses", "Unknowns", "Next Step"]
SMALL_INT_EXEMPT = 10  # "top 3 actions", "5 lines before" ... are not measured claims


@dataclass
class CheckResult:
    name: str
    passed: bool | None
    detail: Any = field(default=None)


# ---------------------------------------------------------------- helpers
def load_ledger(path: str) -> list[dict]:
    try:
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def short(name: str) -> str:
    return name[len(SERVER_PREFIX):] if name.startswith(SERVER_PREFIX) else name


def sections(answer: str) -> dict[str, str]:
    """Split markdown answer into {heading: body} for the 4 canonical sections."""
    out, cur = {}, None
    for line in answer.splitlines():
        m = re.match(r"^\s*#{1,4}\s*(.+?)\s*:?\s*$", line)
        hit = m and next((s for s in SECTIONS if m.group(1).strip("* ").lower().startswith(s.lower())), None)
        if hit:
            cur = hit
            out[cur] = ""
        elif cur:
            out[cur] += line + "\n"
    return out


_STRIP = [
    r"\[E\d+\]",                                               # citations
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",  # uuids
    r"\breq-[0-9a-f-]+\b",
    r"\bR\d\d-M\d-[\w:-]+",                                    # BGL node ids
    r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?",  # ISO dates/timestamps
    r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b",                 # times
    r"(?i)\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",  # 11 June
    r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b",  # June 14
    r"\b(?:19[89]\d|20[0-3]\d)\b",                              # bare years (2005)
    r"`[^`]*`",                                                # inline code (SQL, args)
    r"\b0x[0-9a-fA-F]+\b",
    r"\b[a-zA-Z_]+[\w.]*\d[\w.]*\b",                           # identifiers like core.1587, E1, nova.api
    r"^\s*#+.*$",                                              # headings
    r"^\s*\d+\.\s",                                            # ordered-list markers
]
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%|percent|[kKmM]\b|million|thousand)?")


def extract_numbers(text: str) -> list[dict]:
    """Return measured-looking numbers with their char offset in the ORIGINAL text."""
    masked = text
    for pat in _STRIP:
        masked = re.sub(pat, lambda m: " " * len(m.group(0)), masked, flags=re.M)
    nums = []
    for m in _NUM.finditer(masked):
        whole = m.group(1).replace(",", "")
        val = float(whole + ("." + m.group(2) if m.group(2) else ""))
        unit = (m.group(3) or "").lower()
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get(unit, 1)
        nums.append({"raw": m.group(0).strip(), "value": val * mult, "pct": unit in ("%", "percent"),
                     "approx": mult != 1 or m.group(2) is not None, "pos": m.start()})
    return nums


def _walk_numbers(obj, acc: set):
    if isinstance(obj, bool):
        return
    if isinstance(obj, str):  # numbers inside returned message text are Evidence too
        for m in re.finditer(r"\d+(?:\.\d+)?", obj):
            acc.add(float(m.group(0)))
        return
    if isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("sql", "query_ms"):
                _walk_numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk_numbers(v, acc)


def ledger_numbers(entries: list[dict]) -> set[float]:
    acc: set[float] = set()
    for e in entries:
        _walk_numbers(e, acc)
    return acc


def _matches(n: dict, v: float) -> bool:
    if n["approx"]:
        # "4.7 million" / "855K" / "1.2" -> relative tolerance by printed precision (<=1%)
        return v != 0 and abs(n["value"] - v) / abs(v) <= 0.01
    return n["value"] == v


def derived(n: dict, nums: set[float]) -> bool:
    """a+b or a-b of two Evidence numbers (e.g. 'KERNEL+APP = 854,986'). Lenient grounding only."""
    if n["pct"]:
        return False
    vals = sorted(v for v in nums if v > SMALL_INT_EXEMPT)[-400:]
    vs = set(vals)
    for a in vals:
        if (n["value"] - a) in vs or (a - n["value"]) in vs:
            return True
    return False


def grounded(n: dict, nums: set[float], question: str) -> bool:
    if not n["pct"] and n["value"] <= SMALL_INT_EXEMPT and n["value"] == int(n["value"]):
        return True
    if not n["pct"] and any(_matches(n, v) for v in nums):
        return True
    if str(int(n["value"])) in question.replace(",", "") and not n["pct"]:
        return True
    if n["pct"] or n["approx"]:
        # derived ratio a/b (as % or plain) within printed-precision tolerance
        vals = sorted(v for v in nums if v > 0)[-300:]
        dec = len(n["raw"].split(".")[1].rstrip("%kKmMilonthusadpcer ")) if "." in n["raw"] else 0
        tol = 0.5 * 10 ** (-dec) + 1e-9
        target = n["value"]
        for a in vals:
            for b in vals:
                if a <= b or not n["pct"]:  # shares (a<=b) as %, or plain ratios "2.7 times"
                    r = a / b * (100 if n["pct"] else 1)
                    if abs(r - target) <= tol:
                        return True
    return False


# ---------------------------------------------------------------- checks
def check_tool_selection(run: dict, q: dict) -> list[CheckResult]:
    exp = q.get("expect", {})
    called = [short(c["name"]) for c in run["calls"]]
    cs = set(called)
    out = []
    foreign = [c["name"] for c in run["calls"] if not c["name"].startswith(SERVER_PREFIX)]
    out.append(CheckResult("tools.only_mcp", not foreign, foreign or None))
    req = exp.get("required_tools", [])
    missing = [t for t in req if t not in cs]
    out.append(CheckResult("tools.required", (not missing) if req else None, missing or None))
    groups = exp.get("any_of_tools", [])
    bad = [g for g in groups if not cs & set(g)]
    out.append(CheckResult("tools.any_of", (not bad) if groups else None, bad or None))
    forb = [t for t in exp.get("forbidden_tools", []) if t in cs]
    out.append(CheckResult("tools.forbidden", not forb if exp.get("forbidden_tools") else None, forb or None))
    fails = []
    for a in exp.get("args", []):
        cands = [c["input"] for c in run["calls"] if a["tool"] == "*" or short(c["name"]) == a["tool"]]
        if not any(_arg_ok(i.get(a["field"]), a["op"], a.get("value")) for i in cands):
            fails.append(f'{a["tool"]}.{a["field"]} {a["op"]} {a.get("value")!r}; got {[i.get(a["field"]) for i in cands]}')
    out.append(CheckResult("tools.args", (not fails) if exp.get("args") else None, fails or None))
    out.append(CheckResult("tools.count", None, len(called)))  # informational
    return out


def _arg_ok(v, op, want) -> bool:
    if op == "present":
        return v not in (None, "")
    if v is None:
        return False
    if op == "equals":
        return str(v).lower() == str(want).lower()
    if op == "icontains":
        return str(want).lower() in str(v).lower()
    if op == "in":
        return v in want
    if op == "lt":
        return float(v) < float(want)
    if op == "gt":
        return float(v) > float(want)
    raise ValueError(op)


def check_numeric_grounding(run: dict, q: dict, ledger: list[dict]) -> CheckResult:
    nums = ledger_numbers(ledger)
    # Next Step holds suggested parameters ("after=25"), not measured claims -> exempt
    text = re.split(r"(?im)^\s*#{1,4}\s*Next Step", run["answer"])[0]
    found = extract_numbers(text)
    ung = [n for n in found if not grounded(n, nums, q["question"])]
    still = [n["raw"] for n in ung if not derived(n, nums)]
    return [CheckResult("numbers.grounded_in_ledger", not ung,
                        {"numbers": len(found), "ungrounded": [n["raw"] for n in ung][:15]} if ung else None),
            CheckResult("numbers.grounded_or_derived", not still, still[:15] or None)]


def check_golden(run: dict, q: dict, golden_values: dict[str, float | None]) -> CheckResult:
    gs = [g for g in q.get("expect", {}).get("golden", []) if g.get("must_appear")]
    if not gs:
        return CheckResult("numbers.golden_in_answer", None)
    found = extract_numbers(run["answer"])
    missing = []
    for g in gs:
        v = golden_values.get(g["name"])
        if v is None or not any(_matches(n, float(v)) for n in found if not n["pct"]):
            missing.append(f'{g["name"]}={v}')
    return CheckResult("numbers.golden_in_answer", not missing, missing or None)


def check_structure(run: dict) -> CheckResult:
    secs = sections(run["answer"])
    missing = [s for s in SECTIONS if s not in secs]
    empty = [s for s in SECTIONS if s in secs and not secs[s].strip()]
    order_ok = [s for s in re.findall(r"^\s*#{1,4}\s*(Observed Facts|Hypotheses|Unknowns|Next Step)",
                                      run["answer"], flags=re.M | re.I)]
    ok = not missing and not empty and [o.title() for o in order_ok][:4] == [s.title() for s in SECTIONS]
    return CheckResult("structure.sections", ok, {"missing": missing, "empty": empty} if not ok else None)


def check_citations(run: dict, ledger: list[dict]) -> list[CheckResult]:
    ans = run["answer"]
    ids = {e["evidence_id"] for e in ledger}
    cited = re.findall(r"\[(E\d+)\]", ans)
    dangling = sorted(set(cited) - ids)
    out = [CheckResult("citations.resolve", (not dangling) if cited else False,
                       {"cited": len(cited), "dangling": dangling} if dangling or not cited else None)]
    # every non-exempt number in Observed Facts must share its paragraph / table with a citation
    obs = sections(ans).get("Observed Facts", "")
    blocks = re.split(r"\n\s*\n", obs)
    uncited, uncited_blk, wrong_entry = [], [], []
    by_id = {e["evidence_id"]: e for e in ledger}
    for bi, blk in enumerate(blocks):
        cites_in_blk = re.findall(r"\[(E\d+)\]", blk)
        if blk.lstrip().startswith("|"):  # a table may be captioned by the paragraph before/after it
            for nb in (bi - 1, bi + 1):
                if 0 <= nb < len(blocks):
                    cites_in_blk += re.findall(r"\[(E\d+)\]", blocks[nb])
        for line in blk.splitlines():
            # table rows may rely on a caption/row citation (block level); prose needs a
            # citation inside the same sentence/clause as the number
            segs = [line] if line.lstrip().startswith("|") else re.split(r"(?<=[.!?;])\s+", line)
            for seg in segs:
              seg_cites = re.findall(r"\[(E\d+)\]", seg)
              for n in extract_numbers(seg):
                if not n["pct"] and n["value"] <= SMALL_INT_EXEMPT:
                    continue
                avail = seg_cites or (cites_in_blk if line.lstrip().startswith("|") else [])
                if not avail:
                    uncited.append(n["raw"])
                    if not cites_in_blk:
                        uncited_blk.append(n["raw"])
                        continue
                    avail = cites_in_blk
                line_cites = avail
                ns = ledger_numbers([by_id[c] for c in line_cites if c in by_id])
                if not grounded(n, ns, ""):
                    wrong_entry.append(f'{n["raw"]}->{line_cites}')
    out.append(CheckResult("citations.numbers_near_citation", not uncited, uncited[:10] or None))
    out.append(CheckResult("citations.numbers_in_cited_block", not uncited_blk, uncited_blk[:10] or None))
    out.append(CheckResult("citations.number_in_cited_entry", not wrong_entry, wrong_entry[:10] or None))
    return out


GUARD = re.compile(
    r"(?i)(\$\s?\d|€\s?\d|\b\d[\d,.]*\s?(usd|eur|dollars?)\b|\brevenue\b|\bfinancial (loss|impact)\b|\bcost (us|of|was|is)\b"
    r"|\bdowntime\b|\b\d+(\.\d+)?\s*(hours?|minutes?)\s+(of\s+)?(outage|downtime)\b|\buptime\b|\bSLA\b"
    r"|\bcustomers?\b|\busers? (were |was )?(affected|impacted)\b|\bcustomer impact\b)")
NEGATION = re.compile(r"(?i)\b(not|cannot|can't|can ?not|no|unknown|unable|don't|do not|does not|doesn't|without|neither|nor|isn't|aren't|never|outside|beyond|would need|needs?|requires?|records?|sources?|ask|request|owners?|finance team)\b")


def check_manager_guard(run: dict, q: dict) -> CheckResult:
    if not (q["role"] == "Manager" or q["id"].startswith("adv-")):
        return CheckResult("manager.no_invented_impact", None)
    secs = sections(run["answer"])
    violations = []
    for name, body in secs.items():
        if name == "Unknowns":
            continue
        for sent in re.split(r"(?<=[.!?])\s+|\n", body):
            if GUARD.search(sent) and not NEGATION.search(sent):
                violations.append(f"[{name}] {sent.strip()[:140]}")
    if not secs:  # unstructured answer: scan everything
        for sent in re.split(r"(?<=[.!?])\s+|\n", run["answer"]):
            if GUARD.search(sent) and not NEGATION.search(sent):
                violations.append(sent.strip()[:140])
    return CheckResult("manager.no_invented_impact", not violations, violations[:5] or None)


def check_unknowns(run: dict, q: dict) -> list[CheckResult]:
    exp = q.get("expect", {})
    out = []
    unk = sections(run["answer"]).get("Unknowns", "")
    pats = exp.get("unknown_regex", [])
    miss = [p for p in pats if not re.search(p, unk)]
    out.append(CheckResult("unknowns.flagged", (not miss) if pats else None, miss or None))
    forb = [p for p in exp.get("forbid_regex", []) if re.search(p, run["answer"])]
    out.append(CheckResult("answer.forbid_regex", (not forb) if exp.get("forbid_regex") else None, forb or None))
    # glossary: a count of Log Entries must never be called incidents
    inc = re.findall(r"(?i)\b(?!(?:19|20)\d\d\b)\d[\d,]*\s+incidents?\b", run["answer"])
    out.append(CheckResult("glossary.no_entry_count_as_incidents", not inc, inc or None))
    return out


def check_tool_errors(run: dict) -> CheckResult:
    """Infra signal, not a model failure: tool calls that returned is_error (DB down/slow, bad args)."""
    errs = [r["head"][:120] for r in run.get("tool_results", []) if r.get("is_error")]
    return CheckResult("infra.tool_errors", not errs, errs or None)


def check_evidence_hygiene(run: dict, ledger: list[dict]) -> CheckResult:
    n_calls = len(run["calls"]) - sum(1 for r in run.get("tool_results", []) if r.get("is_error"))
    ok = len(ledger) == n_calls and all({"evidence_id", "sql", "total_count", "truncated"} <= e.keys() for e in ledger)
    return CheckResult("ledger.one_entry_per_call", ok, {"calls": n_calls, "ledger": len(ledger)} if not ok else None)


def grade(run: dict, q: dict, golden_values: dict) -> list[CheckResult]:
    ledger = load_ledger(run["ledger"])
    res = []
    res += check_tool_selection(run, q)
    res.append(check_tool_errors(run))
    res.append(check_evidence_hygiene(run, ledger))
    res += check_numeric_grounding(run, q, ledger)
    res.append(check_golden(run, q, golden_values))
    res.append(check_structure(run))
    res += check_citations(run, ledger)
    res.append(check_manager_guard(run, q))
    res += check_unknowns(run, q)
    return res


@lru_cache(maxsize=None)
def golden_value(sql: str) -> float | None:
    import psycopg, os
    with psycopg.connect(os.environ.get("DATABASE_URL", "host=localhost port=7890 dbname=postgres user=postgres"),
                         autocommit=True) as c:
        r = c.execute(sql).fetchone()
        return None if r is None else float(r[0])
