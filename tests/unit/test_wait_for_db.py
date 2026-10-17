"""scripts/wait_for_db.py: poll SELECT 1 until SereneDB answers or the timeout passes."""

import time

import pytest

import wait_for_db

UNREACHABLE = "host=127.0.0.1 port=9 dbname=postgres user=postgres"


def test_unreachable_server_fails_after_the_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    started = time.monotonic()

    code = wait_for_db.main(["--dsn", UNREACHABLE, "--timeout", "0.6", "--interval", "0.2"])

    elapsed = time.monotonic() - started
    err = capsys.readouterr().err
    assert code == 1
    assert 0.5 <= elapsed < 5
    assert "did not answer within 0.6 s" in err
    assert "host=127.0.0.1 port=9" in err


def test_password_is_not_printed(capsys: pytest.CaptureFixture[str]) -> None:
    wait_for_db.main(["--dsn", UNREACHABLE + " password=hunter2", "--timeout", "0.2", "--interval", "0.1"])

    captured = capsys.readouterr()
    assert "hunter2" not in captured.err + captured.out


@pytest.mark.parametrize("value", ["0", "-1", "nan"])
def test_timeout_must_be_positive(value: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        wait_for_db.main(["--dsn", UNREACHABLE, "--timeout", value])

    assert exit_info.value.code == 2
