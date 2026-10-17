"""The Evidence Ledger: E1, E2, ... in call order, shared across sessions through LEDGER_PATH."""

import fcntl
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from serenedb_log_mcp.config import load_settings, set_settings
from serenedb_log_mcp.ledger import Ledger, LedgerError, get_ledger, reset_ledger


def store(ledger: Ledger, **fields: Any) -> dict[str, Any]:
    """Append one record, letting the ledger assign the evidence id."""
    with ledger.appending() as slot:
        record = {"evidence_id": slot.evidence_id, "tool": "count_by", **fields}
        slot.store(record)
    return record


def test_ids_are_assigned_in_call_order() -> None:
    ledger = Ledger()

    ids = [store(ledger)["evidence_id"] for _ in range(3)]

    assert ids == ["E1", "E2", "E3"]
    assert [record["evidence_id"] for record in ledger.all()] == ids


def test_the_record_is_stored_exactly_as_handed_over() -> None:
    # Regression for the #3 pilot bug: the ledger was written before count_by attached
    # groups_total, so a true "31,468 nodes" looked ungrounded. The id is reserved first and the
    # record is written last, so whatever the tool finished building is what the ledger holds.
    ledger = Ledger()

    with ledger.appending() as slot:
        record = {"evidence_id": slot.evidence_id, "tool": "count_by", "total_matches": 3}
        record["summary"] = {"groups_total": 31_468, "entries_total": 348_460}
        slot.store(record)

    assert ledger.get("E1") == record
    assert ledger.get("E1")["summary"]["groups_total"] == 31_468


def test_an_unstored_slot_consumes_no_id() -> None:
    ledger = Ledger()
    store(ledger)

    with pytest.raises(LedgerError, match="stored"), ledger.appending():
        pass

    assert store(ledger)["evidence_id"] == "E2"


def test_a_failed_tool_call_consumes_no_id() -> None:
    ledger = Ledger()

    with pytest.raises(ZeroDivisionError), ledger.appending():
        raise ZeroDivisionError("the query failed after the id was reserved")

    assert store(ledger)["evidence_id"] == "E1"


def test_a_slot_rejects_a_record_with_another_id() -> None:
    ledger = Ledger()

    with pytest.raises(LedgerError, match="E1"), ledger.appending() as slot:
        slot.store({"evidence_id": "E7", "tool": "count_by"})


def test_an_unknown_id_is_not_found() -> None:
    ledger = Ledger()
    store(ledger)

    assert ledger.get("E2") is None
    assert ledger.last_id == "E1"
    assert Ledger().last_id is None


def test_without_a_ledger_path_the_ledger_stays_in_the_process(tmp_path: Path) -> None:
    ledger = Ledger()
    record = store(ledger)

    assert ledger.path is None
    assert ledger.all() == [record]
    assert list(tmp_path.iterdir()) == []


def test_with_a_ledger_path_every_record_is_one_json_line(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)

    store(ledger, total_matches=3)
    store(ledger, total_matches=7)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["evidence_id"] for line in lines] == ["E1", "E2"]
    assert json.loads(lines[1])["total_matches"] == 7


def test_a_later_session_continues_the_numbering_and_reads_the_earlier_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    store(Ledger(path), total_matches=3)

    later = Ledger(path)

    assert later.loaded == 1
    assert later.last_id == "E1"
    assert later.get("E1")["total_matches"] == 3
    assert store(later)["evidence_id"] == "E2"


def test_the_last_id_is_re_read_under_the_lock_so_parallel_servers_do_not_collide(
    tmp_path: Path,
) -> None:
    # Two Ledger instances stand in for two server processes on one LEDGER_PATH: neither may
    # hand out an id from its own cached state.
    path = tmp_path / "ledger.jsonl"
    first, second = Ledger(path), Ledger(path)

    ids = [
        store(first)["evidence_id"],
        store(second)["evidence_id"],
        store(first)["evidence_id"],
        store(second)["evidence_id"],
    ]

    assert ids == ["E1", "E2", "E3", "E4"]
    assert [record["evidence_id"] for record in first.all()] == ids
    assert second.get("E3") is not None


def test_a_file_that_is_not_a_ledger_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "not-a-ledger.jsonl"
    path.write_text("this is not JSON\n", encoding="utf-8")

    with pytest.raises(LedgerError, match=r"not-a-ledger\.jsonl"):
        Ledger(path)


def test_a_record_without_an_evidence_id_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps({"tool": "count_by"}) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="evidence_id"):
        Ledger(path)


def half_written(tmp_path: Path) -> Path:
    """A ledger whose last append was cut off, as a killed process leaves it."""
    path = tmp_path / "ledger.jsonl"
    complete = json.dumps({"evidence_id": "E1", "tool": "count_by"}) + "\n"
    torn = json.dumps({"evidence_id": "E2", "tool": "search_logs"})[:24]
    path.write_text(complete + torn, encoding="utf-8")
    return path


def test_a_torn_last_line_from_a_killed_process_is_ignored_when_reading(tmp_path: Path) -> None:
    # A process killed mid-append leaves an unterminated last line. Every read and append used to
    # raise LedgerError from then on, so the ledger was unusable until someone edited the file.
    path = half_written(tmp_path)

    ledger = Ledger(path)

    assert ledger.loaded == 1
    assert [record["evidence_id"] for record in ledger.all()] == ["E1"]
    assert ledger.last_id == "E1"
    assert ledger.get("E1") is not None


