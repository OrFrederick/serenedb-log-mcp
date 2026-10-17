"""`get_evidence`: fetch a stored Evidence record again, shaped for the Role."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from serenedb_log_mcp.evidence import Evidence, Role, RoleParam
from serenedb_log_mcp.ledger import get_ledger
from serenedb_log_mcp.toolkit import apply_role_hook, role_tool

EvidenceId = Annotated[
    str,
    Field(
        description="The id of a stored Evidence record, as it appears in a result or a citation, "
        "for example 'E3'.",
        min_length=1,
        max_length=16,
    ),
]


def register(server: MCPServer) -> None:
    """Register `get_evidence` on `server`."""

    @role_tool(server, title="Get stored Evidence by id", output_model=Evidence)
    def get_evidence(role: RoleParam, evidence_id: EvidenceId) -> Evidence:
        """Fetch one Evidence record from the Evidence Ledger again, by its id.

        Purpose: re-read the query, Data Scope, counts and rows behind an `[E<n>]` citation,
        including Evidence from an earlier session when LEDGER_PATH is set, without running the
        query again. Use it to check a number before you state it, or to pick up an investigation
        another Role started.
        Inputs: role (who is asking), evidence_id (for example 'E3').
        Outputs: the Evidence envelope that was stored, shaped for the Role: evidence_id, tool,
        role, sql, params, data_scope, total_matches, returned, truncated, query_ms, columns, rows,
        summary, notes, answer_frame and not_measured.
        """
        wanted = _normalise(evidence_id)
        ledger = get_ledger()
        record = ledger.get(wanted)
        if record is None:
            raise ToolError(_not_found(wanted, ledger.last_id))
        try:
            stored = Evidence.model_validate(record)
        except ValidationError as exc:
            raise ToolError(
                f"{wanted} is in the Evidence Ledger but does not validate against this server's "
                f"Evidence contract ({exc.error_count()} problems: {exc.errors()[0]['msg']}). It "
                "was probably written by another version of this server. Run the query again "
                "instead of citing it."
            ) from exc
        return apply_role_hook(_handover_note(stored, role), role)


def _handover_note(stored: Evidence, role: Role) -> Evidence:
    """Say so when one Role picks up Evidence another Role produced (differentiator 6)."""
    if stored.role is role:
        return stored
    note = (
        f"This Evidence was produced for role {stored.role.value!r} and is returned to role "
        f"{role.value!r}; it is shaped for {role.value!r}."
    )
    return stored.model_copy(update={"notes": [*stored.notes, note]})


def _normalise(evidence_id: str) -> str:
    """Accept the id the way an agent writes it: 'E3', 'e3', '[E3]' or '3'."""
    cleaned = evidence_id.strip().strip("[]").strip().upper()
    return f"E{cleaned}" if cleaned.isdigit() else cleaned


def _not_found(wanted: str, last_id: str | None) -> str:
    if last_id is None:
        return (
            f"The Evidence Ledger is empty, so {wanted} does not exist yet. Call a search or an "
            "aggregation tool first; every result carries the evidence_id to cite and to fetch."
        )
    return (
        f"No Evidence with id {wanted}. This Evidence Ledger holds E1 to {last_id}. Cite an id "
        "from a result of this session, or from the ledger of an earlier one."
    )
