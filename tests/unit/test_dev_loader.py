"""scripts/dev_loader.py: parsing, sample files and argument handling (no SereneDB needed).

The log lines below are synthetic, written in the BGL and OpenStack formats of Loghub
(https://github.com/logpai/loghub; Zhu et al., ISSRE 2023).
"""

import hashlib
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg import sql

import dev_loader

BGL_ALERT = (
    "KERNDTLB 1118536327 2005.06.11 R30-M0-N9-C:J16-U01 2005-06-11-17.32.07.581048 "
    "R30-M0-N9-C:J16-U01 RAS KERNEL FATAL data TLB error interrupt"
)
BGL_PLAIN = (
    "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.363779 "
    "R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected"
)
OS_API = (
    "nova-api.log.1.2017-05-16_13:53:08 2017-05-16 00:00:00.008 25746 INFO nova.osapi_compute.wsgi.server "
    "[req-38101a0b-2096-447d-96ea-a692162415ae 113d3a99c3da401fbd62cc2caa5b96d2 "
    '54fadb412c4e40cdbaed9335e4c35a9e - - -] 10.11.10.1 "GET /v2/54fa/servers/detail HTTP/1.1" '
    "status: 200 len: 1893 time: 0.2477829"
)
OS_INSTANCE = (
    "nova-compute.log.1.2017-05-16_13:55:31 2017-05-16 00:00:10.302 2931 INFO nova.virt.libvirt.driver [-] "
    "[instance: b9000564-fe1a-409b-b8cc-1e88b294cd1d] Instance spawned successfully."
)


def test_header_says_it_is_not_the_organisers_loader() -> None:
    assert dev_loader.__doc__ is not None
    assert "not the organisers' loader" in dev_loader.__doc__


def test_bgl_alert_line() -> None:
    row = dev_loader.parse_bgl_line(7, BGL_ALERT)

    assert row == (
        7,
        "KERNDTLB",
        1118536327,
        datetime(2005, 6, 11, 17, 32, 7, 581048),
        "R30-M0-N9-C:J16-U01",
        "KERNEL",
        "FATAL",
        "data TLB error interrupt",
        BGL_ALERT,
    )


def test_bgl_non_alert_line_keeps_dash_tag() -> None:
    row = dev_loader.parse_bgl_line(1, BGL_PLAIN)

    assert row[1] == "-"
    assert row[7] == "instruction cache parity error corrected"


def test_bgl_short_line_does_not_fail() -> None:
    row = dev_loader.parse_bgl_line(3, "- notanepoch 2005.06.03")

    assert row == (3, "-", None, None, None, None, None, None, "- notanepoch 2005.06.03")


def test_bgl_bad_timestamp_gives_null_ts() -> None:
    line = BGL_PLAIN.replace("2005-06-03-15.42.50.363779", "2005-06-03-xx")

    assert dev_loader.parse_bgl_line(1, line)[3] is None


def test_nul_bytes_are_escaped_because_copy_rejects_them() -> None:
    row = dev_loader.parse_bgl_line(1, BGL_PLAIN + "\x00tail")

    assert "\x00" not in row[8]
    assert row[8].endswith("\\x00tail")


def test_openstack_api_line() -> None:
    row = dev_loader.parse_openstack_line(5, "openstack_normal1.log", OS_API)

    assert row[:9] == (
        5,
        "openstack_normal1.log",
        "nova-api.log.1.2017-05-16_13:53:08",
        datetime(2017, 5, 16, 0, 0, 0, 8000),
        25746,
        "INFO",
        "nova.osapi_compute.wsgi.server",
        "req-38101a0b-2096-447d-96ea-a692162415ae",
        None,
    )
    assert row[9].startswith('10.11.10.1 "GET /v2/54fa/servers/detail')
    assert row[10] == OS_API


def test_openstack_instance_line_without_request() -> None:
    row = dev_loader.parse_openstack_line(9, "OpenStack_2k.log", OS_INSTANCE)

    assert row[7] is None
    assert row[8] == "b9000564-fe1a-409b-b8cc-1e88b294cd1d"
    assert row[9] == "[instance: b9000564-fe1a-409b-b8cc-1e88b294cd1d] Instance spawned successfully."


def test_openstack_unparsed_line_keeps_raw_text_only() -> None:
    # message stays NULL: the raw line (with its timestamp and pid prefix) is only in raw.
    row = dev_loader.parse_openstack_line(2, "x.log", "garbage")

    assert row == (2, "x.log", None, None, None, None, None, None, None, None, "garbage")


