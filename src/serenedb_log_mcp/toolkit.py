"""`evidence_tool`: turn a query function into a well-behaved MCP tool.

A tool function runs its SQL and returns a `ToolOutcome` (the database layer's `QueryResult`, the
Data Scope, the column names and the computed summary). Everything that must be true for every
Evidence tool is written once here:

- `role` is required on every tool (#4), and a server started with `--role` rejects every other Role;
- the tool is registered with a title, the read-only annotations and `_meta["anthropic/alwaysLoad"]`,
  so Claude Code loads it without ToolSearch, plus `_meta.ui` when it has an MCP Apps visual;
- the Evidence envelope goes out as `structuredContent`, with the same JSON in one text block;
- the finished envelope is appended to the Evidence Ledger before it is returned, so every cited
  `[E<n>]` exists;
- the Role hook shapes what the asking Role receives, never what the ledger stores (by default the
  Role shaping of `roles.shape_for_role`, T7);
- when a statement read an agent index (T19) instead of a Dataset's full index, the Data Scope
  (`agent_indexes`) and the notes say so, without any change in the tool;
- every exception becomes a `ToolError`, because on an error only `content[0].text` reaches the
  model and in `mcp` 2.x only `ToolError` text survives.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Annotated, Any, Protocol, get_args, get_origin, runtime_checkable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.context_injection import find_context_parameter
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ValidationError

from serenedb_log_mcp.config import get_settings
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex, capture_use
from serenedb_log_mcp.evidence import DataScope, Evidence, Role
from serenedb_log_mcp.ledger import LedgerError, get_ledger
from serenedb_log_mcp.roles import shape_for_role
from serenedb_log_mcp.visuals import ui_meta

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
"""The annotations every Evidence tool carries. Only create_index and drop_index (T19) differ."""

ALWAYS_LOAD_META: dict[str, Any] = {"anthropic/alwaysLoad": True}
"""Without this, Claude Code hides the tools behind ToolSearch."""

ROLE_PARAMETER = "role"

SAMPLE_TRUNCATION_NOTE = (
    "The returned rows are a sample, not a frequency; use count_by or trend for how often something happens."
)
"""Default second half of the truncation note: true for retrieval, wrong for a top-N aggregation."""

RETRY_HINT = (
    "Retry the call once; if it fails again, narrow the Data Scope (a shorter time range or fewer "
    "filters), or call describe_corpus to check the Datasets, Components and levels you filter on."
)

CONTRACT_BUG_HINT = (
    "That is a bug in this server, not in your call, so the same call will fail the same way and "
    "calling it again will not help. Take another route to the number, for example count_by or "
    "trend for how often something happens and search_logs for example Log Entries, and say in "
    "your answer that this tool is broken."
)
"""Advice for a broken Evidence contract, which is deterministic, so a retry is pointless."""

logger = logging.getLogger(__name__)
"""Crashes are logged here with their traceback. In stdio mode logging goes to stderr, never to
stdout, so it cannot corrupt the protocol stream."""


class ToolContractError(Exception):
    """A tool breaks the Evidence contract (at registration, or in what it returned)."""


@runtime_checkable
class QueryResultLike(Protocol):
    """The part of the database layer's `QueryResult` the Evidence contract needs.

    T2 owns `db.QueryResult(total, rows, truncated, query_ms, sql, params, notes)` (plan section
    2.7). It is matched structurally, so the Evidence contract does not import the database layer
    and can be tested without one. `sql` and `params` line up by index, one entry per statement.
    """

    @property
    def total(self) -> int:
        """Exact number of matching units SereneDB counted."""

    @property
    def rows(self) -> Sequence[Sequence[Any]]:
        """The returned rows, each a sequence of values."""

    @property
    def truncated(self) -> bool:
        """The database layer's own truncation flag; the envelope recomputes it from the counts."""

    @property
    def query_ms(self) -> float:
        """Wall-clock time of the statements, in milliseconds."""

    @property
    def sql(self) -> Sequence[str]:
        """The statements that ran, in order."""

    @property
    def params(self) -> Sequence[Sequence[Any]]:
        """The bound parameters, one sequence per statement."""

    @property
    def notes(self) -> Sequence[str]:
        """Notes from the database layer, for example a retry after a restart."""


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool function returns: the query result plus what only the tool knows.

    `result` is one `QueryResult`, so a tool that runs several statements, for example `count_by`
    with a baseline period or `describe_corpus`, passes a combined one: T2 adds
    `QueryResult.combine(*results)`, which concatenates `sql`, `params` and `notes` and sums
    `query_ms`. The envelope then holds every statement that ran, in order, and `sql[i]` still
    lines up with `params[i]`.
    """

    result: QueryResultLike
    scope: DataScope
    columns: Sequence[str] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    notes: Sequence[str] = ()
    rows: Sequence[Sequence[Any]] | None = None
    """Rows to return instead of `result.rows`, for tools that shape or merge rows themselves."""
    total_matches: int | None = None
    """The unit this tool counts, when it is not `result.total`: count_by counts groups, not Log
    Entries, and then carries the entry count in `summary['entries_total']`."""
    truncation_note: str | None = None
    """The note to add when this result is truncated, instead of `SAMPLE_TRUNCATION_NOTE`.

    The default fits retrieval: the returned Log Entries are a sample, so their number is not a
    frequency. It is wrong for a top-N aggregation, where the rows are exact counts and an `(other)`
    row keeps the totals exact, so count_by, list_incidents, top_patterns and latency_stats pass
    their own sentence, for example "Showing the top 25 of 31,468 groups; the (other) row holds the
    rest, so the counts are exact." The note is dropped when nothing was truncated.
    """


RoleHook = Callable[[Evidence, Role], Evidence]
ToolFunction = Callable[..., ToolOutcome]


def identity_role_hook(evidence: Evidence, role: Role) -> Evidence:
    """A Role hook that shapes nothing: tests of the Evidence contract install it for isolation."""
    return evidence


DEFAULT_ROLE_HOOK: RoleHook = shape_for_role
"""The Role shaping (T7): the answer frame, the Manager's not_measured list, no raw Log Entries for
the Manager and at most three samples for the Team Lead (`roles.shaping`)."""

_role_hook: RoleHook = DEFAULT_ROLE_HOOK


def set_role_hook(hook: RoleHook) -> None:
    """Install the Role shaping. It shapes what the asking Role receives, never what is stored."""
    global _role_hook
    _role_hook = hook


def get_role_hook() -> RoleHook:
    """The active Role hook."""
    return _role_hook


def reset_role_hook() -> None:
    """Go back to the default Role hook, the Role shaping."""
    global _role_hook
    _role_hook = DEFAULT_ROLE_HOOK


def apply_role_hook(evidence: Evidence, role: Role) -> Evidence:
    """Shape one Evidence record for the Role that is asking for it.

    `role` is the Role of this call, which is not always `evidence.role`: Evidence is handed
    between Roles by its id, and `get_evidence` must shape a stored record for whoever asks now.

    The shaped record is validated again, because `model_copy(update=...)` skips validation: a hook
    that lowers `returned` and leaves `truncated` alone would otherwise only fail in the SDK's own
    output validation, as a bare `UnexpectedToolError` with no text for the model to act on.
    """
    shaped = _role_hook(evidence, role)
    try:
        return Evidence.model_validate(shaped.model_dump())
    except ValidationError as exc:
        raise ToolContractError(
            f"the Role shaping for role {role.value!r} returned an Evidence that breaks the "
            f"contract: {_detail(exc)}"
        ) from exc


def role_tool(
    server: MCPServer,
    *,
    title: str,
    output_model: type[BaseModel],
    name: str | None = None,
    annotations: ToolAnnotations = READ_ONLY,
) -> Callable[[Callable[..., BaseModel]], Callable[..., CallToolResult]]:
    """Register a Role-aware tool on `server`: the registration, the `--role` pin and ToolError.

    The function takes `role: RoleParam` plus its own parameters, which become the input schema,
    and returns an instance of `output_model`, which becomes the output schema, the
    `structuredContent` and the text block. Its docstring is the tool description the agent reads,
    so it must state the purpose, the inputs and the outputs.

    `evidence_tool` is this plus the Evidence envelope and the ledger; tools that return no
    Evidence, such as `get_evidence` and T7's `get_role_brief`, use it directly.
    """

    def register(function: Callable[..., BaseModel]) -> Callable[..., CallToolResult]:
        signature = _tool_signature(function)
        tool_name = name or function.__name__

        @functools.wraps(function)
        def tool(*args: Any, **kwargs: Any) -> CallToolResult:
            role = _role_of(_bind(signature, tool_name, args, kwargs))
            _reject_other_role(role)
            try:
                payload = function(*args, **kwargs)
            except ToolError:
                raise
            except LedgerError as exc:
                # `exc` already names the line and explains the damage (see `_NOT_A_LEDGER_LINE`);
                # an unwritable path raises `PermissionError`, an `OSError`, which never reaches
                # here, so no hint here may blame LEDGER_PATH (#49 AC3). `rstrip` avoids a second
                # trailing period, since `exc`'s own message already ends in one.
                raise ToolError(
                    f"The Evidence Ledger could not be read or written: {str(exc).rstrip('.')}."
                ) from exc
            except ToolContractError as exc:
                logger.exception("%s broke the Evidence contract", tool_name)
                raise ToolError(f"{_detail(exc)} {CONTRACT_BUG_HINT}") from exc
            except Exception as exc:
                # The SDK logs a ToolError with logger.info and no traceback, so this is the only
                # place the stack trace of an unexpected crash can still be written down.
                logger.exception("%s failed", tool_name)
                raise ToolError(f"{_detail(exc)} {RETRY_HINT}") from exc
            return _as_result(payload)

        tool.__signature__ = signature.replace(  # type: ignore[attr-defined]
            return_annotation=Annotated[CallToolResult, output_model]
        )
        # A tool with an MCP Apps visual also links its `ui://` resource (visuals, T23).
        meta = {**ALWAYS_LOAD_META, **(ui_meta(tool_name) or {})}
        server.add_tool(tool, name=tool_name, title=title, annotations=annotations, meta=meta)
        return tool

    return register


def evidence_tool(
    server: MCPServer,
    *,
    title: str,
    name: str | None = None,
    annotations: ToolAnnotations = READ_ONLY,
) -> Callable[[ToolFunction], Callable[..., CallToolResult]]:
    """Register a query function as an Evidence tool on `server`.

    The function takes `role: RoleParam` plus its own parameters and returns a `ToolOutcome`. The
    wrapper builds the Evidence envelope from it, appends the finished record to the Evidence
    Ledger and returns it shaped for the Role.
    """

    def register(function: ToolFunction) -> Callable[..., CallToolResult]:
        signature = _tool_signature(function)
        tool_name = name or function.__name__
        # The same call the SDK makes to decide which parameter it injects its Context into.
        injected = find_context_parameter(function)

        @functools.wraps(function)
        def produce_evidence(*args: Any, **kwargs: Any) -> Evidence:
            bound = _bind(signature, tool_name, args, kwargs)
            role = _role_of(bound)
            arguments = _recorded_arguments(bound, injected)
            with capture_use() as agent_indexes:
                outcome = function(*args, **kwargs)
            outcome = _with_agent_indexes(outcome, agent_indexes)
            return apply_role_hook(_store(tool_name, role, arguments, outcome), role)

        return role_tool(server, title=title, output_model=Evidence, name=tool_name, annotations=annotations)(
            produce_evidence
        )

    return register


def _with_agent_indexes(outcome: ToolOutcome, used: Sequence[AgentIndex]) -> ToolOutcome:
    """The outcome with the agent indexes its statements read named in the Data Scope and the notes.

    `corpus.datasets.Relation.index` records an agent index when a statement reads it, so a tool
    that only read base tables never claims one, and no tool has to report it itself.
    """
    if not used:
        return outcome
    scope = outcome.scope.model_copy(
        update={"agent_indexes": {index.dataset: index.qualified_name for index in used}}
    )
    return replace(outcome, scope=scope, notes=[*outcome.notes, *(index.routing_note() for index in used)])


def _tool_signature(function: Callable[..., Any]) -> inspect.Signature:
    """The tool's signature, with the contract on `role` and the description enforced."""
    where = getattr(function, "__name__", repr(function))
    try:
        signature = inspect.signature(function, eval_str=True)
    except NameError as exc:
        raise ToolContractError(f"{where}: its annotations cannot be resolved ({exc})") from exc
    if not (function.__doc__ or "").strip():
        raise ToolContractError(
            f"{where} has no docstring. The docstring is the tool description the agent reads; it "
            "must state the purpose, the inputs and the outputs."
        )
    parameter = signature.parameters.get(ROLE_PARAMETER)
    if parameter is None:
        raise ToolContractError(
            f"{where} has no 'role' parameter. Every Evidence tool takes role: RoleParam, because "
            "the Role selects the connection, the defaults and the output shape (#4)."
        )
    if _annotated_type(parameter.annotation) is not Role:
        raise ToolContractError(
            f"{where}: 'role' must be annotated with the Role enum (role: RoleParam), not "
            f"{parameter.annotation!r}."
        )
    if parameter.default is not inspect.Parameter.empty:
        raise ToolContractError(
            f"{where}: 'role' must be required, so it cannot default to {parameter.default!r}."
        )
    return signature


