"""The agent index registry (T19): names, the catalog marker, slice coverage and use capture."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest

from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex

START, END = datetime(2005, 8, 22), datetime(2005, 9, 19)


def wave(schema: str = "slm", name: str = "agent_idx_bgl_0a1b2c3d", dataset: str = "bgl") -> AgentIndex:
    return AgentIndex(schema=schema, name=name, dataset=dataset, time_from=START, time_to=END, rows=310_868)


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    agent_indexes.clear()
    yield
    agent_indexes.clear()


# ----------------------------------------------------------------- names


def test_a_new_name_carries_the_dataset_and_eight_hex_characters() -> None:
    name = agent_indexes.new_name("openstack", token=lambda: "deadbeef")

    assert name == "agent_idx_openstack_deadbeef"
    assert agent_indexes.is_agent_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "bgl_entry_idx",
        "os_entry_idx",
        "agent_idx_bgl_0a1b2c3",  # seven hex characters
        "agent_idx_bgl_0A1B2C3D",  # upper case
        "agent_idx_hdfs_0a1b2c3d",  # not a Dataset
        "agent_idx_bgl_0a1b2c3d; DROP TABLE x",
        "",
    ],
)
def test_only_generated_names_are_agent_names(name: str) -> None:
    assert not agent_indexes.is_agent_name(name)


# ----------------------------------------------------------------- the catalog marker


def test_the_marker_round_trips_through_the_index_comment() -> None:
    index = wave()

    assert AgentIndex.from_marker("slm", index.name, index.marker()) == index


@pytest.mark.parametrize(
    "comment",
    [
        None,
        "",
        "made by hand",
        'serenedb-log-mcp agent index {"dataset": "bgl"}',  # incomplete
        "serenedb-log-mcp agent index not json",
        'serenedb-log-mcp agent index {"dataset": "hdfs", "time_from": "2005-08-22T00:00:00", '
        '"time_to": "2005-09-19T00:00:00", "rows": 1}',
    ],
)
def test_an_index_without_a_valid_marker_is_not_an_agent_index(comment: str | None) -> None:
    assert AgentIndex.from_marker("slm", "agent_idx_bgl_0a1b2c3d", comment) is None


def test_a_marker_on_an_index_with_a_foreign_name_is_not_an_agent_index() -> None:
    assert AgentIndex.from_marker("slm", "bgl_entry_idx", wave().marker()) is None


def test_a_marker_whose_dataset_differs_from_the_name_is_not_an_agent_index() -> None:
    marker = wave(dataset="openstack", name="agent_idx_openstack_0a1b2c3d").marker()

    assert AgentIndex.from_marker("slm", "agent_idx_bgl_0a1b2c3d", marker) is None


# ----------------------------------------------------------------- slice coverage


@pytest.mark.parametrize(
    ("start", "end", "covered"),
    [
        (START, END, True),
        (datetime(2005, 8, 29), datetime(2005, 9, 5), True),
        (datetime(2005, 8, 21), datetime(2005, 9, 5), False),  # starts before the slice
        (datetime(2005, 8, 29), datetime(2005, 9, 19, 0, 0, 1), False),  # ends after it
        (None, datetime(2005, 9, 5), False),  # open start: the Dataset's first Log Entry
        (datetime(2005, 8, 29), None, False),  # open end: the Dataset's last Log Entry
        (None, None, False),
    ],
)
def test_a_scope_is_covered_only_when_its_whole_range_lies_inside_the_slice(
    start: datetime | None, end: datetime | None, covered: bool
) -> None:
    assert wave().covers(start, end) is covered


# ----------------------------------------------------------------- registry


def test_a_registered_index_is_found_for_a_covered_scope() -> None:
    agent_indexes.register(wave())

    assert agent_indexes.covering("slm", "bgl", datetime(2005, 9, 1), datetime(2005, 9, 2)) == wave()


def test_the_registry_is_per_schema_and_dataset() -> None:
    agent_indexes.register(wave())

    assert agent_indexes.covering("slm_t10", "bgl", START, END) is None
    assert agent_indexes.covering("slm", "openstack", START, END) is None
    assert agent_indexes.covering("slm", "bgl", datetime(2005, 6, 1), END) is None


def test_registering_a_second_index_for_a_dataset_replaces_the_first() -> None:
    agent_indexes.register(wave())
    later = wave(name="agent_idx_bgl_ffffffff")

    agent_indexes.register(later)

    assert agent_indexes.registered("slm", "bgl") == later
    assert agent_indexes.registered_all() == [later]


def test_unregister_removes_only_the_named_index() -> None:
    agent_indexes.register(wave())

    assert agent_indexes.unregister("slm", "agent_idx_bgl_ffffffff") is None
    assert agent_indexes.unregister("slm", "agent_idx_bgl_0a1b2c3d") == wave()
    assert agent_indexes.registered("slm", "bgl") is None


# ----------------------------------------------------------------- use capture


def test_capture_collects_each_used_index_once() -> None:
    with agent_indexes.capture_use() as used:
        agent_indexes.record_use(wave())
        agent_indexes.record_use(wave())

    assert used == [wave()]


def test_a_use_outside_a_capture_is_ignored() -> None:
    agent_indexes.record_use(wave())

    with agent_indexes.capture_use() as used:
        pass

    assert used == []


def test_captures_nest_without_leaking_into_each_other() -> None:
    with agent_indexes.capture_use() as outer:
        with agent_indexes.capture_use() as inner:
            agent_indexes.record_use(wave())
        agent_indexes.record_use(wave(dataset="openstack", name="agent_idx_openstack_0a1b2c3d"))

    assert [index.dataset for index in inner] == ["bgl"]
    assert [index.dataset for index in outer] == ["openstack"]


def test_the_routing_note_names_the_index_its_slice_and_size() -> None:
    note = wave().routing_note()

    assert "slm.agent_idx_bgl_0a1b2c3d" in note
    assert "2005-08-22T00:00:00" in note
    assert "2005-09-19T00:00:00" in note
    assert "310,868" in note
    assert "drop_index" in note
