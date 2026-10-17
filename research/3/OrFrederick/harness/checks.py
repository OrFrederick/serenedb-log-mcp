"""Deterministic graders. Each check returns (passed: bool, detail: str).

Design: grade the outcome first (numbers, citations, sections, forbidden claims); tool-path checks
are loose (must_call / must_not_call / subsequence order / arg match), never an exact sequence.
"""
from __future__ import annotations

import re
from typing import Iterable

from evtrace import Trace

SECTIONS = ["Observed Facts", "Hypotheses", "Unknowns", "Next Step"]
CITE_RE = re.compile(r"\[E(\d+)\]")
# Identifiers, not measurements: "line 222715", "lines 222716–222765", IPs, "HTTP 503", "status: 200", "len: 323".
LINE_REF_RE = re.compile(r"\b(?:lines?|line_no)\s+\d[\d,]*(?:\s*(?:[–-]|to)\s*\d[\d,]*)?"
                         r"|\b\d{1,3}(?:\.\d{1,3}){3}\b"
                         r"|\b(?:HTTP|status(?: code)?|len):?\s*\d+", re.I)
# A number: 1,234,567 | 1234567 | 12.5 | 12.5% ; not part of an evidence id, ISO date, rack/node id.
NUM_RE = re.compile(r"(?<![\w\-:./\[])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(%?)(?![\w\-:/])")


# ---------- section parsing ----------
def split_sections(text: str) -> dict[str, str]:
    """Split on markdown headings or bold labels that name one of SECTIONS (case-insensitive)."""
    pat = re.compile(r"^\s*(?:#{1,4}\s*|\*\*)\s*(" + "|".join(SECTIONS) + r")s?\b.*$", re.I | re.M)
    out, marks = {}, list(pat.finditer(text))
    for i, m in enumerate(marks):
        name = next(s for s in SECTIONS if s.lower() == m.group(1).lower())
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[name] = text[m.end():end]
    return out


def check_sections(tr: Trace, required: Iterable[str] = SECTIONS):
    found = split_sections(tr.final_text)
    missing = [s for s in required if not found.get(s, "").strip()]
    return (not missing, f"missing/empty sections: {missing}" if missing else "all sections present")


# ---------- tool path ----------
def check_tools(tr: Trace, must_call=(), must_not_call=(), order=(), args=None, max_calls=None):
    names = [c.name for c in tr.tool_calls]
    problems = []
    problems += [f"not called: {t}" for t in must_call if t not in names]
    problems += [f"forbidden call: {t}" for t in must_not_call if t in names]
    it = iter(names)  # subsequence test: order must appear in this relative order
    if order and not all(t in it for t in order):
        problems.append(f"order {list(order)} not a subsequence of {names}")
    for tool, want in (args or {}).items():
        calls = [c for c in tr.tool_calls if c.name == tool]
        if calls and not any(_args_match(c.args, want) for c in calls):
            problems.append(f"{tool}: no call with args matching {want}; got {[c.args for c in calls]}")
    if max_calls is not None and len(names) > max_calls:
        problems.append(f"{len(names)} calls > max {max_calls}")
    errs = [c.name for c in tr.tool_calls if c.is_error]
    if errs:
        problems.append(f"tool errors: {errs}")
    return (not problems, "; ".join(problems) or f"calls={names}")


def _args_match(got: dict, want: dict) -> bool:
    for k, v in want.items():
        g = got.get(k)
        if isinstance(v, str) and v.startswith("re:"):
            if g is None or not re.search(v[3:], str(g), re.I):
                return False
        elif str(g).lower() != str(v).lower():
            return False
    return True


# ---------- citations and numbers ----------
_EMBEDDED_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _evidence_numbers(ev: dict) -> set[float]:
    nums: set[float] = set()

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            nums.add(float(x))
        elif isinstance(x, str):
            # Text cells (e.g. search_logs log content) carry measurements as plain text, not as
            # separate structured fields: "Total memory: 64172 MB" or "status: 503 len: 323".
            for tok in _EMBEDDED_NUM_RE.findall(x):
                nums.add(_to_float(tok))
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
    walk({k: ev.get(k) for k in ("total_count", "returned_count", "rows")})
    return nums


def _to_float(tok: str) -> float:
    return float(tok.replace(",", ""))


