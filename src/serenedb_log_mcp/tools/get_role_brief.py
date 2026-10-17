"""`get_role_brief`: the Role playbook as a tool, for clients that do not show MCP prompts (#4).

MCP prompts are user-controlled, the model cannot invoke them, and the Claude Code desktop app does
not list them, so the model fetches the same playbook here. It runs no query and returns no
Evidence, so it uses `role_tool` and writes nothing to the Evidence Ledger.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from serenedb_log_mcp.evidence import Role, RoleParam
from serenedb_log_mcp.roles import NEXT_STEP_KIND, answer_frame, not_measured
from serenedb_log_mcp.roles.briefs import TITLES, role_brief
from serenedb_log_mcp.toolkit import role_tool


class RoleBrief(BaseModel):
    """The playbook of one Role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role = Field(description="The Role this playbook is for.")
    title: str = Field(description="The Role's name as written in answers, for example 'Team Lead'.")
    prompt: str = Field(
        description="The slash command of the MCP prompt that carries the same playbook, for example "
        "'/mcp__serenedb-log__manager'."
    )
    brief: str = Field(
        description="The playbook: the questions this Role asks, how to work and with which tools, the "
        "Evidence it may use, what it avoids, and the four answer sections."
    )
    answer_frame: list[str] = Field(
        description="The four answer sections for this Role, in order, each as '<Section>: <what goes in "
        "it>'; every Evidence result for this Role carries the same list."
    )
    next_step: str = Field(description="The kind of Next Step this Role gets, for example 'a check'.")
    not_measured: list[str] = Field(
        description="What this Role must not claim, because these logs do not measure it (cost, downtime, "
        "customer impact, SLA, root cause); empty except for the Manager."
    )


def register(server: MCPServer) -> None:
    """Register `get_role_brief` on `server`."""
    slash = f"/mcp__{server.name}__"

    @role_tool(server, title="Get the playbook of a Role", output_model=RoleBrief)
    def get_role_brief(role: RoleParam) -> RoleBrief:
        """The playbook of one Role: its questions, tools, allowed Evidence, limits and answer sections.

        Purpose: read it once before you answer as a Role, unless that Role's MCP prompt (engineer,
        team_lead or manager) is already in the conversation; the text is the same. The Roles differ
        in what they ask and receive: the Engineer investigates one failure with raw Log Entries, the
        Team Lead looks at recurring problems with counts and trends, and the Manager prioritises from
        measured frequency and affected Components, with no raw Log Entries and never cost, downtime
        or customer impact. It runs no query and adds nothing to the Evidence Ledger.
        Inputs: role (engineer, team_lead or manager).
        Outputs: role, title, prompt (the slash command of the same playbook), brief (the playbook
        text), answer_frame (the four answer sections for this Role), next_step (the Role's kind of
        Next Step) and not_measured (what the Manager must not claim; empty for the other Roles).
        """
        return RoleBrief(
            role=role,
            title=TITLES[role],
            prompt=f"{slash}{role.value}",
            brief=role_brief(role),
            answer_frame=answer_frame(role),
            next_step=NEXT_STEP_KIND[role],
            not_measured=not_measured(role),
        )
