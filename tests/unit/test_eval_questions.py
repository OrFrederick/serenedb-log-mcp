"""The eval question set (T17): its shape, and that it only names tools, arguments and prompts that exist."""

from collections import Counter
from pathlib import Path
from typing import Any

import anyio
import pytest

import questions as qmod
from questions import Question, QuestionError, load
from serenedb_log_mcp.config import Role
from serenedb_log_mcp.server import create_server

QS = load()
FUTURE_TOOLS = {"list_incidents", "top_patterns", "list_patterns", "search_patterns", "export_investigation"}
"""Planned tools (plan section 2.3) that may appear in an any_of group before they exist."""


@pytest.fixture(scope="module")
def schemas() -> dict[str, set[str]]:
    tools = anyio.run(create_server().list_tools)
    return {t.name: set(t.input_schema["properties"]) for t in tools}


def test_ten_questions_per_role_and_ten_adversarial() -> None:
    assert Counter(q.group for q in QS.questions) == {
        "engineer": 10,
        "team_lead": 10,
        "manager": 10,
        "adversarial": 10,
    }


def test_each_group_has_a_tune_and_a_holdout_split() -> None:
    splits = Counter((q.group, q.split) for q in QS.questions)
    for group in ("engineer", "team_lead", "manager", "adversarial"):
        assert splits[(group, "tune")] == 7
        assert splits[(group, "holdout")] == 3


def test_cross_role_triples_cover_every_role() -> None:
    triples = QS.triples()

    assert len(triples) >= 3
    for members in triples.values():
        assert set(members) == set(Role)


def test_the_adversarial_set_has_the_cost_question_and_the_disk_full_controls() -> None:
    adversarial = [q for q in QS.questions if q.group == "adversarial"]
    cost = [q for q in adversarial if q.role is Role.MANAGER and "cost" in q.question]
    disk_full = [q for q in adversarial if "disk full" in q.question]

    assert cost
    assert cost[0].expect.unknowns
    assert {q.prompt_args.get("dataset") for q in disk_full} == {"bgl", "openstack"}


def test_required_tools_exist_and_any_of_groups_name_one_that_does(schemas: dict[str, set[str]]) -> None:
    for q in QS.questions:
        for tool in [*q.expect.must_call, *q.expect.order, *q.expect.must_not_call]:
            assert tool in schemas, f"{q.id}: {tool} is not a tool of the server"
        for group in q.expect.any_of:
            assert set(group) & set(schemas), f"{q.id}: no tool of {group} exists yet"
            assert set(group) <= set(schemas) | FUTURE_TOOLS, f"{q.id}: unknown tool in {group}"


def test_argument_predicates_name_real_arguments(schemas: dict[str, set[str]]) -> None:
    every_argument = set().union(*schemas.values())
    for q in QS.questions:
        for predicate in q.expect.args:
            known = every_argument if predicate.tool == "*" else schemas[predicate.tool]
            assert predicate.arg in known, f"{q.id}: {predicate.describe()} names no argument of the tool"


def test_prompt_arguments_follow_the_server_prompts() -> None:
    prompts = anyio.run(create_server().list_prompts)
    declared = {p.name: tuple(a.name for a in p.arguments or []) for p in prompts}

    assert declared == {role.value: names for role, names in qmod._PROMPT_ARGUMENTS.items()}


def test_slash_command_passes_arguments_positionally() -> None:
    question = QS.by_id()["tl-10"]

    assert question.slash_command("serenedb-log") == "/mcp__serenedb-log__team_lead openstack 24h"
    assert QS.by_id()["tl-08"].slash_command("serenedb-log") == "/mcp__serenedb-log__team_lead"


def test_select_by_ids_and_split() -> None:
    assert [q.id for q in QS.select(["eng-02", "eng-01"])] == ["eng-01", "eng-02"]
    assert all(q.split == "holdout" for q in QS.select(split="holdout"))
    with pytest.raises(QuestionError, match="nope-01"):
        QS.select(["nope-01"])


def _question(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "eng-01", "role": "engineer", "split": "tune", "question": "What failed?"}
    return base | fields


def _write(tmp_path: Path, questions: list[dict[str, Any]]) -> Path:
    import yaml

    path = tmp_path / "questions.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "questions": questions}))
    return path


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"role": "manager"}, "cannot have role"),
        ({"prompt_args": {"period": "24h"}}, "not arguments of the engineer prompt"),
        ({"prompt_args": {"dataset": "two words"}}, "one non-empty token"),
        ({"expect": {"must_call": ["trend"], "must_not_call": ["trend"]}}, "both required and forbidden"),
        ({"expect": {"args": [{"tool": "count_by", "arg": "levels", "op": "in", "value": "FATAL"}]}}, "list"),
        ({"expect": {"golden": [{"name": "x", "sql": "SELECT 1"}]}}, "must read"),
        ({"expect": {"mention": ["("]}}, "not a regex"),
        ({"expect": {"must_cal": ["count_by"]}}, "must_cal"),
    ],
)
def test_invalid_questions_are_refused(tmp_path: Path, fields: dict[str, Any], message: str) -> None:
    with pytest.raises(QuestionError, match=message):
        load(_write(tmp_path, [_question(**fields)]))


def test_a_later_prompt_argument_needs_the_earlier_ones(tmp_path: Path) -> None:
    with pytest.raises(QuestionError, match="needs every earlier argument"):
        load(_write(tmp_path, [_question(prompt_args={"focus": "KERNDTLB"})]))


def test_duplicate_ids_and_incomplete_triples_are_refused(tmp_path: Path) -> None:
    with pytest.raises(QuestionError, match="duplicate"):
        load(_write(tmp_path, [_question(), _question()]))
    with pytest.raises(QuestionError, match="no question for"):
        load(_write(tmp_path, [_question(triple="t1")]))


def test_report_group_of_an_adversarial_question() -> None:
    question = Question.model_validate(_question(id="adv-01"))

    assert question.group == "adversarial"
