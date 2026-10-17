#!/usr/bin/env python3
"""Download the Base Corpus and the Loghub-2.0 files into SLM_DATA_DIR.

Usage (see `make help`):
    uv run python scripts/fetch_data.py            # download what is missing, verify, extract
    uv run python scripts/fetch_data.py --check    # only report missing files; exit 1 if any

Sources:
    Zenodo 8196385 (Loghub): BGL.zip, OpenStack.tar.gz           -> raw/
    Zenodo 8275861 (Loghub-2.0): BGL.zip, OpenStack.zip          -> lh2/BGL/, lh2/OpenStack/

The script is idempotent. A file that already exists with the expected size is left alone, so a
second run downloads nothing. An archive is downloaded only when one of its files is missing or
has the wrong size; it is checked against the size and md5 that Zenodo publishes before anything
is extracted, and it is kept under archives/<record>/ so a damaged file can be re-extracted
without downloading again. Only the listed files are extracted, each through a temporary file.

raw/anomaly_labels.txt holds the OpenStack ground-truth labels. Only scripts/ground_truth.py may
read it; no derive step and no tool loads it (plan section 2.2).

Data: Loghub, https://github.com/logpai/loghub. Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu,
Michael R. Lyu. Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics.
ISSRE 2023. Loghub-2.0: Zhihan Jiang, Jinyang Liu, Junjie Huang, Yichen Li, Yintong Huo, Jiazhen
Gu, Zhuangbin Chen, Jieming Zhu, Michael R. Lyu. A Large-scale Evaluation for Log Parsing
Techniques: How Far Are We? ISSTA 2024.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from serenedb_log_mcp.config import ConfigError, load_settings

CHUNK = 1 << 20


class FetchError(Exception):
    """A download or archive that does not match its specification; printed without a traceback."""


@dataclass(frozen=True)
class Member:
    """One file inside an archive: its path there, its size, and where it goes under SLM_DATA_DIR."""

    path: str
    size: int
    dest: str


@dataclass(frozen=True)
class Archive:
    """One file of a Zenodo record, with the size and md5 that Zenodo publishes for it."""

    record: str
    name: str
    size: int
    md5: str
    members: tuple[Member, ...]

    @property
    def url(self) -> str:
        return f"https://zenodo.org/api/records/{self.record}/files/{self.name}/content"


# Sizes and md5 from https://zenodo.org/api/records/<record> (checked 2026-10-17); member sizes
# from the archives themselves.
ARCHIVES: tuple[Archive, ...] = (
    Archive(
        record="8196385",
        name="BGL.zip",
        size=57_489_019,
        md5="4452953c470f2d95fcb32d5f6e733f7a",
        members=(
            Member("BGL.log", 743_185_031, "raw/BGL.log"),
            Member("README.md", 1_246, "raw/README.md"),
        ),
    ),
    Archive(
        record="8196385",
        name="OpenStack.tar.gz",
        size=5_394_691,
        md5="66bd42c07837a094d9b0ea2d036b5713",
        members=(
            Member("openstack_normal1.log", 15_499_595, "raw/openstack_normal1.log"),
            Member("openstack_normal2.log", 40_506_365, "raw/openstack_normal2.log"),
            Member("openstack_abnormal.log", 5_436_122, "raw/openstack_abnormal.log"),
            Member("anomaly_labels.txt", 243, "raw/anomaly_labels.txt"),
        ),
    ),
    Archive(
        record="8275861",
        name="BGL.zip",
        size=86_118_141,
        md5="521ee643375bb6e9d0dd88636ff7ece1",
        members=(
            Member("BGL/BGL_full.log", 719_447_452, "lh2/BGL/BGL_full.log"),
            Member("BGL/BGL_full.log_structured.csv", 479_786_584, "lh2/BGL/BGL_full.log_structured.csv"),
            Member("BGL/BGL_full.log_templates.csv", 22_318, "lh2/BGL/BGL_full.log_templates.csv"),
        ),
    ),
    Archive(
        record="8275861",
        name="OpenStack.zip",
        size=7_387_614,
        md5="707faeedf8b1b9f920c073114139a023",
        members=(
            Member("OpenStack/OpenStack_full.log", 61_407_564, "lh2/OpenStack/OpenStack_full.log"),
            Member(
                "OpenStack/OpenStack_full.log_structured.csv",
                36_993_999,
                "lh2/OpenStack/OpenStack_full.log_structured.csv",
            ),
            Member(
                "OpenStack/OpenStack_full.log_templates.csv",
                3_102,
                "lh2/OpenStack/OpenStack_full.log_templates.csv",
            ),
        ),
    ),
)

Download = Callable[[str, Path], None]


def http_download(url: str, target: Path) -> None:
    """Stream `url` into `target`, printing progress to stderr."""
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(CHUNK):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / total:6.1%} of {total:,} bytes", end="", file=sys.stderr, flush=True)
        if total:
            print(file=sys.stderr)


def archive_path(data_dir: Path, archive: Archive) -> Path:
    return data_dir / "archives" / archive.record / archive.name


def _has_size(path: Path, size: int) -> bool:
    return path.is_file() and path.stat().st_size == size


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_problem(path: Path, archive: Archive) -> str | None:
    if not path.is_file():
        return "missing"
    size = path.stat().st_size
    if size != archive.size:
        return f"size {size:,} bytes, expected {archive.size:,}"
    digest = _md5(path)
    if digest != archive.md5:
        return f"md5 {digest}, expected {archive.md5}"
    return None


def check(archives: Sequence[Archive], data_dir: Path) -> list[Path]:
    """The files under `data_dir` that are missing or have the wrong size."""
    return [
        data_dir / m.dest for a in archives for m in a.members if not _has_size(data_dir / m.dest, m.size)
    ]


def _ensure_archive(archive: Archive, data_dir: Path, download: Download) -> Path:
    path = archive_path(data_dir, archive)
    if _archive_problem(path, archive) is None:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    print(f"downloading {archive.url} ({archive.size:,} bytes)", flush=True)
    try:
        download(archive.url, partial)
        if problem := _archive_problem(partial, archive):
            raise FetchError(f"{archive.url} has {problem}; not keeping it")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


@contextmanager
def _open_member(path: Path, name: str) -> Iterator[IO[bytes]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zipped, zipped.open(name) as member:
            yield member
        return
    with tarfile.open(path) as tarred:
        extracted = tarred.extractfile(name)
        if extracted is None:
            raise FetchError(f"{path} has no regular file {name}")
        with extracted:
            yield extracted


def _extract(path: Path, member: Member, data_dir: Path) -> None:
    target = data_dir / member.dest
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        with _open_member(path, member.path) as source, partial.open("wb") as out:
            shutil.copyfileobj(source, out, CHUNK)
        size = partial.stat().st_size
        if size != member.size:
            raise FetchError(f"{member.dest} extracted with {size:,} bytes, expected {member.size:,}")
        partial.replace(target)
    except KeyError:
        raise FetchError(f"{path} has no file {member.path}") from None
    finally:
        partial.unlink(missing_ok=True)


def fetch(archives: Sequence[Archive], data_dir: Path, download: Download = http_download) -> None:
    """Make every member of `archives` present under `data_dir` with its expected size."""
    for archive in archives:
        stale = [m for m in archive.members if not _has_size(data_dir / m.dest, m.size)]
        if not stale:
            print(f"ok      {archive.record}/{archive.name}: {len(archive.members)} files", flush=True)
            continue
        path = _ensure_archive(archive, data_dir, download)
        for member in stale:
            _extract(path, member, data_dir)
            print(f"wrote   {member.dest} ({member.size:,} bytes)", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_data.py",
        description="Download BGL and OpenStack (Zenodo 8196385) and Loghub-2.0 (Zenodo 8275861) "
        "into SLM_DATA_DIR.",
    )
    parser.add_argument("--check", action="store_true", help="only report missing files; exit 1 if any")
    return parser


def main(argv: Sequence[str] | None = None, *, download: Download = http_download) -> int:
    args = build_parser().parse_args(argv)
    try:
        data_dir = load_settings().data_dir
        if args.check:
            missing = check(ARCHIVES, data_dir)
            for path in missing:
                print(f"missing {path}")
            return 1 if missing else 0
        fetch(ARCHIVES, data_dir, download)
    except (FetchError, ConfigError, OSError) as exc:
        print(f"fetch_data: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
