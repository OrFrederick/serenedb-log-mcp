"""scripts/fetch_data.py: download, verify and extract the Base Corpus and Loghub-2.0 archives.

No test here touches the network: a fake download copies prepared archives, and the real-data test
only checks file sizes in an existing SLM_DATA_DIR, one Zenodo record at a time.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

import dev_loader
import fetch_data
from fetch_data import Archive, FetchError, Member
from serenedb_log_mcp.config import load_settings


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def tar_gz_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def spec_for(name: str, payload: bytes, members: dict[str, tuple[bytes, str]]) -> Archive:
    return Archive(
        record="123",
        name=name,
        size=len(payload),
        md5=hashlib.md5(payload).hexdigest(),
        members=tuple(Member(path, len(data), dest) for path, (data, dest) in members.items()),
    )


class FakeDownload:
    """Serves prepared archive bytes by URL and counts the calls."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def __call__(self, url: str, target: Path) -> None:
        self.calls.append(url)
        target.write_bytes(self.payloads[url])


LOG = b"line one\nline two\n"
CSV = b"LineId,Content\n1,x\n"


@pytest.fixture
def zip_case() -> tuple[Archive, FakeDownload]:
    payload = zip_bytes({"BGL/BGL_full.log": LOG, "BGL/BGL_full.log_structured.csv": CSV})
    spec = spec_for(
        "BGL.zip",
        payload,
        {
            "BGL/BGL_full.log": (LOG, "lh2/BGL/BGL_full.log"),
            "BGL/BGL_full.log_structured.csv": (CSV, "lh2/BGL/BGL_full.log_structured.csv"),
        },
    )
    return spec, FakeDownload({spec.url: payload})


