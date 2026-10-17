"""scripts/fetch_data.py: download, verify and extract the Base Corpus and Loghub-2.0 archives.

No test here touches the network: a fake download copies prepared archives, and the real-data test
only checks file sizes in an existing SLM_DATA_DIR.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

import dev_loader
import fetch_data
from fetch_data import Archive, FetchError, Member


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


def test_a_corrupt_cached_archive_is_downloaded_again(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case
    archive = fetch_data.archive_path(tmp_path, spec)
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"not a zip")

    fetch_data.fetch([spec], tmp_path, download)

    assert download.calls == [spec.url]
    assert (tmp_path / "lh2/BGL/BGL_full.log").read_bytes() == LOG


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


def test_check_reports_missing_files_without_downloading(
    tmp_path: Path, zip_case: tuple[Archive, FakeDownload]
) -> None:
    spec, download = zip_case

    missing = fetch_data.check([spec], tmp_path)

    assert [str(path.relative_to(tmp_path)) for path in missing] == [
        "lh2/BGL/BGL_full.log",
        "lh2/BGL/BGL_full.log_structured.csv",
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

    assert fetch_data.main(["--check"], download=download) == 1

    assert "lh2/BGL/BGL_full.log" in capsys.readouterr().out
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

    assert needed <= fetched
    assert tmp_path / "raw/anomaly_labels.txt" in fetched


def never_download(url: str, target: Path) -> None:
    raise AssertionError(f"tried to download {url}")


def real_data_dir() -> Path | None:
    value = os.environ.get("SLM_DATA_DIR", "").strip()
    if not value:
        return None
    path = Path(value)
    return path if all((path / m.dest).exists() for a in fetch_data.ARCHIVES for m in a.members) else None


@pytest.mark.skipif(real_data_dir() is None, reason="SLM_DATA_DIR does not hold the fetched corpus")
def test_an_already_fetched_data_dir_is_left_alone() -> None:
    data_dir = real_data_dir()
    assert data_dir is not None
    download: Callable[[str, Path], None] = never_download

    assert fetch_data.check(fetch_data.ARCHIVES, data_dir) == []
    fetch_data.fetch(fetch_data.ARCHIVES, data_dir, download)
