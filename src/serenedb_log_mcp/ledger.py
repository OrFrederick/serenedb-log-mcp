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

A process killed during an append leaves an unterminated last line. Reads ignore it, and the next
append cuts it off, so one interrupted write does not make the ledger unreadable for ever. Any
other unparseable line is still a `LedgerError`, because that means `LEDGER_PATH` points at the
wrong file.

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
            # Mode "a+" creates the file and appends every write at the end, whatever we read first.
            with self.path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    text = handle.read()
                    written = self._parse(text)
                    _drop_a_torn_tail(handle, text)

                    def write(record: LedgerRecord) -> None:
                        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
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
        return self._parse(self.path.read_text(encoding="utf-8"))

    def _parse(self, text: str) -> list[LedgerRecord]:
        records: list[LedgerRecord] = []
        for number, line in enumerate(_complete_lines(text), start=1):
            if not line.strip():
                continue
            where = f"{self.path}:{number}"
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(
                    f"{where} is not JSON ({exc.msg}). LEDGER_PATH must point at an Evidence "
                    "Ledger written by this server, or at a path that does not exist yet."
                ) from exc
            if not isinstance(record, dict) or not _ID_RE.fullmatch(str(record.get("evidence_id"))):
                raise LedgerError(
                    f"{where} has no valid evidence_id. LEDGER_PATH must point at an Evidence "
                    "Ledger written by this server, or at a path that does not exist yet."
                )
            records.append(record)
        return records


def _complete_lines(text: str) -> list[str]:
    """The lines that were written whole, so a torn append does not break every later read.

    A process killed during an append leaves an unterminated last line. Every record this module
    writes ends in a newline, so a file that does not end in one ends in a fragment, and a fragment
    is not a record. Any other unparseable line still fails loudly: that means `LEDGER_PATH` points
    at the wrong file.
    """
    lines = text.splitlines()
    return lines[:-1] if lines and not text.endswith("\n") else lines


def _drop_a_torn_tail(handle: IO[str], text: str) -> None:
    """Cut a torn last line off the file, under the lock, before the next record is appended.

    Writing the next record after the fragment would turn the fragment into a terminated line, and
    `_complete_lines` only forgives the last one, so every later read would raise again. Nothing a
    reader can use is lost: the bytes removed are the tail of a record that was never finished.
    """
    if not text or text.endswith("\n"):
        return
    whole = text[: text.rindex("\n") + 1] if "\n" in text else ""
    handle.truncate(len(whole.encode("utf-8")))
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