def test_first_run_downloads_and_extracts_every_member(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case

    fetch_data.fetch([spec], tmp_path, download)

    assert download.calls == [spec.url]
    assert (tmp_path / "lh2/BGL/BGL_full.log").read_bytes() == LOG
    assert (tmp_path / "lh2/BGL/BGL_full.log_structured.csv").read_bytes() == CSV


def test_second_run_downloads_nothing(tmp_path: Path, zip_case: tuple[Archive, FakeDownload]) -> None:
    spec, download = zip_case
    fetch_data.fetch([spec], tmp_path, download)

    fetch_data.fetch([spec], tmp_path, download)

    assert download.calls == [spec.url]


def test_present_members_need_no_archive_at_all(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case
    fetch_data.fetch([spec], tmp_path, download)
    archive = fetch_data.archive_path(tmp_path, spec)
    archive.unlink()

    fetch_data.fetch([spec], tmp_path, download)

    assert download.calls == [spec.url]
    assert not archive.exists()


def test_a_member_with_the_wrong_size_is_extracted_again_from_the_cached_archive(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case
    fetch_data.fetch([spec], tmp_path, download)
    (tmp_path / "lh2/BGL/BGL_full.log").write_bytes(b"truncated")

    fetch_data.fetch([spec], tmp_path, download)

    assert download.calls == [spec.url]
    assert (tmp_path / "lh2/BGL/BGL_full.log").read_bytes() == LOG


def test_a_download_with_the_wrong_size_is_rejected_and_not_kept(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case
    download.payloads[spec.url] = download.payloads[spec.url][:-10]

    with pytest.raises(FetchError, match="size"):
        fetch_data.fetch([spec], tmp_path, download)

    archive = fetch_data.archive_path(tmp_path, spec)
    assert not archive.exists()
    assert list(archive.parent.glob("*")) == []
    assert not (tmp_path / "lh2/BGL/BGL_full.log").exists()


def test_a_download_with_the_wrong_checksum_is_rejected(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case
    good = download.payloads[spec.url]
    download.payloads[spec.url] = good[:-1] + bytes([good[-1] ^ 0xFF])

    with pytest.raises(FetchError, match="md5"):
        fetch_data.fetch([spec], tmp_path, download)

    assert not fetch_data.archive_path(tmp_path, spec).exists()


def test_a_corrupt_cached_archive_is_downloaded_again_and_says_why(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload], capsys: pytest.CaptureFixture[str]
) -> None:
    spec, download = zip_case
    archive = fetch_data.archive_path(tmp_path, spec)
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"not a zip")

    fetch_data.fetch([spec], tmp_path, download)

    assert download.calls == [spec.url]
    assert (tmp_path / "lh2/BGL/BGL_full.log").read_bytes() == LOG
    assert f"size 9 bytes, expected {spec.size:,}" in capsys.readouterr().out


def test_tar_gz_archives_are_extracted(tmp_path: Path) -> None:
    payload = tar_gz_bytes({"openstack_normal1.log": LOG, "anomaly_labels.txt": CSV})
    spec = spec_for(
        "OpenStack.tar.gz",
        payload,
        {
            "openstack_normal1.log": (LOG, "raw/openstack_normal1.log"),
            "anomaly_labels.txt": (CSV, "raw/anomaly_labels.txt"),
        },
    )

    fetch_data.fetch([spec], tmp_path, FakeDownload({spec.url: payload}))

    assert (tmp_path / "raw/openstack_normal1.log").read_bytes() == LOG
    assert (tmp_path / "raw/anomaly_labels.txt").read_bytes() == CSV


def test_only_the_listed_members_are_extracted(tmp_path: Path) -> None:
    payload = zip_bytes({"BGL.log": LOG, "../escape.txt": b"x", "extra.txt": b"y"})
    spec = spec_for("BGL.zip", payload, {"BGL.log": (LOG, "raw/BGL.log")})

    fetch_data.fetch([spec], tmp_path / "data", FakeDownload({spec.url: payload}))

    assert (tmp_path / "data/raw/BGL.log").read_bytes() == LOG
    assert not (tmp_path / "escape.txt").exists()
    assert sorted(p.name for p in (tmp_path / "data/raw").iterdir()) == ["BGL.log"]


def test_a_member_whose_size_differs_from_the_spec_is_an_error(tmp_path: Path) -> None:
    payload = zip_bytes({"BGL.log": LOG})
    spec = Archive(
        record="123",
        name="BGL.zip",
        size=len(payload),
        md5=hashlib.md5(payload).hexdigest(),
        members=(Member("BGL.log", len(LOG) + 1, "raw/BGL.log"),),
    )

    with pytest.raises(FetchError, match=r"raw/BGL\.log"):
        fetch_data.fetch([spec], tmp_path, FakeDownload({spec.url: payload}))

    assert not (tmp_path / "raw/BGL.log").exists()


def test_a_corrupt_archive_that_matches_its_checksum_is_a_fetch_error(tmp_path: Path) -> None:
    payload = zip_bytes({"BGL.log": LOG})[:-30]  # truncated: Zenodo-verified size and md5, unreadable
    spec = spec_for("BGL.zip", payload, {"BGL.log": (LOG, "raw/BGL.log")})

    with pytest.raises(FetchError, match=r"cannot read raw/BGL\.log from .*BGL\.zip"):
        fetch_data.fetch([spec], tmp_path, FakeDownload({spec.url: payload}))

    assert not (tmp_path / "raw/BGL.log").exists()


def test_a_corrupt_tar_archive_is_a_fetch_error(tmp_path: Path) -> None:
    payload = tar_gz_bytes({"anomaly_labels.txt": CSV})[:20]
    spec = spec_for("OpenStack.tar.gz", payload, {"anomaly_labels.txt": (CSV, "raw/anomaly_labels.txt")})

    with pytest.raises(FetchError, match="cannot read"):
        fetch_data.fetch([spec], tmp_path, FakeDownload({spec.url: payload}))


def test_check_reports_missing_files_without_downloading(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case

    (tmp_path / "lh2/BGL").mkdir(parents=True)
    (tmp_path / "lh2/BGL/BGL_full.log").write_bytes(b"short")

    problems = fetch_data.check([spec], tmp_path)

    assert [(str(path.relative_to(tmp_path)), problem) for path, problem in problems] == [
        ("lh2/BGL/BGL_full.log", f"size 5 bytes, expected {len(LOG):,}"),
        ("lh2/BGL/BGL_full.log_structured.csv", "missing"),
    ]
    assert download.calls == []


def test_main_fetches_into_slm_data_dir(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, download = zip_case
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "corpus"))
    monkeypatch.setattr(fetch_data, "ARCHIVES", (spec,))

    assert fetch_data.main([], download=download) == 0

    assert (tmp_path / "corpus/lh2/BGL/BGL_full.log").read_bytes() == LOG


def test_main_check_fails_when_files_are_missing(
    tmp_path: Path,
    zip_case: tuple[Archive, FakeDownload],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec, download = zip_case
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fetch_data, "ARCHIVES", (spec,))

    (tmp_path / "lh2/BGL").mkdir(parents=True)
    (tmp_path / "lh2/BGL/BGL_full.log").write_bytes(b"short")

    assert fetch_data.main(["--check"], download=download) == 1

    out = capsys.readouterr().out
    assert "lh2/BGL/BGL_full.log: size 5 bytes" in out
    assert "lh2/BGL/BGL_full.log_structured.csv: missing" in out
    assert download.calls == []


def test_main_reports_a_bad_download_without_a_traceback(
    tmp_path: Path,
    zip_case: tuple[Archive, FakeDownload],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec, download = zip_case
    download.payloads[spec.url] = b"short"
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fetch_data, "ARCHIVES", (spec,))

    assert fetch_data.main([], download=download) == 2

    assert "fetch_data:" in capsys.readouterr().err


# ----------------------------------------------------------------- the real specification


def test_the_specification_names_the_two_zenodo_records() -> None:
    names = {(a.record, a.name) for a in fetch_data.ARCHIVES}

    assert names == {
        ("8196385", "BGL.zip"),
        ("8196385", "OpenStack.tar.gz"),
        ("8275861", "BGL.zip"),
        ("8275861", "OpenStack.zip"),
    }
    for archive in fetch_data.ARCHIVES:
        assert archive.url == f"https://zenodo.org/api/records/{archive.record}/files/{archive.name}/content"
        assert len(archive.md5) == 32


def test_every_file_the_dev_loader_reads_is_fetched(tmp_path: Path) -> None:
    fetched = {tmp_path / m.dest for a in fetch_data.ARCHIVES for m in a.members}
    needed = {f for source in dev_loader.full_sources(tmp_path, dev_loader.TABLES) for f in source.files}

    assert needed
    assert needed <= fetched
    assert tmp_path / "raw/anomaly_labels.txt" in fetched


def never_download(url: str, target: Path) -> None:
    raise AssertionError(f"tried to download {url}")


RECORDS = sorted({a.record for a in fetch_data.ARCHIVES})


def real_data_problem(record: str, data_dir: Path) -> str | None:
    """Why `data_dir` cannot serve the real-data test of Zenodo `record`, or None if it can.

    The predicate is per record: a host can hold the Base Corpus (8196385) without the optional
    Loghub-2.0 files (8275861). A record none of whose files are there was not fetched and skips;
    a record with any file there is checked, so a partial or damaged fetch fails.
    """
    if not data_dir.is_dir():
        return f"{data_dir} does not exist; set SLM_DATA_DIR to a fetched corpus"
    members = [m for a in fetch_data.ARCHIVES if a.record == record for m in a.members]
    if not any((data_dir / m.dest).exists() for m in members):
        return (
            f"{data_dir} holds none of the files of Zenodo {record}; run `make fetch-data` "
            "or set SLM_DATA_DIR"
        )
    return None


def touch_member(data_dir: Path, record: str) -> None:
    """One file of `record` under `data_dir`, with any content."""
    member = next(a for a in fetch_data.ARCHIVES if a.record == record).members[0]
    (data_dir / member.dest).parent.mkdir(parents=True, exist_ok=True)
    (data_dir / member.dest).write_bytes(b"partial")


def test_the_real_data_predicate_is_per_archive_record(tmp_path: Path) -> None:
    # The Base Corpus is there, Loghub-2.0 is not: the first is checked, the second skips.
    touch_member(tmp_path, "8196385")

    assert RECORDS == ["8196385", "8275861"]
    assert real_data_problem("8196385", tmp_path) is None
    assert "holds none of the files of Zenodo 8275861" in str(real_data_problem("8275861", tmp_path))


def test_the_real_data_predicate_skips_a_data_dir_that_does_not_exist(tmp_path: Path) -> None:
    for record in RECORDS:
        assert "does not exist" in str(real_data_problem(record, tmp_path / "nowhere"))


@pytest.mark.parametrize("record", RECORDS)
def test_an_already_fetched_data_dir_is_complete_and_left_alone(record: str) -> None:
    # Runs for every record the data dir holds any file of, so a partial or damaged fetch fails here.
    data_dir = load_settings().data_dir
    if (problem := real_data_problem(record, data_dir)) is not None:
        pytest.skip(problem)
    archives = [a for a in fetch_data.ARCHIVES if a.record == record]
    download: Callable[[str, Path], None] = never_download

    assert fetch_data.check(archives, data_dir) == []
    fetch_data.fetch(archives, data_dir, download)