def test_the_next_append_recovers_a_torn_last_line_for_good(tmp_path: Path) -> None:
    path = half_written(tmp_path)
    ledger = Ledger(path)

    stored = store(ledger, tool="trend")

    assert stored["evidence_id"] == "E2"
    # The fragment is gone, so the next read does not trip over it as a terminated broken line.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["evidence_id"] for line in lines] == ["E1", "E2"]
    assert [record["evidence_id"] for record in Ledger(path).all()] == ["E1", "E2"]


def test_a_torn_line_that_is_not_the_last_still_fails_loudly(tmp_path: Path) -> None:
    # Only an unterminated final line is a torn append; a complete line that is not a record means
    # LEDGER_PATH points at the wrong file, and that must still be loud.
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        '{"evidence_id": "E1", "tool": "count_by"}\n{"evidence_id": "E2", "to\n',
        encoding="utf-8",
    )

    with pytest.raises(LedgerError, match="is not JSON"):
        Ledger(path)


def test_a_whole_last_record_without_a_trailing_newline_is_kept(tmp_path: Path) -> None:
    # A missing final newline alone does not make the last line a torn append. Ledgers this server
    # did not write often have that shape: a fixture built with "\n".join(...), a hand-edited file,
    # or a converted pilot ledger. Dropping E2 here would let an answer that cites [E2] resolve to
    # whatever the next append stores under that id, with no error anywhere.
    path = tmp_path / "ledger.jsonl"
    records = [
        {"evidence_id": "E1", "tool": "count_by", "total_matches": 101},
        {"evidence_id": "E2", "tool": "count_by", "total_matches": 102},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    ledger = Ledger(path)

    assert ledger.loaded == 2
    assert ledger.get("E2") == records[1]
    assert ledger.last_id == "E2"

    stored = store(ledger, tool="search_logs", total_matches=7)

    assert stored["evidence_id"] == "E3"
    # E2 still names the count_by Evidence it named before the append, and the file is readable.
    assert [record["evidence_id"] for record in Ledger(path).all()] == ["E1", "E2", "E3"]
    assert Ledger(path).get("E2") == records[1]


def test_an_unterminated_last_line_that_is_json_but_not_a_record_is_dropped(
    tmp_path: Path,
) -> None:
    # The boundary case on the other side: the fragment parses, so "did it parse" is not the test.
    # It has no evidence_id, so no reader could ever cite it, and it is dropped like any fragment.
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        json.dumps({"evidence_id": "E1", "tool": "count_by"}) + "\n" + '{"tool": "count_by"}',
        encoding="utf-8",
    )

    ledger = Ledger(path)

    assert ledger.loaded == 1
    assert store(ledger, tool="trend")["evidence_id"] == "E2"
    assert [record["evidence_id"] for record in Ledger(path).all()] == ["E1", "E2"]


def test_a_last_record_torn_inside_a_string_is_dropped(tmp_path: Path) -> None:
    # The common shape of a real torn append: the kill landed inside the sql field, so the line is
    # neither valid JSON nor a record, even though it starts with a valid evidence_id.
    path = tmp_path / "ledger.jsonl"
    whole = json.dumps({"evidence_id": "E2", "tool": "count_by", "sql": "SELECT 1 FROM bgl"})
    torn = whole[: whole.index("FROM")]
    path.write_text(
        json.dumps({"evidence_id": "E1", "tool": "count_by"}) + "\n" + torn,
        encoding="utf-8",
    )

    ledger = Ledger(path)

    assert ledger.loaded == 1
    assert store(ledger, tool="trend")["evidence_id"] == "E2"
    assert [record["evidence_id"] for record in Ledger(path).all()] == ["E1", "E2"]


def test_blank_lines_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps({"evidence_id": "E1", "tool": "trend"}) + "\n\n", encoding="utf-8")

    assert Ledger(path).loaded == 1


def test_the_process_ledger_follows_the_configured_ledger_path(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    set_settings(load_settings({}, ledger_path=path))
    reset_ledger()

    store(get_ledger())

    assert get_ledger() is get_ledger()
    assert get_ledger().path == path
    assert path.exists()

    set_settings(load_settings({}))

    assert get_ledger().path is None


def test_an_append_waits_for_a_lock_another_process_holds(tmp_path: Path) -> None:
    # flock is held per open file, so a second handle stands in for a second server process.
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    finished = threading.Event()

    def append() -> None:
        store(ledger)
        finished.set()

    with path.open("a+", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        waiting = threading.Thread(target=append, daemon=True)
        waiting.start()

        assert not finished.wait(0.3), "the append did not wait for the lock"

        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

    waiting.join(timeout=5)
    assert finished.is_set()
    assert ledger.get("E1") is not None


@pytest.mark.parametrize("on_disk", [False, True])
def test_parallel_tool_calls_in_one_process_get_unique_gap_free_ids(tmp_path: Path, on_disk: bool) -> None:
    # The MCP SDK runs synchronous tools in worker threads, so two calls can reach the ledger at
    # the same time inside one server process.
    ledger = Ledger(tmp_path / "ledger.jsonl" if on_disk else None)
    threads = 8
    per_thread = 25
    switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-7)  # make a lost update likely rather than rare
    try:
        workers = [
            threading.Thread(target=lambda: [store(ledger) for _ in range(per_thread)])
            for _ in range(threads)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
    finally:
        sys.setswitchinterval(switch_interval)

    ids = [record["evidence_id"] for record in ledger.all()]
    assert len(ids) == threads * per_thread
    assert sorted(ids, key=lambda id_: int(id_[1:])) == [f"E{n}" for n in range(1, len(ids) + 1)]
