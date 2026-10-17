"""scripts/wait_for_db.py against a running SereneDB."""

import pytest

import wait_for_db


def test_running_server_is_ready_at_once(database_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert wait_for_db.main(["--dsn", database_url, "--timeout", "10"]) == 0

    assert "SereneDB" in capsys.readouterr().out
