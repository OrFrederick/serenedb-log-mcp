"""Shared shaping for the retrieval tools `search_logs` and `get_context` (T5).

Both return Log Entries as rows. What a row shows is decided once here:

- `ENTRY_COLUMNS`: the canonical columns a row carries per Dataset, chosen so an entry can be
  handed on: `entry_id` to get_context, `template_id` to count_by(pattern_ids=...), the node,
  request or instance to a narrower search;
- `shape_message`: the message capped at `MESSAGE_CHARS` characters (plan section 2.3), with the
  words a full-text query matched marked `**like this**` by SereneDB's `ts_highlight`, and the cut
  placed so the first match stays in view.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Any

from psycopg import sql

from serenedb_log_mcp.corpus.datasets import Relation

__all__ = [
    "ELLIPSIS",
    "ENTRY_COLUMNS",
    "HIGHLIGHT_OPTIONS",
    "MARK",
    "MESSAGE_CHARS",
    "entry_column",
    "entry_values",
    "shape_message",
]

MESSAGE_CHARS = 240
"""Longest message a row carries, marks and ellipses included."""
MARK = "**"
"""The marker ts_highlight puts on both sides of a matched word."""
ELLIPSIS = "…"
MARK_BY = 180
"""A first match that starts after this many characters moves the window of a long message."""
MARK_LEAD = 60
"""Characters of context the moved window shows before the first match."""
HIGHLIGHT_OPTIONS = "StartSel=**, StopSel=**, HighlightAll=true"
"""ts_highlight options: Markdown bold marks around the matched words of the whole message. The
default passage selection (MaxWords) trimmed messages that fit the cap ('… (spawning). Skip.' came
back as '**Skip**' on 26.10.1), so `shape_message` picks the window itself. The options must be a
constant, so they are a literal in the SQL, never a parameter."""

ENTRY_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "bgl": ("entry_id", "ts", "node", "component", "level", "alert_tag", "template_id"),
        "openstack": ("entry_id", "ts", "component", "level", "request_id", "instance_ref", "template_id"),
    }
)
"""The canonical columns of a retrieval row before its message, per Dataset."""


def entry_column(rel: Relation, concept: str, alias: str = "e") -> sql.Composable:
    """`<alias>."<column>"` for a concept or canonical column of the Dataset (the alias is ours)."""
    return sql.SQL("{}.{}").format(sql.SQL(alias), rel.column(concept))


def entry_values(values: Sequence[Any]) -> list[Any]:
    """The ENTRY_COLUMNS values of a row as JSON carries them: timestamps as ISO-8601."""
    return [value.isoformat() if isinstance(value, datetime) else value for value in values]


def shape_message(message: str | None, highlighted: str | None = None) -> str | None:
    """The message for a row: the highlighted message when there is one, capped at MESSAGE_CHARS.

    `highlighted` is the whole message with its matches marked (ts_highlight with HighlightAll).
    """
    if message is None:
        return None
    return _cap(highlighted or message)


def _cap(text: str) -> str:
    """`text` cut to at most MESSAGE_CHARS characters, keeping the first mark in view.

    A long message is cut at the end with an ellipsis. When its first match starts after MARK_BY
    characters, the window starts MARK_LEAD characters before the match instead (or later, so the
    end of the message fits), with a leading ellipsis. A mark cut in half is dropped.
    """
    if len(text) <= MESSAGE_CHARS:
        return text
    first = text.find(MARK)
    start = 0
    if first > MARK_BY:
        start = min(first - MARK_LEAD, len(text) - (MESSAGE_CHARS - len(ELLIPSIS)))
    head = ELLIPSIS if start > 0 else ""
    rest = text[start:]
    if len(head) + len(rest) <= MESSAGE_CHARS:
        return head + rest
    cut = rest[: MESSAGE_CHARS - len(head) - len(ELLIPSIS)]
    if cut.endswith("*") and not cut.endswith(MARK):
        cut = cut[:-1]  # half of a mark
    if cut.count(MARK) % 2:  # the cut ends inside a marked word: drop its opening mark
        at = cut.rfind(MARK)
        cut = cut[:at] + cut[at + len(MARK) :]
    return head + cut + ELLIPSIS
