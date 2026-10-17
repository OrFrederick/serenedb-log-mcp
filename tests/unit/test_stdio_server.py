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
from typing import IO, Any, NoReturn

import pytest

PROTOCOL_VERSION = "2025-11-25"
TIMEOUT_S = 30


class StdioPeer:
    """A minimal line-delimited JSON-RPC client over a child's stdin/stdout."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        self.proc = proc
        self.stdin: IO[str] = proc.stdin
        self.lines: queue.Queue[str] = queue.Queue()
        self.stderr_lines: list[str] = []
        self._stderr_thread = threading.Thread(target=self._drain, args=(proc.stderr,), daemon=True)
        threading.Thread(target=self._pump, args=(proc.stdout,), daemon=True).start()
        self._stderr_thread.start()

    def _pump(self, stdout: IO[str]) -> None:
        for line in stdout:
            self.lines.put(line)

    def _drain(self, stderr: IO[str]) -> None:
        # Read stderr continuously so the child never blocks on a full pipe.
        for line in stderr:
            self.stderr_lines.append(line)

    def fail(self, reason: str) -> NoReturn:
        if self.proc.poll() is not None:
            self._stderr_thread.join(timeout=2)
        stderr = "".join(self.stderr_lines).strip() or "<empty>"
        pytest.fail(f"{reason}\n--- server stderr ---\n{stderr}")

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
                    self.fail(f"server exited with code {self.proc.returncode} before answering")
                continue
            message: dict[str, Any] = json.loads(line)
            if message.get("id") == request_id:
                return message
        self.fail(f"no response to request {request_id} within {TIMEOUT_S}s")


@pytest.fixture
def server() -> Iterator[StdioPeer]:
    script = Path(sys.executable).with_name("serenedb-log-mcp")
    assert script.exists(), f"console script missing at {script}; run uv sync"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [str(script), "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def test_a_crashing_server_reports_its_stderr() -> None:
    crash = "import sys; sys.stderr.write('boom: bad config\\n'); sys.exit(3)"
    peer = StdioPeer(
        subprocess.Popen(
            [sys.executable, "-c", crash],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    )

    with pytest.raises(pytest.fail.Exception, match=r"(?s)code 3.*boom: bad config"):
        peer.response(1)


def initialize(server: StdioPeer) -> dict[str, Any]:
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
    server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return init["result"]


def test_role_prompts_tools_and_instructions_over_stdio(server: StdioPeer) -> None:
    # The smoke run of T7: what Claude Code sees when it starts the server.
    result = initialize(server)
    assert "prompts" in result["capabilities"]
    assert "get_role_brief" in result["instructions"]
    assert len(result["instructions"]) <= 4096

    server.send({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"})
    prompts = server.response(2)["result"]["prompts"]
    assert sorted(prompt["name"] for prompt in prompts) == ["engineer", "manager", "team_lead"]

    server.send({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in server.response(3)["result"]["tools"]}
    for name in ("describe_corpus", "search_logs", "get_context", "count_by", "trend", "get_evidence"):
        assert name in tools, name
    assert tools["get_role_brief"]["_meta"] == {"anthropic/alwaysLoad": True}

    server.send(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "prompts/get",
            "params": {"name": "manager", "arguments": {"dataset": "bgl", "period": "30d"}},
        }
    )
    (message,) = server.response(4)["result"]["messages"]
    assert message["role"] == "user"
    assert "not measurable from these logs" in message["content"]["text"]

    server.send(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "get_role_brief", "arguments": {"role": "manager"}},
        }
    )
    brief = server.response(5)["result"]
    assert brief["isError"] is False
    assert brief["structuredContent"]["brief"] in message["content"]["text"]
