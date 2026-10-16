"""Start the real console script over stdio and speak raw JSON-RPC to it, as Claude Code does."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest

PROTOCOL_VERSION = "2025-11-25"
TIMEOUT_S = 30


class StdioPeer:
    """A minimal line-delimited JSON-RPC client over a child's stdin/stdout."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdin is not None
        assert proc.stdout is not None
        self.proc = proc
        self.stdin: IO[str] = proc.stdin
        self.lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, args=(proc.stdout,), daemon=True).start()

    def _pump(self, stdout: IO[str]) -> None:
        for line in stdout:
            self.lines.put(line)

    def send(self, message: dict[str, Any]) -> None:
        self.stdin.write(json.dumps(message) + "\n")
        self.stdin.flush()

    def response(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    pytest.fail(f"server exited with code {self.proc.returncode} before answering")
                continue
            message: dict[str, Any] = json.loads(line)
            if message.get("id") == request_id:
                return message
        pytest.fail(f"no response to request {request_id} within {TIMEOUT_S}s")


@pytest.fixture
def server() -> Iterator[StdioPeer]:
    script = Path(sys.executable).with_name("serenedb-log-mcp")
    assert script.exists(), f"console script missing at {script}; run uv sync"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [str(script), "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    try:
        yield StdioPeer(proc)
    finally:
        proc.terminate()
        proc.wait(timeout=TIMEOUT_S)


def test_initialize_and_tools_list_over_stdio(server: StdioPeer) -> None:
    server.send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        }
    )
    init = server.response(1)

    assert "error" not in init, init
    assert init["jsonrpc"] == "2.0"
    assert init["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert init["result"]["serverInfo"]["name"] == "serenedb-log"
    assert "tools" in init["result"]["capabilities"]

    server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = server.response(2)

    assert "error" not in tools, tools
    assert isinstance(tools["result"]["tools"], list)
