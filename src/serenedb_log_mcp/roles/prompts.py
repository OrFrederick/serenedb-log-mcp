"""The MCP prompts engineer, team_lead and manager: the Role playbooks as slash commands (#4).

In Claude Code a prompt is `/mcp__serenedb-log__<role> [arguments]`, and its text goes into the
conversation as the user's message. Claude Code splits prompt arguments on whitespace, so a question
cannot be an argument: every argument is one optional token that scopes the investigation, and the
prompt asks for the question when none was given.

The prompt text is the Role's playbook (`briefs.role_brief`, the same text `get_role_brief`
returns) after a short opening in the user's voice.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.prompts.base import Prompt
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import Field

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES
from serenedb_log_mcp.roles.briefs import EXAMPLE_QUESTIONS, TITLES, role_brief

BOTH = "both"
PERIOD_RE = re.compile(r"^(?:last)?(\d+)(min|h|d|w)$")
MAX_FOCUS_CHARS = 200

DatasetArgument = Annotated[
    str,
    Field(
        description="Optional, one token: bgl, openstack or both. Leave it out to let the question "
        "decide which Datasets to use."
    ),
]
PeriodArgument = Annotated[
    str,
    Field(
        description="Optional, one token: how far back from each Dataset's last Log Entry, such as 30d, "
        "2w, 24h or 90min. Leave it out for the whole Dataset."
    ),
]
FocusArgument = Annotated[
    str,
    Field(
        description="Optional, one token to start from: a BGL node such as R27-M0-NA-C:J11-U11, an alert "
        "category such as KERNMNTF, a Component, an OpenStack request or instance id, or an entry_id."
    ),
]

DESCRIPTIONS = {
    Role.ENGINEER: "Answer as the Engineer: investigate one failure, what failed, where, and what happened "
    "just before and after, with the key Log Entries and one concrete check as the Next Step.",
    Role.TEAM_LEAD: "Answer as the Team Lead: recurring problems over a period with exact counts, trends "
    "and the Components and Teams involved, with an investigation priority as the Next Step.",
    Role.MANAGER: "Answer as the Manager: which observed problems deserve attention first, from measured "
    "frequency and affected Components only, never cost, downtime or customer impact, with a "
    "prioritised action as the Next Step.",
}


def _engineer(dataset: DatasetArgument = "", focus: FocusArgument = "") -> str:
    return render(Role.ENGINEER, dataset=dataset, focus=focus)


def _team_lead(dataset: DatasetArgument = "", period: PeriodArgument = "") -> str:
    return render(Role.TEAM_LEAD, dataset=dataset, period=period)


def _manager(dataset: DatasetArgument = "", period: PeriodArgument = "") -> str:
    return render(Role.MANAGER, dataset=dataset, period=period)


PROMPT_FUNCTIONS: dict[Role, Callable[..., str]] = {
    Role.ENGINEER: _engineer,
    Role.TEAM_LEAD: _team_lead,
    Role.MANAGER: _manager,
}


def register_prompts(server: MCPServer, *, only: Role | None = None) -> list[str]:
    """Register the Role prompts on `server` (only the pinned Role's, when `only` is set)."""
    names: list[str] = []
    for role, function in PROMPT_FUNCTIONS.items():
        if only is not None and role is not only:
            continue
        server.add_prompt(
            Prompt.from_function(
                function, name=role.value, title=f"{TITLES[role]} playbook", description=DESCRIPTIONS[role]
            )
        )
        names.append(role.value)
    return names


def render(role: Role, *, dataset: str = "", period: str = "", focus: str = "") -> str:
    """The prompt text: an opening in the user's voice, the scope from the arguments, the playbook."""
    first, second = EXAMPLE_QUESTIONS[role]
    lines = [
        f"I am the {TITLES[role]}. Answer my questions in this Role for the rest of this conversation, "
        f"with role='{role.value}' in every tool call, following the playbook below.",
        *_scope_lines(dataset, period, focus),
        "If I have not asked a question yet, ask me what I want to know and offer examples such as "
        f'"{first}" or "{second}"',
        "",
        role_brief(role),
    ]
    return "\n".join(lines)


def _scope_lines(dataset: str, period: str, focus: str) -> list[str]:
    lines: list[str] = []
    if chosen := _dataset(dataset):
        lines.append(
            "Use both Datasets of the Base Corpus (omit dataset where a tool allows it)."
            if chosen == BOTH
            else f"Use dataset='{chosen}'."
        )
    if window := _period(period):
        lines.append(
            f"Use the period time_from='last {window}', which ends at each Dataset's last Log Entry, "
            "not today."
        )
    if start := _focus(focus):
        lines.append(f"Start from {start}.")
    return lines


def _dataset(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned and cleaned not in {*DATASET_NAMES, BOTH}:
        names = ", ".join(DATASET_NAMES)
        raise _bad_argument(f"dataset={value!r} is not a Dataset. Pass {names} or both, or leave it out.")
    return cleaned


def _period(value: str) -> str:
    cleaned = value.strip().lower()
    if not cleaned:
        return ""
    match = PERIOD_RE.fullmatch(cleaned.replace(" ", ""))
    if match is None or int(match.group(1)) < 1:
        raise _bad_argument(
            f"period={value!r} is not a period. Pass one token such as 30d, 2w, 24h or 90min (a number of at "
            "least 1 and the unit min, h, d or w), or leave it out."
        )
    return f"{int(match.group(1))}{match.group(2)}"


def _focus(value: str) -> str:
    cleaned = value.strip()
    if any(character.isspace() for character in cleaned) or len(cleaned) > MAX_FOCUS_CHARS:
        raise _bad_argument(
            f"focus={value!r} must be one token of at most {MAX_FOCUS_CHARS} characters, such as a node, an "
            "alert category, a Component or an entry_id; put the question itself in your next message."
        )
    return cleaned


def _bad_argument(message: str) -> MCPError:
    """An invalid-params error: its message reaches the client, unlike a plain exception's."""
    return MCPError(INVALID_PARAMS, message)
