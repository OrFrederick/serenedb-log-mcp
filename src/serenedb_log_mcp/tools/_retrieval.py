"""Shared shaping for the retrieval tools `search_logs` and `get_context` (T5).

Both return Log Entries as rows. What a row shows is decided once here:

- `ENTRY_COLUMNS`: the canonical columns a row carries per Dataset, chosen so an entry can be
  handed on: `entry_id` to get_context, `template_id` to count_by(pattern_ids=...), the node,
  request or instance to a narrower search;
- `shape_message`: the message capped at `MESSAGE_CHARS` characters (plan section 2.3), with the
  words a full-text query matched marked `**like this**` by SereneDB's `ts_highlight`.
"""

from __future__ import annotations

import re
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
_WORD_RE = re.compile(r"\w")
HIGHLIGHT_OPTIONS = "StartSel=**, StopSel=**, MaxWords=40"
"""ts_highlight options: Markdown bold marks, and a passage of at most 40 words around the matches
(the options must be a constant, so they are a literal in the SQL, never a parameter)."""

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
    """The message for a row: the highlighted passage when there is one, capped at MESSAGE_CHARS.

    ts_highlight returns the best passage of at most 40 words around the matches, so for a long
    message it can leave out the start or the end; an ellipsis then marks the side that is missing.
    It also leaves out punctuation at the edges (a leading '[', a trailing '.', measured on
    26.10.1), which is put back as it was, because nothing else is missing there.
    """
    if message is None:
        return None
    if not highlighted:
        return _cap(message)
    plain = highlighted.replace(MARK, "")
    at = message.find(plain) if plain else -1
    if at < 0:
        return _cap(highlighted)
    head, tail = message[:at], message[at + len(plain) :]
    before = head if not _WORD_RE.search(head) else ELLIPSIS
    after = tail if not _WORD_RE.search(tail) else ELLIPSIS
    return _cap(f"{before}{highlighted}{after}")


def _cap(text: str) -> str:
    """`text` cut to MESSAGE_CHARS with a trailing ellipsis, never leaving a mark open."""
    if len(text) <= MESSAGE_CHARS:
        return text
    cut = text[: MESSAGE_CHARS - len(ELLIPSIS)]
    if cut.endswith("*") and not cut.endswith(MARK):
        cut = cut[:-1]  # half of a mark
    if cut.count(MARK) % 2:  # the cut ends inside a marked word: drop its opening mark
        at = cut.rfind(MARK)
        cut = cut[:at] + cut[at + len(MARK) :]
    return cut + ELLIPSIS