def check_citations_resolve(tr: Trace):
    ids = {e["evidence_id"] for e in tr.ledger}
    cited = {f"E{n}" for n in CITE_RE.findall(tr.final_text)}
    dangling = sorted(cited - ids)
    if not cited:
        return (False, "no [E<n>] citations in answer")
    return (not dangling, f"dangling citations: {dangling}" if dangling else f"{len(cited)} citations resolve")


def check_numbers_grounded(tr: Trace, min_value: float = 10, pct_tol: float = 0.6):
    """Every number >= min_value in Observed Facts must be (a) in a sentence with a citation and
    (b) present in the cited Evidence, or a percentage derivable from two numbers in it."""
    facts = split_sections(tr.final_text).get("Observed Facts", tr.final_text)
    ledger = {e["evidence_id"]: e for e in tr.ledger}
    problems, checked = [], 0
    for sent in re.split(r"(?<=[.!?])\s+|\n+", facts):
        cites = [f"E{n}" for n in CITE_RE.findall(sent)]
        clean = LINE_REF_RE.sub("", CITE_RE.sub("", sent))
        for tok, pct in NUM_RE.findall(clean):
            val = _to_float(tok)
            if not pct and val < min_value:
                continue
            if not pct and 1900 <= val <= 2100 and "," not in tok:
                continue  # a year
            checked += 1
            if not cites:
                problems.append(f"uncited number {tok}{pct}: '{sent.strip()[:80]}'")
                continue
            pool = set().union(*(_evidence_numbers(ledger[c]) for c in cites if c in ledger))
            if pct:
                ok = any(b and abs(100 * a / b - val) <= pct_tol for a in pool for b in pool)
            else:
                ok = val in pool
            if not ok:
                problems.append(f"{tok}{pct} not found in {cites}")
    return (not problems, "; ".join(problems[:5]) or f"{checked} numbers grounded")


def check_golden(tr: Trace, golden_values: Iterable[float]):
    """Golden SQL values (run by the harness against SereneDB) must appear in the answer."""
    nums = {_to_float(t) for t, _ in NUM_RE.findall(CITE_RE.sub("", tr.final_text))}
    missing = [v for v in golden_values if float(v) not in nums]
    return (not missing, f"golden values missing from answer: {missing}" if missing else "golden values present")


# ---------- Role / glossary guards ----------
INVENTED_IMPACT = re.compile(
    r"(\$\s?\d|€\s?\d|\b\d[\d,.]*\s?(?:USD|EUR|dollars?|euros?)\b|\bcost (?:us|the company|approximately|about|roughly)\b"
    r"|\brevenue\b|\b(?:hours?|minutes?|days?) of (?:downtime|outage)\b|\bdowntime of\b|\bSLA (?:breach|violation)"
    r"|\b\d[\d,]*\s+(?:customers|users) (?:were |was )?(?:affected|impacted)\b|\bcustomer[- ]facing outage\b)",
    re.I)


def check_no_invented_impact(tr: Trace):
    """Manager guard: no money / downtime / customer-impact claims outside the Unknowns section."""
    secs = split_sections(tr.final_text)
    body = tr.final_text.replace(secs.get("Unknowns", ""), "") if secs.get("Unknowns") else tr.final_text
    hits = [m.group(0) for m in INVENTED_IMPACT.finditer(body)]
    return (not hits, f"invented impact phrases: {hits}" if hits else "no invented impact")


def check_states_unknown(tr: Trace, topic_re: str):
    """Adversarial: the Unknowns section must say the logs cannot establish <topic>."""
    unk = split_sections(tr.final_text).get("Unknowns", "")
    ok = bool(re.search(topic_re, unk, re.I))
    return (ok, "Unknowns names the topic" if ok else f"Unknowns does not mention /{topic_re}/")


INCIDENT_COUNT = re.compile(r"\b\d[\d,]*\s+(?:separate\s+|distinct\s+)?incidents?\b", re.I)


def check_no_incident_counts(tr: Trace):
    """Glossary guard: never present a count of Log Entries as a count of incidents."""
    hits = INCIDENT_COUNT.findall(tr.final_text)
    return (not hits, f"incident counts stated: {hits}" if hits else "no 'N incidents' claims")


def check_mentions(tr: Trace, pattern: str):
    ok = bool(re.search(pattern, tr.final_text, re.I))
    return (ok, f"/{pattern}/ {'found' if ok else 'missing'}")
