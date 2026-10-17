"""`verify_answer`: check a draft answer against the Evidence Ledger before it is sent (T12).

Differentiator 3: the agent checks its own draft, and every number and `[E<n>]` citation is checked
against the Evidence that was actually produced. The checks live in `verify.checks`, which the eval's
L2 grader (T17) reuses, so the tool and the grader can never disagree.

It is registered with `role_tool`, not `evidence_tool`: it runs no query and produces no Evidence, so
it writes nothing to the Evidence Ledger. Verifying a draft therefore cannot change the ids the
draft cites, and calling it again after a fix checks the same ledger. It reads the whole ledger on
every call, so Evidence from earlier sessions on the same `LEDGER_PATH`, and Evidence another server
process appended since this one started, resolves too.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from serenedb_log_mcp.evidence import Role, RoleParam
from serenedb_log_mcp.ledger import get_ledger
from serenedb_log_mcp.toolkit import role_tool
from serenedb_log_mcp.verify import INCIDENT_TOOLS, AnswerReport, CheckedNumber, Problem, check_answer

DraftParam = Annotated[
    str,
    Field(
        description="The full draft answer in Markdown, exactly as you would send it: the four sections "
        "Observed Facts, Hypotheses, Unknowns and Next Step, with [E<n>] next to every number."
    ),
]


class VerifyResult(BaseModel):
    """What verify_answer found in one draft answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = Field(description="True when no problem was found: send the draft as your answer.")
    role: Role = Field(description="The Role the draft was checked for.")
    summary: str = Field(description="One line: the verdict, the problem counts by kind and what to do next.")
    problems: list[Problem] = Field(
        description="Every problem found, in answer order: kind, section, the offending text and a fix hint."
    )
    checked_numbers: list[CheckedNumber] = Field(
        description="Every number that was checked: as written, its value, section, cited Evidence ids, "
        "status and the Evidence ids that hold it."
    )
    cited_evidence: list[str] = Field(description="The Evidence ids the draft cites, in order of first use.")
    notes: list[str] = Field(description="Checks that were skipped, and why.")


def register(server: MCPServer) -> None:
    """Register `verify_answer` on `server`."""

    @role_tool(server, title="Check a draft answer against the Evidence", output_model=VerifyResult)
    def verify_answer(role: RoleParam, draft: DraftParam) -> VerifyResult:
        """Check a draft answer against the Evidence Ledger. Call it before every final answer.

        Purpose: call it with your full draft before every final answer, fix every problem it reports,
        and call it again until ok is true. It checks that every [E<n>] exists in the Evidence Ledger
        (this session and earlier ones on the same LEDGER_PATH); that every number appears exactly in
        the Evidence its sentence cites (rows, summary, totals, parameters), never rounded, bracketed
        as a range or bound, or added up by hand, unless the sentence is marked "(derived from [E1],[E2])"
        and the number recomputes from those records; that the sections Observed Facts, Hypotheses,
        Unknowns and Next Step are all there, in that order; and that a count called Incidents cites
        list_incidents or top_patterns Evidence. For team_lead and manager, Log Entry counts must stand
        next to Incident and Spread Evidence (once list_incidents or top_patterns is offered). For
        manager, no raw-line Evidence (search_logs, get_context) may be cited, cost, money, downtime,
        customer and SLA terms belong in Unknowns or in a sentence saying the logs cannot establish them,
        and Unknowns must name cost, downtime and customer impact. It runs no query and adds nothing to
        the Evidence Ledger.
        Inputs: role (the Role the answer is for), draft (the full answer text in Markdown).
        Outputs: ok (true when there is nothing to fix), role, summary (the verdict in one line),
        problems (each with kind, section, the offending text and a fix hint), checked_numbers (every
        number checked, with its status and the Evidence ids that hold it), cited_evidence and notes
        (checks that were skipped, and why).
        """
        report = check_answer(role, draft, get_ledger().all(), incident_tools=_offers_incident_tools(server))
        return VerifyResult(role=role, summary=_summary(report), **report.model_dump())


def _offers_incident_tools(server: MCPServer) -> bool:
    """Whether `server` offers list_incidents or top_patterns (T27, T28), looked up at call time.

    MCPServer has no public synchronous lookup, so this asks its tool manager. Should that ever
    change, the side-by-side check applies, as the acceptance criteria state it, rather than going
    silent.
    """
    manager = getattr(server, "_tool_manager", None)
    get_tool = getattr(manager, "get_tool", None)
    if get_tool is None:
        return True
    return any(get_tool(name) is not None for name in INCIDENT_TOOLS)


def _summary(report: AnswerReport) -> str:
    if report.ok:
        numbers = len(report.checked_numbers)
        cited = ", ".join(report.cited_evidence) or "no Evidence"
        return f"OK: {numbers} numbers checked against {cited}. Send the draft as your answer."
    counts = Counter(problem.kind for problem in report.problems)
    kinds = ", ".join(f"{n} {kind}" for kind, n in counts.most_common())
    total = len(report.problems)
    return (
        f"{total} problem{'s' if total != 1 else ''} ({kinds}). Fix every problem as its hint says, "
        "then call verify_answer again with the corrected draft."
    )
