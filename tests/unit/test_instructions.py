"""The server instructions (T7): at most 4,096 characters, covering the points of plan section 2.3.

Claude Code loads the instructions at startup and cuts them at 4,096 characters, so the limit is a
hard one, with a Role pin line added too.
"""

import anyio
import pytest
from mcp import Client

from serenedb_log_mcp.config import load_settings, set_settings
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.instructions import INSTRUCTIONS, MAX_INSTRUCTIONS_CHARS, server_instructions
from serenedb_log_mcp.server import create_server


def test_the_limit_is_claude_codes() -> None:
    assert MAX_INSTRUCTIONS_CHARS == 4096


@pytest.mark.parametrize("pin", [None, *Role])
def test_the_instructions_stay_within_the_limit(pin: Role | None) -> None:
    assert len(server_instructions(pin)) <= MAX_INSTRUCTIONS_CHARS


@pytest.mark.parametrize(
    ("point", "phrases"),
    [
        ("the Datasets", ["bgl", "openstack", "describe_corpus"]),
        ("retrieval returns samples, aggregation answers frequency", ["sample", "count_by", "trend"]),
        ("cite [E<n>] next to every number, quote exact numbers", ["[E<n>]", "exactly"]),
        ("Log Entries are not Incidents", ["Log Entries are not Incidents"]),
        ("four sections", ["Observed Facts", "Hypotheses", "Unknowns", "Next Step"]),
        ("verify_answer before answering", ["verify_answer"]),
        ("the Role prompts and get_role_brief", ["get_role_brief", "engineer", "team_lead", "manager"]),
        ("the Manager does not invent impact", ["cost", "downtime", "customer impact", "not_measured"]),
        ("the Roles differ in what they receive", ["no raw Log Entries", "at most 3"]),
        ("relative times resolve against the corpus end", ["not today"]),
    ],
)
def test_the_instructions_cover_the_points_of_the_plan(point: str, phrases: list[str]) -> None:
    for phrase in phrases:
        assert phrase in INSTRUCTIONS, f"{point}: {phrase!r} missing"


def test_never_calls_log_entries_incidents_except_to_say_they_are_not() -> None:
    lowered = INSTRUCTIONS.lower()

    assert lowered.count("incident") == lowered.count("not incidents") + lowered.count("never call")


def test_an_unpinned_server_has_the_plain_instructions() -> None:
    assert server_instructions(None) == INSTRUCTIONS


def test_a_pinned_server_says_which_role_to_pass() -> None:
    text = server_instructions(Role.MANAGER)

    assert text.startswith(INSTRUCTIONS)
    assert "role='manager'" in text
    assert "--role manager" in text


def handshake_instructions() -> str | None:
    async def handshake() -> str | None:
        async with Client(create_server(), mode="legacy") as client:
            return client.instructions

    return anyio.run(handshake)


def test_the_server_sends_the_instructions() -> None:
    assert handshake_instructions() == INSTRUCTIONS


def test_the_pinned_server_sends_the_pinned_instructions() -> None:
    set_settings(load_settings({}, role="team_lead"))

    assert handshake_instructions() == server_instructions(Role.TEAM_LEAD)
