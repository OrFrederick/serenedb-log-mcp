"""The Evidence Ledger: every Evidence record, addressable by its id.

Ids are `E1`, `E2`, ... in call order. Without `LEDGER_PATH` the ledger lives in the server process
only. With `LEDGER_PATH` it is an append-only JSONL file: it is loaded at startup, the numbering
continues where the file ends, and every append happens under an advisory lock
(`fcntl.flock`) with the last id re-read inside the lock. Several server processes and several
sessions can therefore share one ledger, which is what the eval harness (one ledger per trial),
`get_evidence`, `verify_answer`, `export_investigation` and `scripts/replay_ledger.py` rely on.

`appending()` reserves the id first and writes the record last, so a record is only ever stored
complete. That is the fix for the #3 pilot bug, where `count_by` wrote the ledger before it had
attached `groups_total` and a true number then looked ungrounded.

The lock has two halves: a `threading.Lock` for the tool calls the SDK runs in parallel worker
threads inside one server process, and the advisory file lock for other server processes.

A process killed during an append leaves an unterminated last line. Reads ignore that fragment and
the next append cuts it off, so one interrupted write does not make the ledger unreadable for ever.
A last line that holds a whole record and only lacks its newline is not a fragment: it is read and
kept, because dropping it would let the next append reuse its id. Any other unparseable line is
still a `LedgerError`: such a file is not an Evidence Ledger this server wrote, or it was damaged
after it was written.

Records are separated by line endings alone, and the file is never split with `str.splitlines()`,
which also breaks at U+2028, U+2029, U+0085 and five ASCII control characters that a Log Entry or a
SQL string may legitimately contain (see `_split`).

The file is read and split as bytes, and each line is decoded on its own (#54). A tear can land
inside a multi-byte UTF-8 character, which `ensure_ascii=False` puts in the file, and the offset the
next append truncates at must count the bytes of every CR and multi-byte character before it.

`fcntl` is POSIX-only, which covers the supported platforms (macOS for development, Linux in CI).
"""

from __future__ import annotations

import fcntl
import json
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from serenedb_log_mcp.config import get_settings
from serenedb_log_mcp.evidence import EVIDENCE_ID_PATTERN

LedgerRecord = dict[str, Any]

_ID_RE = re.compile(EVIDENCE_ID_PATTERN)

_NOT_A_LEDGER_LINE = (
    "Every line of an Evidence Ledger is one complete record, so this file was either not written "
    "by this server, or damaged after it was written."
)


class LedgerError(RuntimeError):
    """The Evidence Ledger could not be read or written."""


class LedgerSlot:
    """A reserved evidence id. Build the record with it, then `store` it exactly once."""

    def __init__(self, evidence_id: str, write: Callable[[LedgerRecord], None]) -> None:
        self.evidence_id = evidence_id
        self.stored = False
        self._write = write

    def store(self, record: Mapping[str, Any]) -> None:
        """Append the finished record; its `evidence_id` must be the reserved one."""
        if self.stored:
            raise LedgerError(f"{self.evidence_id} was already stored")
        if record.get("evidence_id") != self.evidence_id:
            raise LedgerError(
                f"the record carries evidence_id {record.get('evidence_id')!r}, but this slot "
                f"reserved {self.evidence_id!r}"
            )
        self._write(dict(record))
        self.stored = True


