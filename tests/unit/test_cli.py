"""The serenedb-log-mcp command line: --transport stdio|http, --port, --role."""

import subprocess
import sys
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import pytest

from serenedb_log_mcp import cli, config, ledger


@dataclass
class FakeServer:
    runs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def run(self, transport: str, **kwargs: Any) -> None:
        self.runs.append((transport, kwargs))


@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    server = FakeServer()
    monkeypatch.setattr(cli, "create_server", lambda: server)
    monkeypatch.setattr(config, "_active", None)
    for name in ("SLM_SCHEMA", "SLM_QUERY_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    return server


def test_help_lists_transport_port_and_role(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    out = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert "--transport {stdio,http}" in out
    assert "--port" in out
    assert "--role {engineer,team_lead,manager}" in out


def test_console_script_points_at_cli_main() -> None:
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}

    assert scripts["serenedb-log-mcp"] == "serenedb_log_mcp.cli:main"


def test_python_dash_m_runs_the_cli() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "serenedb_log_mcp", "--help"], capture_output=True, text=True, timeout=60
    )

    assert proc.returncode == 0, proc.stderr
    assert "--transport" in proc.stdout


def test_defaults_are_stdio_without_role_pin() -> None:
    args = cli.build_parser().parse_args([])

    assert args.transport == "stdio"
    assert args.port == 8000
    assert args.host == "127.0.0.1"
    assert args.role is None


@pytest.mark.parametrize("argv", [["--role", "ceo"], ["--transport", "sse"], ["--port", "x"]])
def test_invalid_arguments_exit_with_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)

    assert exit_info.value.code == 2


def test_stdio_transport_runs_the_server_over_stdio(fake_server: FakeServer) -> None:
    cli.main([])

    assert fake_server.runs == [("stdio", {})]


def test_http_transport_runs_streamable_http_on_the_given_port(fake_server: FakeServer) -> None:
    cli.main(["--transport", "http", "--port", "9123", "--host", "0.0.0.0"])

    assert fake_server.runs == [("streamable-http", {"host": "0.0.0.0", "port": 9123})]


def test_role_flag_pins_the_role_in_the_active_settings(fake_server: FakeServer) -> None:
    cli.main(["--role", "manager"])

    assert config.get_settings().role == "manager"


def test_invalid_environment_exits_with_a_message_naming_the_variable(
    fake_server: FakeServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SLM_SCHEMA", "not-valid")

    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == 2
    assert "SLM_SCHEMA" in capsys.readouterr().err
    assert fake_server.runs == []


def test_settings_validation_error_exits_with_a_message(
    fake_server: FakeServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def invalid_settings(**overrides: Any) -> config.Settings:
        return config.Settings(query_timeout_s=-1)

    monkeypatch.setattr(cli, "load_settings", invalid_settings)

    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == 2
    assert "query_timeout_s" in capsys.readouterr().err
    assert fake_server.runs == []


def test_a_malformed_ledger_path_fails_at_startup(
    fake_server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The Evidence Ledger is loaded at startup, so a broken LEDGER_PATH is a usage error now
    # instead of a ToolError in the middle of an investigation.
    broken = tmp_path / "ledger.jsonl"
    broken.write_text("this is not an Evidence Ledger\n", encoding="utf-8")
    monkeypatch.setenv("LEDGER_PATH", str(broken))

    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == 2
    assert "LEDGER_PATH" in capsys.readouterr().err
    assert fake_server.runs == []


def test_a_usable_ledger_path_is_loaded_before_the_server_runs(
    fake_server: FakeServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    usable = tmp_path / "ledger.jsonl"
    usable.write_text('{"evidence_id": "E7", "tool": "count_by"}\n', encoding="utf-8")
    monkeypatch.setenv("LEDGER_PATH", str(usable))

    cli.main([])

    assert fake_server.runs == [("stdio", {})]
    # Startup built the process-wide ledger; nothing in this test called get_ledger() itself.
    assert ledger._active is not None
    assert ledger._active.path == usable
    assert ledger._active.loaded == 1
    # Loaded, so the numbering continues where the file ends.
    assert ledger._active.last_id == "E7"
