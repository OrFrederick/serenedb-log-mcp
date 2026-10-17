"""`export_investigation`: the investigation of a session as a Markdown report (T13, Differentiator 8).

It is registered with `role_tool`, not `evidence_tool`: it runs no query and produces no Evidence, so
it adds nothing to the Evidence Ledger, and it writes no file (plan section 6, assumption 6). It
returns the Markdown in the result; `scripts/export_report.py` writes the same Markdown to disk, and
the worked investigation (T18) is exported that way with `role='engineer'`.

`role` is the audience of the report, not the Role of the answers: each answer names its own Role.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from serenedb_log_mcp.evidence import EVIDENCE_ID_PATTERN, Role
from serenedb_log_mcp.export import Answer, render_investigation
from serenedb_log_mcp.ledger import get_ledger
from serenedb_log_mcp.toolkit import role_tool
from serenedb_log_mcp.verify import INCIDENT_TOOLS

SPILL_CHARS = 50_000
"""Claude Code writes a tool result above this many characters to a file instead of showing it."""

AudienceParam = Annotated[
    Role,
    Field(
        description="The audience of the report: 'manager' gets no raw Log Entry rows in any table "
        "(counts, summaries, SQL and Data Scopes stay); 'engineer' and 'team_lead' get tables with the "
        "first 10 rows of each result. Export the worked investigation with role='engineer'."
    ),
]
TitleParam = Annotated[str, Field(min_length=1, max_length=200, description="The report title.")]
AnswersParam = Annotated[
    list[Answer],
    Field(
        min_length=1,
        max_length=20,
        description="The answers of the investigation in the order it was handed on, each with role (the "
        "Role that asked), question and answer (the final answer exactly as sent, with its [E<n>] "
        "citations).",
    ),
]
EvidenceIdsParam = Annotated[
    list[Annotated[str, Field(pattern=EVIDENCE_ID_PATTERN)]] | None,
    Field(
        description="Optional: only these Evidence ids, for example ['E1', 'E2']; Evidence an answer "
        "cites is always included. Omit it to include every record of the Evidence Ledger.",
        max_length=500,
    ),
]


class InvestigationExport(BaseModel):
    """The rendered report of one investigation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role = Field(description="The audience the report was rendered for.")
    title: str = Field(description="The report title.")
    markdown: str = Field(description="The report in Markdown.")
    size_chars: int = Field(description="Length of markdown in characters.")
    size_bytes: int = Field(description="Length of markdown in UTF-8 bytes.")
    evidence_ids: list[str] = Field(
        description="The Evidence ids shown in full in the report, in ledger order."
    )
    notes: list[str] = Field(
        description="What to know about the report: cited or requested Evidence ids the ledger does not "
        "hold, and a hint when the report is too long to read in a tool result."
    )


def register(server: MCPServer) -> None:
    """Register `export_investigation` on `server`."""

    @role_tool(
        server, title="Export the investigation as a Markdown report", output_model=InvestigationExport
    )
    def export_investigation(
        role: AudienceParam,
        title: TitleParam,
        answers: AnswersParam,
        evidence_ids: EvidenceIdsParam = None,
    ) -> InvestigationExport:
        """Render the investigation as a Markdown report: questions, tool calls, SQL, results and answers.

        Purpose: turn the final answers and the Evidence Ledger behind them into the reproducible report
        of an investigation. Per answering Role it shows the question, every tool call with its
        parameters and Data Scope, the SQL with its bound parameters, the result tables (totals,
        truncated, first 10 rows), the answer with its verify_answer check, and a closing section "What
        the data does and does not establish" (Observed Facts and Data Scopes, then Hypotheses, Unknowns,
        samples and what these logs do not measure). Call it once every answer is final. It is
        read-only: it returns the Markdown and writes no file; scripts/export_report.py writes the same
        report to disk, and scripts/replay_ledger.py re-runs its SQL.
        Inputs: role (the audience of the report, not of the answers: 'manager' leaves raw Log Entry
        rows out of every table while counts, SQL and Data Scope stay; 'engineer' and 'team_lead' get
        the first 10 rows; export the worked investigation with role='engineer'), title, answers (each
        with role, question and the final answer as sent), evidence_ids (optional subset of the ledger).
        Outputs: role, title, markdown (the report), size_chars and size_bytes (its size), evidence_ids
        (the Evidence shown in full) and notes (cited Evidence the ledger does not hold, size hints).
        """
        report = render_investigation(
            role,
            title,
            answers,
            get_ledger().all(),
            evidence_ids=evidence_ids,
            incident_tools=_offers_incident_tools(server),
        )
        notes = list(report.notes)
        if len(report.markdown) > SPILL_CHARS:
            notes.append(
                f"The report has {len(report.markdown):,} characters, more than a tool result shows in "
                "full; write it to a file with scripts/export_report.py instead of quoting it here."
            )
        return InvestigationExport(
            role=role,
            title=title,
            markdown=report.markdown,
            size_chars=len(report.markdown),
            size_bytes=len(report.markdown.encode("utf-8")),
            evidence_ids=report.evidence_ids,
            notes=notes,
        )


def _offers_incident_tools(server: MCPServer) -> bool:
    """Whether `server` offers list_incidents or top_patterns, so the answers are checked as
    verify_answer checks them on this server (the same lookup as `verify_answer`'s)."""
    get_tool = getattr(getattr(server, "_tool_manager", None), "get_tool", None)
    if get_tool is None:
        return True
    return any(get_tool(name) is not None for name in INCIDENT_TOOLS)