class Ledger:
    """The Evidence Ledger of one investigation, in memory or at `path`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._records: list[LedgerRecord] = []
        # Load at startup, so a broken LEDGER_PATH fails now and not in the middle of an answer.
        self.loaded = len(self._read()) if path is not None and path.exists() else 0

    @contextmanager
    def appending(self) -> Iterator[LedgerSlot]:
        """Reserve the next evidence id, then store the finished record under it.

        Nothing is written and no id is consumed when the body raises, so the ids of the stored
        Evidence stay gap-free.
        """
        with self._lock:
            if self.path is None:
                slot = LedgerSlot(_next_id(self._records), self._records.append)
                yield slot
                _require_stored(slot)
                return

            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Mode "a+b" creates the file and appends every write at the end, whatever we read first.
            with self.path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    data = handle.read()
                    written = self._parse(data)
                    _drop_a_torn_tail(handle, data)

                    def write(record: LedgerRecord) -> None:
                        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
                        handle.write(line.encode("utf-8"))
                        handle.flush()

                    slot = LedgerSlot(_next_id(written), write)
                    yield slot
                    _require_stored(slot)
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def all(self) -> list[LedgerRecord]:
        """Every stored record in call order, read from the file when there is one."""
        return self._read() if self.path is not None else list(self._records)

    def get(self, evidence_id: str) -> LedgerRecord | None:
        """One stored record by its id, or None when this ledger does not hold it."""
        return next((r for r in self.all() if r.get("evidence_id") == evidence_id), None)

    @property
    def last_id(self) -> str | None:
        """The id of the most recent record, or None while the ledger is empty."""
        records = self.all()
        return str(records[-1]["evidence_id"]) if records else None

    def _read(self) -> list[LedgerRecord]:
        assert self.path is not None
        if not self.path.exists():
            return []
        return self._parse(self.path.read_bytes())

    def _parse(self, data: bytes) -> list[LedgerRecord]:
        records: list[LedgerRecord] = []
        for number, raw in enumerate(_complete_lines(data), start=1):
            # The path can be exactly right and the file still damaged, so the message names the
            # line that cannot be read and leaves both causes open.
            where = f"{self.path} line {number}"
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LedgerError(f"{where} is not UTF-8 ({exc.reason}). {_NOT_A_LEDGER_LINE}") from exc
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"{where} is not JSON ({exc.msg}). {_NOT_A_LEDGER_LINE}") from exc
            if not isinstance(record, dict) or not _ID_RE.fullmatch(str(record.get("evidence_id"))):
                raise LedgerError(f"{where} has no valid evidence_id. {_NOT_A_LEDGER_LINE}")
            records.append(record)
        return records


def _is_a_record(line: bytes) -> bool:
    """Whether an unterminated last line is a whole record rather than a torn fragment.

    The missing newline alone does not decide it. Ledgers this server did not write can end without
    one and still hold a whole record on that line: a fixture built with `"\\n".join(...)`, a
    hand-edited file, a converted pilot ledger. A torn append, by contrast, leaves bytes that do not
    decode (the tear landed inside a multi-byte character), that do not parse, or that parse but
    carry no `evidence_id` and so can never be cited.
    """
    try:
        record = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(record, dict) and _ID_RE.fullmatch(str(record.get("evidence_id"))) is not None


_LINE_END = re.compile(rb"\r\n|\r|\n")


def _split(data: bytes) -> tuple[list[bytes], bytes]:
    """The ledger file as its terminated lines and the unterminated tail after the last ending.

    It works on bytes, so `len` of everything before the tail is the byte offset the tail starts at,
    whatever CRs and multi-byte characters come before it (#54). A line ends at CRLF, CR or LF, the
    endings that reading in text mode used to translate; `json.dumps` escapes CR and LF, so neither
    can appear inside a record, and in UTF-8 their bytes never occur inside another character.

    `str.splitlines()` must not be used here. It also breaks at U+2028, U+2029, U+0085, `\\x0b`,
    `\\x0c`, `\\x1c`, `\\x1d` and `\\x1e`, and `json.dumps` writes the first three of those raw. One
    Log Entry message or SQL string holding such a character then turned a stored record into
    several apparent lines, which made the file unreadable for every later read and append, and cost
    a whole record its id when it sat on an unterminated last line (#49). Splitting on line endings
    alone keeps one record on one line whatever text it carries, and also recovers ledgers already
    written that way.
    """
    end = max(data.rfind(b"\n"), data.rfind(b"\r")) + 1
    lines = _LINE_END.split(data[:end])
    lines.pop()  # the empty piece after the last ending, or the whole of an empty prefix
    return lines, data[end:]


def _complete_lines(data: bytes) -> list[bytes]:
    """The lines that hold a record, so a torn append does not break every later read.

    A process killed during an append leaves an unterminated last line. That line is dropped only
    when it is a fragment: dropping a whole record instead would let the next append reuse its id,
    and an answer citing it would silently resolve to different Evidence. Any unreadable line that
    is not the last one still fails loudly: such a file is not a ledger this server wrote, or it was
    damaged after it was written.
    """
    lines, tail = _split(data)
    if tail and _is_a_record(tail):
        lines.append(tail)
    return lines


def _drop_a_torn_tail(handle: IO[bytes], data: bytes) -> None:
    """Make the last line safe to append after, under the lock, before the next record is written.

    Appending straight after an unterminated line would turn it into a terminated one. For a torn
    fragment that is fatal, because `_complete_lines` only forgives the last line, so every later
    read would raise again; the fragment is cut off instead, and nothing a reader can use is lost,
    because those bytes are the tail of a record that was never finished. A whole record on that
    line is kept and only terminated, so its id stays taken and its content stays readable. The cut
    is a byte offset into the file as read, so no ending or character before it can shift it.
    """
    _, tail = _split(data)
    if not tail:
        return
    if _is_a_record(tail):
        handle.write(b"\n")
    else:
        handle.truncate(len(data) - len(tail))
    handle.flush()


def _next_id(records: list[LedgerRecord]) -> str:
    numbers = [int(str(record["evidence_id"])[1:]) for record in records]
    return f"E{max(numbers, default=0) + 1}"


def _require_stored(slot: LedgerSlot) -> None:
    if not slot.stored:
        raise LedgerError(f"{slot.evidence_id} was reserved but no record was stored under it")


_active: Ledger | None = None


def get_ledger() -> Ledger:
    """The process-wide ledger for the configured `LEDGER_PATH`."""
    global _active
    path = get_settings().ledger_path
    if _active is None or _active.path != path:
        _active = Ledger(path)
    return _active


def reset_ledger() -> None:
    """Forget the process-wide ledger, so the next call reloads it (tests, and a changed path)."""
    global _active
    _active = None