def _annotated_type(annotation: Any) -> Any:
    """The type inside `Annotated[...]`, or the annotation itself."""
    return get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation


def _bind(
    signature: inspect.Signature, tool: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> inspect.BoundArguments:
    try:
        return signature.bind(*args, **kwargs)
    except TypeError as exc:
        raise ToolError(f"{tool} was called with the wrong arguments: {exc}.") from exc


def _role_of(bound: inspect.BoundArguments) -> Role:
    value = bound.arguments[ROLE_PARAMETER]
    try:
        return Role(value)
    except ValueError as exc:
        roles = ", ".join(role.value for role in Role)
        raise ToolError(f"role={value!r} is not a Role. Pass one of: {roles}.") from exc


def _recorded_arguments(bound: inspect.BoundArguments, injected: str | None) -> dict[str, Any]:
    """The call's parameters for the Evidence: what the tool ran with, minus what the caller cannot set.

    The SDK validates the call against the input schema before the tool runs, so a default the
    caller left out is bound too and this is the call as it ran, which is what a replay needs. Two
    parameters are left out because they are not call parameters: `role`, which is a field of its
    own and selects the connection, and the one the SDK injects its `Context` into, which it also
    keeps out of the input schema, so no replay could set it.

    The keys can only be parameters the tool declares, which are search terms, Dataset names, time
    ranges, Pattern ids and limits; credentials live in `DATABASE_URL` and `DATABASE_URL_<ROLE>` in
    the environment and are never tool parameters, so nothing secret can reach this field.
    """
    skipped = {ROLE_PARAMETER, injected}
    return {name: value for name, value in bound.arguments.items() if name not in skipped}


def _reject_other_role(role: Role) -> None:
    pin = get_settings().role
    if pin is not None and role != pin:
        raise ToolError(
            f"This server was started with --role {pin.value}, so it only answers as "
            f"{pin.value!r}, not as {role.value!r}. Call it with role={pin.value!r}, or use a "
            "server registration without a --role pin."
        )


def _store(tool: str, role: Role, arguments: dict[str, Any], outcome: ToolOutcome) -> Evidence:
    """Build the envelope and append it to the Evidence Ledger, complete, under its reserved id."""
    source = outcome.result
    rows = [list(row) for row in (source.rows if outcome.rows is None else outcome.rows)]
    columns = list(outcome.columns)
    _check_columns(tool, columns, rows)
    returned = len(rows)
    total = source.total if outcome.total_matches is None else outcome.total_matches
    # The truncation note comes first: the Role shaping replaces it when it lowers the row count
    # (roles.shaping._replace_rows), because it counts the stored rows.
    notes = [
        *_truncation_notes(returned, total, outcome.truncation_note),
        *source.notes,
        *outcome.notes,
    ]
    with get_ledger().appending() as slot:
        evidence = Evidence(
            evidence_id=slot.evidence_id,
            tool=tool,
            role=role,
            arguments=arguments,
            sql=[_as_it_ran(statement) for statement in source.sql],
            params=[list(values) for values in source.params],
            data_scope=outcome.scope,
            total_matches=total,
            returned=returned,
            # One place decides truncation, so the envelope can never contradict its own counts.
            truncated=returned < total,
            query_ms=round(source.query_ms, 1),
            columns=columns,
            rows=rows,
            summary=dict(outcome.summary),
            notes=notes,
        )
        slot.store(evidence.model_dump(mode="json"))
    return evidence


def _as_it_ran(statement: str) -> str:
    """One stored statement: the string that ran, minus the template's own indentation.

    Nothing else is rewritten. `" ".join(statement.split())` used to collapse every run of
    whitespace, which turned a `--` comment line into a comment over the rest of the statement and
    rewrote string literals containing runs of whitespace. The `sql` field promises the statements
    that actually ran, and T13's `replay_ledger.py` re-runs exactly these strings. Compacting SQL
    for display belongs in T13's renderer, not here.
    """
    return textwrap.dedent(statement).strip()


def _check_columns(tool: str, columns: list[str], rows: list[list[Any]]) -> None:
    widths = {len(row) for row in rows}
    if widths and widths != {len(columns)}:
        named = ", ".join(columns) or "none"
        raise ToolContractError(
            f"{tool} returned rows with {sorted(widths)} values for {len(columns)} columns "
            f"({named}); every row must line up with the columns."
        )


def _truncation_notes(returned: int, total: int, own_note: str | None) -> list[str]:
    """The truncation note, if any: the tool's own sentence, or the retrieval default."""
    if returned >= total:
        return []
    if own_note is not None:
        return [own_note]
    return [f"Truncated: {returned} of {total:,} matches returned. {SAMPLE_TRUNCATION_NOTE}"]


VALIDATION_DETAILS = 2
"""How many field errors of a `ValidationError` go into the message the model reads."""


def _detail(exc: Exception) -> str:
    """One sentence that says what went wrong, ending in a full stop."""
    text = _validation_detail(exc) if isinstance(exc, ValidationError) else _first_line(exc)
    return text if text.endswith((".", "!", "?")) else f"{text}."


SELF_DESCRIBING_ERRORS = (ToolContractError, LedgerError, ToolError)
"""Errors this server raises with a sentence written for the model; their type adds nothing."""


def _first_line(exc: Exception) -> str:
    """The first line of the message, named by its exception type unless it names itself.

    `str(KeyError("entries_total"))` is `"'entries_total'"`, so the detail the model read for a
    missing summary key was `'entries_total'.`, which does not say what kind of failure it was.
    Naming the type makes it `KeyError: 'entries_total'`. The errors this server raises itself
    already explain themselves, so those keep their own wording.
    """
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if not text:
        return type(exc).__name__
    return text if isinstance(exc, SELF_DESCRIBING_ERRORS) else f"{type(exc).__name__}: {text}"


def _validation_detail(exc: ValidationError) -> str:
    """The failing fields and reasons of a `ValidationError`.

    `str(exc)` starts with "2 validation errors for DataScope.", so the first line alone says
    nothing about which field was wrong or why. The fields come from the server's own code, not
    from the caller, so naming them is safe.
    """
    errors = exc.errors()
    named = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in errors[:VALIDATION_DETAILS]
    )
    more = f" and {len(errors) - VALIDATION_DETAILS} more" if len(errors) > VALIDATION_DETAILS else ""
    return f"{exc.title} is invalid: {named}{more}."


def _as_result(payload: BaseModel) -> CallToolResult:
    """The payload as structuredContent, mirrored in one text block for other clients."""
    data = payload.model_dump(mode="json")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
        structured_content=data,
    )