def test_openstack_line_with_an_empty_message_still_parses() -> None:
    line = (
        "nova-compute.log.1.2017-05-16_13:55:31 2017-05-16 03:19:45.356 2931 ERROR oslo_service.periodic_task"
    )

    row = dev_loader.parse_openstack_line(4, "openstack_normal1.log", line)

    assert row == (
        4,
        "openstack_normal1.log",
        "nova-compute.log.1.2017-05-16_13:55:31",
        datetime(2017, 5, 16, 3, 19, 45, 356000),
        2931,
        "ERROR",
        "oslo_service.periodic_task",
        None,
        None,
        None,
        line,
    )


BGL_NO_NODE_REPEAT = (
    "- 1119415930 2005.06.21 - 2005-06-21-21.52.10.214285 RAS KERNEL FATAL "
    "Kill job 20251 timed out. Block freed."
)
BGL_GARBLED_NODE_REPEAT = (
    "- 1133447861 2005.12.01 - 2005-12-01-06.37.41.417709 time for a single instance of a correctable ddr. "
    "RAS KERNEL INFO 0 microseconds spent in the rbs signal handler during 0 calls."
)


def test_bgl_line_without_the_node_repeat_field_is_re_split() -> None:
    row = dev_loader.parse_bgl_line(5, BGL_NO_NODE_REPEAT)

    assert row == (
        5,
        "-",
        1119415930,
        datetime(2005, 6, 21, 21, 52, 10, 214285),
        "-",
        "KERNEL",
        "FATAL",
        "Kill job 20251 timed out. Block freed.",
        BGL_NO_NODE_REPEAT,
    )


def test_bgl_line_with_text_in_the_node_repeat_field_is_re_split() -> None:
    row = dev_loader.parse_bgl_line(6, BGL_GARBLED_NODE_REPEAT)

    assert row[5:8] == ("KERNEL", "INFO", "0 microseconds spent in the rbs signal handler during 0 calls.")


def test_bgl_null_event_type_is_accepted() -> None:
    line = BGL_PLAIN.replace(" RAS KERNEL INFO ", " NULL DISCOVERY WARNING ")

    assert dev_loader.parse_bgl_line(1, line)[5:7] == ("DISCOVERY", "WARNING")


def test_bgl_line_that_ends_at_the_level_has_no_message() -> None:
    line = BGL_PLAIN.split(" instruction")[0]

    row = dev_loader.parse_bgl_line(1, line)

    assert row[5:8] == ("KERNEL", "INFO", None)


def test_bgl_unknown_severity_is_never_loaded_as_a_level() -> None:
    line = BGL_PLAIN.replace(" RAS KERNEL INFO ", " RAS KERNEL BOGUS ")

    row = dev_loader.parse_bgl_line(1, line)

    assert row[5:8] == (None, None, None)
    assert row[8] == line


def test_parse_stats_count_what_needs_a_second_look() -> None:
    stats = dev_loader.ParseStats()
    rows = [
        dev_loader.parse_bgl_line(1, BGL_PLAIN),
        dev_loader.parse_bgl_line(2, BGL_PLAIN.split(" instruction")[0]),
        dev_loader.parse_bgl_line(3, BGL_PLAIN.replace(" RAS KERNEL INFO ", " RAS KERNEL BOGUS ")),
        dev_loader.parse_bgl_line(4, "- x"),
        dev_loader.parse_bgl_line(5, BGL_PLAIN + "\x00"),
    ]

    assert list(stats.track("bgl", rows)) == rows
    assert (stats.rows, stats.unparsed, stats.null_ts, stats.null_message, stats.nul_rewritten) == (
        5,
        2,
        1,
        3,
        1,
    )
    assert stats.summary() == "unparsed 2, NULL ts 1, NULL message 3, unknown level 0, NUL rewritten 1"


def test_parse_stats_flag_unknown_openstack_levels() -> None:
    stats = dev_loader.ParseStats()
    rows = [dev_loader.parse_openstack_line(1, "a.log", OS_API.replace(" INFO ", " NOTICE "))]

    list(stats.track("openstack", rows))

    assert stats.unknown_level == 1


