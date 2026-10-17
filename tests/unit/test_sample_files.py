"""tests/support/sample_files.py: which sample-download failures may skip a test and which may not."""

import socket
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
import sample_files

import dev_loader


@pytest.fixture(autouse=True)
def _own_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SLM_REQUIRE_DB", raising=False)


def _raise(exc: BaseException) -> None:
    def fail(*args: object, **kwargs: object) -> list[Path]:
        raise exc

    return fail  # type: ignore[return-value]


@pytest.mark.parametrize(
    "exc",
    [
        URLError(socket.gaierror(8, "nodename nor servname provided")),
        URLError(ConnectionRefusedError(61, "Connection refused")),
        HTTPError("https://example.invalid/x", 503, "Service Unavailable", {}, None),  # type: ignore[arg-type]
        TimeoutError("timed out"),
    ],
)
def test_a_network_failure_skips(exc: BaseException, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_loader, "ensure_samples", _raise(exc))

    with pytest.raises(pytest.skip.Exception, match="cannot download the Loghub 2k samples"):
        sample_files.samples_or_skip()


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError(13, "Permission denied"),
        OSError(30, "Read-only file system"),
        OSError(28, "No space left on device"),
        dev_loader.LoaderError("has sha256 beef, expected cafe; not saving it"),
    ],
)
def test_an_environment_failure_is_raised_not_skipped(
    exc: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Skipping here would turn a whole db module into a silent pass on a broken machine.
    monkeypatch.setattr(dev_loader, "ensure_samples", _raise(exc))

    with pytest.raises(type(exc)):
        sample_files.samples_or_skip()


def test_require_db_turns_the_network_skip_into_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_REQUIRE_DB", "1")
    monkeypatch.setattr(dev_loader, "ensure_samples", _raise(URLError("down")))

    with pytest.raises(pytest.fail.Exception, match="SLM_REQUIRE_DB is set"):
        sample_files.samples_or_skip()


def test_it_returns_the_sample_paths_when_the_download_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_loader, "ensure_samples", lambda directory: [directory / "BGL_2k.log"])

    paths = sample_files.samples_or_skip()

    assert [p.name for p in paths] == ["BGL_2k.log"]
    assert paths[0].parent.name == "sample"
