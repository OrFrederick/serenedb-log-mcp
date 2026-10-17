"""Makefile targets that cannot run in tests are checked with `make -n` (print, do not run)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def dry_run(*args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SERENEDB_")}
    proc = subprocess.run(
        ["make", "-n", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True, env=env
    )
    return proc.stdout


@pytest.mark.parametrize("arch", ["x86_64", "amd64"])
def test_db_up_uses_26_10_1_for_amd64_hosts(arch: str) -> None:
    out = dry_run("db-up", f"HOST_ARCH={arch}")

    assert "--platform linux/amd64" in out
    assert "serenedb/serenedb:26.10.1" in out


@pytest.mark.parametrize("arch", ["arm64", "aarch64"])
def test_db_up_uses_the_newest_arm64_build_on_arm64_hosts(arch: str) -> None:
    # 26.10.1 is published for linux/amd64 only and crashes under emulation on Apple Silicon.
    out = dry_run("db-up", f"HOST_ARCH={arch}")

    assert "--platform linux/arm64" in out
    assert "serenedb/serenedb:26.09.1.3" in out


@pytest.mark.parametrize("arch", ["x86_64", "arm64"])
def test_db_up_image_and_platform_can_be_overridden(arch: str) -> None:
    out = dry_run(
        "db-up", f"HOST_ARCH={arch}", "SERENEDB_IMAGE=example/img:1", "SERENEDB_PLATFORM=linux/s390x"
    )

    assert "--platform linux/s390x" in out
    assert "example/img:1" in out


def test_db_up_uses_trust_auth_and_waits_until_the_server_answers() -> None:
    out = dry_run("db-up", "SERENEDB_PORT=17890")

    assert "-e SERENEDB_HOST_AUTH_METHOD=trust" in out
    assert "scripts/wait_for_db.py --timeout 90" in out
    assert '--dsn "host=localhost port=17890 dbname=postgres user=postgres"' in out
    assert "docker logs --tail 20" in out


def test_db_up_names_serenedb_port_and_the_container_when_docker_run_fails() -> None:
    # The bind error itself says only "port is already allocated", which does not say which
    # variable to change.
    out = dry_run("db-up", "SERENEDB_PORT=17890")

    assert "SERENEDB_PORT=17890" in out
    assert "SERENEDB_CONTAINER=serenedb-log-mcp-db" in out
    # Not `rm -f`: a container of that name that is still running belongs to someone else, and
    # plain `docker rm` refuses it instead of killing it.
    assert "docker rm serenedb-log-mcp-db" in out


def test_db_up_removes_the_container_when_the_readiness_wait_fails() -> None:
    # Otherwise the next `make db-up` dies with "container name already in use".
    out = dry_run("db-up", "SERENEDB_PORT=17890")

    assert "docker logs --tail 20 serenedb-log-mcp-db" in out
    assert "docker rm -f serenedb-log-mcp-db" in out
    assert "SERENEDB_WAIT_S=90" in out