def test_openstack_rows_number_lines_across_files(tmp_path: Path) -> None:
    first, second = tmp_path / "a.log", tmp_path / "b.log"
    first.write_text(OS_API + "\n" + OS_INSTANCE + "\n")
    second.write_text(OS_INSTANCE)

    rows = list(dev_loader.openstack_rows([first, second]))

    assert [(r[0], r[1]) for r in rows] == [(1, "a.log"), (2, "a.log"), (3, "b.log")]


def test_lh2_rows_read_the_structured_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "x.csv"
    csv_path.write_text('LineId,Content,EventId,EventTemplate\n1,"a ""b"" c",E1,"a <*> c"\n')

    assert list(dev_loader.lh2_rows(csv_path)) == [(1, 'a "b" c', "E1", "a <*> c")]


def test_lh2_empty_file_is_a_loader_error(tmp_path: Path) -> None:
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("")

    with pytest.raises(dev_loader.LoaderError, match=r"empty\.csv.*empty"):
        list(dev_loader.lh2_rows(csv_path))


def test_lh2_unexpected_header_is_a_loader_error(tmp_path: Path) -> None:
    csv_path = tmp_path / "other.csv"
    csv_path.write_text("LineId,Date,Time,Content\n1,2005,12:00,x\n")

    with pytest.raises(dev_loader.LoaderError, match=r"other\.csv.*LineId,Content,EventId,EventTemplate"):
        list(dev_loader.lh2_rows(csv_path))


@pytest.mark.parametrize("bad_row", ["2,only two", "x,a,E1,t", "3,a,E1,t,extra"])
def test_lh2_malformed_row_is_a_loader_error_naming_the_line(tmp_path: Path, bad_row: str) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(f"LineId,Content,EventId,EventTemplate\n1,a,E1,t\n{bad_row}\n")

    with pytest.raises(dev_loader.LoaderError, match=r"bad\.csv line 3"):
        list(dev_loader.lh2_rows(csv_path))


def _sample(content: bytes) -> dev_loader.SampleFile:
    return dev_loader.SampleFile(
        "X_2k.log", "https://example.invalid/X_2k.log", hashlib.sha256(content).hexdigest()
    )


def test_ensure_samples_downloads_missing_files_once(tmp_path: Path) -> None:
    sample = _sample(b"line\n")
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return b"line\n"

    paths = dev_loader.ensure_samples(tmp_path, [sample], fetch)
    dev_loader.ensure_samples(tmp_path, [sample], fetch)

    assert paths == [tmp_path / "X_2k.log"]
    assert paths[0].read_bytes() == b"line\n"
    assert calls == ["https://example.invalid/X_2k.log"]
    assert "Zhu" in (tmp_path / "LOGHUB_LICENSE.txt").read_text()


def test_ensure_samples_rejects_a_checksum_mismatch(tmp_path: Path) -> None:
    sample = _sample(b"expected\n")

    with pytest.raises(dev_loader.LoaderError, match="sha256"):
        dev_loader.ensure_samples(tmp_path, [sample], lambda url: b"tampered\n")

    assert not (tmp_path / "X_2k.log").exists()


def test_pinned_samples_are_the_loghub_2k_files() -> None:
    names = [s.name for s in dev_loader.SAMPLES]

    assert names == ["BGL_2k.log", "OpenStack_2k.log"]
    assert all("/logpai/loghub/" in s.url and len(s.sha256) == 64 for s in dev_loader.SAMPLES)


def test_full_mode_reports_missing_files_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "host=127.0.0.1 port=9")

    code = dev_loader.main(["bgl"])

    err = capsys.readouterr().err
    assert code == 2
    assert str(tmp_path / "raw" / "BGL.log") in err
    assert "SLM_DATA_DIR" in err


def test_sample_mode_rejects_table_names(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        dev_loader.main(["--sample", "lh2_bgl"])

    assert exit_info.value.code == 2
    assert "--sample" in capsys.readouterr().err


def test_unknown_table_is_rejected() -> None:
    with pytest.raises(SystemExit) as exit_info:
        dev_loader.main(["users"])

    assert exit_info.value.code == 2


def test_schema_must_be_a_plain_identifier(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        dev_loader.main(["--schema", "public; drop table bgl"])

    assert exit_info.value.code == 2
    assert "--schema" in capsys.readouterr().err


def test_ensure_samples_replaces_an_existing_file_with_the_wrong_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A download interrupted halfway leaves the right name and the wrong bytes, and was trusted.
    sample = _sample(b"expected\n")
    (tmp_path / "X_2k.log").write_bytes(b"trunca")
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return b"expected\n"

    paths = dev_loader.ensure_samples(tmp_path, [sample], fetch)

    assert paths[0].read_bytes() == b"expected\n"
    assert calls == [sample.url]
    assert "wrong sha256" in capsys.readouterr().err


def test_ensure_samples_does_not_refetch_a_file_that_matches(tmp_path: Path) -> None:
    sample = _sample(b"expected\n")
    (tmp_path / "X_2k.log").write_bytes(b"expected\n")

    def fetch(url: str) -> bytes:
        raise AssertionError("an existing file with the right sha256 must not be downloaded again")

    assert dev_loader.ensure_samples(tmp_path, [sample], fetch) == [tmp_path / "X_2k.log"]


def test_ensure_samples_leaves_no_part_file_behind(tmp_path: Path) -> None:
    sample = _sample(b"expected\n")

    dev_loader.ensure_samples(tmp_path, [sample], lambda url: b"expected\n")

    assert [p.name for p in sorted(tmp_path.iterdir())] == ["LOGHUB_LICENSE.txt", "X_2k.log"]


def test_ensure_samples_writes_the_download_through_a_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real name must never carry unverified bytes, so the download lands in X_2k.log.part and
    # is moved into place with os.replace, which is atomic within one filesystem.
    sample = _sample(b"expected\n")
    moves: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: Any, dst: Any) -> None:
        moves.append((Path(src).name, Path(dst).name))
        real_replace(src, dst)

    monkeypatch.setattr(dev_loader.os, "replace", spy)

    dev_loader.ensure_samples(tmp_path, [sample], lambda url: b"expected\n")

    assert moves == [("X_2k.log.part", "X_2k.log")]


def test_ensure_samples_keeps_an_existing_file_when_the_download_is_rejected(tmp_path: Path) -> None:
    sample = _sample(b"expected\n")
    (tmp_path / "X_2k.log").write_bytes(b"older\n")

    with pytest.raises(dev_loader.LoaderError, match="sha256"):
        dev_loader.ensure_samples(tmp_path, [sample], lambda url: b"tampered\n")

    assert (tmp_path / "X_2k.log").read_bytes() == b"older\n"
    assert not (tmp_path / "X_2k.log.part").exists()


class FakeCopy:
    """Records every row written through it, as psycopg's copy object would receive them."""

    def __init__(self, events: list[tuple[str, object]]) -> None:
        self.events = events

    def __enter__(self) -> "FakeCopy":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write_row(self, row: tuple[Any, ...]) -> None:
        self.events.append(("write", row[0]))


class FakeCursor:
    def __init__(self, events: list[tuple[str, object]]) -> None:
        self.events = events

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def copy(self, statement: object) -> FakeCopy:
        self.events.append(("copy", None))
        return FakeCopy(self.events)


class FakeConnection:
    def __init__(self, events: list[tuple[str, object]]) -> None:
        self.events = events

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.events)

    def commit(self) -> None:
        self.events.append(("commit", None))


def test_copy_rows_writes_each_row_as_it_is_parsed() -> None:
    # No Python buffer: parsing row n+1 must happen after row n has gone to the server, otherwise
    # the loader's memory grows with the batch size (a few hundred MB on BGL).
    events: list[tuple[str, object]] = []

    def rows() -> Iterator[tuple[int]]:
        for i in range(4):
            events.append(("parse", i))
            yield (i,)

    count = dev_loader.copy_rows(
        FakeConnection(events),  # type: ignore[arg-type]
        sql.Identifier("s", "t"),
        ["line_no"],
        rows(),
    )

    assert count == 4
    assert events == [
        ("copy", None),
        ("parse", 0),
        ("write", 0),
        ("parse", 1),
        ("write", 1),
        ("parse", 2),
        ("write", 2),
        ("parse", 3),
        ("write", 3),
        ("commit", None),
    ]


def test_copy_rows_of_an_empty_source_still_opens_one_copy() -> None:
    events: list[tuple[str, object]] = []

    count = dev_loader.copy_rows(
        FakeConnection(events),  # type: ignore[arg-type]
        sql.Identifier("s", "t"),
        ["line_no"],
        iter(()),
    )

    assert count == 0
    assert events == [("copy", None), ("commit", None)]
