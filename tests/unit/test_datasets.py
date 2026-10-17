"""The Dataset registry and the resolver relation(dataset, role, scope) (plan section 2.7)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from psycopg import sql

from serenedb_log_mcp import config
from serenedb_log_mcp.config import ROLES, load_settings
from serenedb_log_mcp.corpus import agent_indexes, datasets
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex
from serenedb_log_mcp.corpus.datasets import (
    DATASET_NAMES,
    DATASETS,
    DatasetName,
    UnknownColumnError,
    UnknownDatasetError,
    relation,
)
from serenedb_log_mcp.evidence import DataScope

SLM = load_settings({})


def text(composable: sql.Composable) -> str:
    return composable.as_string()


def test_bgl_resolves_to_the_canonical_table_and_index_in_slm_schema() -> None:
    rel = relation("bgl", settings=SLM)

    assert text(rel.table) == '"slm"."bgl_entry"'
    assert text(rel.index) == '"slm"."bgl_entry_idx"'
    assert rel.table_name == "slm.bgl_entry"
    assert rel.index_name == "slm.bgl_entry_idx"
    assert rel.dataset.name == "bgl"


def test_openstack_resolves_to_os_entry() -> None:
    rel = relation("openstack", settings=SLM)

    assert text(rel.table) == '"slm"."os_entry"'
    assert text(rel.index) == '"slm"."os_entry_idx"'


def test_the_schema_comes_from_slm_schema() -> None:
    rel = relation("bgl", settings=load_settings({"SLM_SCHEMA": "slm_t10"}))

    assert rel.table_name == "slm_t10.bgl_entry"
    assert text(rel.ownership_table) == '"slm_t10"."ownership"'


def test_without_explicit_settings_the_process_settings_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings({"SLM_SCHEMA": "slm_process"}))

    assert relation("bgl").table_name == "slm_process.bgl_entry"


def test_shared_tables_live_in_the_same_schema() -> None:
    rel = relation("openstack", settings=SLM)

    assert text(rel.ownership_table) == '"slm"."ownership"'
    assert text(rel.pattern_dict_table) == '"slm"."pattern_dict"'
    assert text(rel.patterns_table) == '"slm"."pattern"'
    assert text(rel.incidents_table) == '"slm"."incident"'
    assert text(rel.instances_table) == '"slm"."os_instance"'


@pytest.mark.parametrize("name", ["BGL", " bgl ", "OpenStack"])
def test_dataset_names_are_case_insensitive(name: str) -> None:
    assert relation(name, settings=SLM).dataset.name == name.strip().lower()


def test_an_unknown_dataset_names_the_valid_ones_and_a_close_match() -> None:
    with pytest.raises(UnknownDatasetError) as excinfo:
        relation("openstak", settings=SLM)

    message = str(excinfo.value)
    assert "'openstak'" in message
    assert "bgl, openstack" in message
    assert "did you mean 'openstack'" in message
    assert isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize("role", [*ROLES, None])
def test_every_role_resolves_to_the_same_relation_for_now(role: config.RoleName | None) -> None:
    # T22 gives each Role its own views; until then the Role does not change the relation.
    assert relation("bgl", role, settings=SLM) == relation("bgl", settings=SLM)


def test_an_unknown_role_is_an_error() -> None:
    with pytest.raises(ValueError, match="engineer, team_lead, manager"):
        relation("bgl", "ceo", settings=SLM)  # type: ignore[arg-type]


def test_a_scope_without_a_time_range_does_not_change_the_relation() -> None:
    assert relation("bgl", "engineer", object(), settings=SLM) == relation("bgl", settings=SLM)


# ----------------------------------------------------------------- agent index routing (T19)

WAVE = AgentIndex(
    schema="slm",
    name="agent_idx_bgl_0a1b2c3d",
    dataset="bgl",
    time_from=datetime(2005, 8, 22),
    time_to=datetime(2005, 9, 19),
    rows=310_868,
)


@pytest.fixture
def wave_index() -> Iterator[AgentIndex]:
    agent_indexes.clear()
    agent_indexes.register(WAVE)
    yield WAVE
    agent_indexes.clear()


def scope(time_from: str | None, time_to: str | None, dataset: str = "bgl") -> DataScope:
    return DataScope(datasets=[dataset], time_from=time_from, time_to=time_to)


def test_a_scope_inside_an_agent_index_slice_reads_that_index(wave_index: AgentIndex) -> None:
    rel = relation("bgl", "engineer", scope("2005-08-29T00:00:00", "2005-09-05T00:00:00"), settings=SLM)

    assert text(rel.index) == '"slm"."agent_idx_bgl_0a1b2c3d"'
    assert rel.index_name == "slm.agent_idx_bgl_0a1b2c3d"
    assert rel.agent_index == wave_index
    # Point lookups, plain filters and aggregations still read the canonical table.
    assert text(rel.table) == '"slm"."bgl_entry"'


@pytest.mark.parametrize(
    ("time_from", "time_to"),
    [
        ("2005-08-21T00:00:00", "2005-09-05T00:00:00"),  # starts before the slice
        ("2005-08-29T00:00:00", "2005-09-20T00:00:00"),  # ends after it
        (None, "2005-09-05T00:00:00"),  # from the Dataset's first Log Entry
        ("2005-08-29T00:00:00", None),  # to the Dataset's last Log Entry
    ],
)
def test_a_scope_reaching_outside_the_slice_reads_the_full_index(
    wave_index: AgentIndex, time_from: str | None, time_to: str | None
) -> None:
    rel = relation("bgl", "engineer", scope(time_from, time_to), settings=SLM)

    assert rel.index_name == "slm.bgl_entry_idx"
    assert rel.agent_index is None


def test_without_a_scope_the_full_index_answers_even_with_an_agent_index(wave_index: AgentIndex) -> None:
    assert relation("bgl", settings=SLM).index_name == "slm.bgl_entry_idx"


def test_an_agent_index_serves_only_its_own_dataset_and_schema(wave_index: AgentIndex) -> None:
    inside = ("2005-08-29T00:00:00", "2005-09-05T00:00:00")

    assert relation("openstack", scope=scope(*inside, "openstack"), settings=SLM).index_name == (
        "slm.os_entry_idx"
    )
    other = load_settings({"SLM_SCHEMA": "slm_t10"})
    assert relation("bgl", scope=scope(*inside), settings=other).index_name == "slm_t10.bgl_entry_idx"


def test_reading_the_routed_index_records_its_use(wave_index: AgentIndex) -> None:
    rel = relation("bgl", scope=scope("2005-08-29T00:00:00", "2005-09-05T00:00:00"), settings=SLM)

    with agent_indexes.capture_use() as before_index:
        _ = rel.table
    with agent_indexes.capture_use() as used:
        _ = rel.index

    assert before_index == []
    assert used == [wave_index]


def test_the_full_index_records_no_use(wave_index: AgentIndex) -> None:
    with agent_indexes.capture_use() as used:
        _ = relation("bgl", settings=SLM).index

    assert used == []


def test_the_text_dictionary_lives_next_to_the_tables() -> None:
    assert text(relation("bgl", settings=SLM).text_dictionary) == '"slm"."log_text"'


# ----------------------------------------------------------------- columns


@pytest.mark.parametrize(
    ("dataset", "concept", "column"),
    [
        ("bgl", "time", "ts"),
        ("bgl", "epoch", "epoch_utc"),
        ("bgl", "source", "node"),
        ("bgl", "alert_category", "alert_tag"),
        ("bgl", "pattern", "template_id"),
        ("bgl", "rack", "rack"),
        ("openstack", "time", "ts"),
        ("openstack", "source", "instance_ref"),
        ("openstack", "pattern", "template_id"),
        ("openstack", "endpoint", "endpoint"),
    ],
)
def test_concepts_map_to_columns(dataset: str, concept: str, column: str) -> None:
    rel = relation(dataset, settings=SLM)

    assert rel.columns[concept] == column
    assert text(rel.column(concept)) == f'"{column}"'


def test_every_canonical_column_maps_to_itself() -> None:
    for dataset in DATASETS.values():
        for column in dataset.table_columns:
            assert dataset.columns[column] == column


def test_a_column_the_dataset_lacks_names_the_available_ones() -> None:
    rel = relation("openstack", settings=SLM)

    with pytest.raises(UnknownColumnError) as excinfo:
        rel.column("rack")

    message = str(excinfo.value)
    assert "openstack has no column 'rack'" in message
    assert "instance_ref" in message
    assert isinstance(excinfo.value, ValueError)


def test_the_canonical_columns_follow_plan_section_2_2() -> None:
    assert DATASETS["bgl"].table_columns == (
        "entry_id",
        "ts",
        "epoch_utc",
        "node",
        "rack",
        "midplane",
        "nodecard",
        "component",
        "level",
        "alert_tag",
        "message",
        "template_id",
        "family_id",
        "pattern_key",
    )
    assert DATASETS["openstack"].table_columns == (
        "entry_id",
        "ts",
        "epoch_utc",
        "source_id",
        "log_file",
        "pid",
        "component",
        "level",
        "request_id",
        "instance_id",
        "instance_ref",
        "endpoint",
        "http_status",
        "latency_s",
        "message",
        "template_id",
        "family_id",
        "pattern_key",
    )


def test_each_dataset_says_how_to_read_its_timestamps() -> None:
    assert "US Pacific" in DATASETS["bgl"].time_note
    assert "epoch_utc" in DATASETS["bgl"].time_note
    assert "UTC" in DATASETS["openstack"].time_note


def test_each_dataset_names_the_time_zone_of_ts() -> None:
    # Machine-readable, so the Data Scope and tool descriptions need not hard-code it (#48).
    assert DATASETS["bgl"].ts_zone == "America/Los_Angeles"
    assert DATASETS["openstack"].ts_zone == "UTC"


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_the_time_zone_of_ts_is_a_known_iana_zone(name: DatasetName) -> None:
    assert ZoneInfo(DATASETS[name].ts_zone).key == DATASETS[name].ts_zone


def test_the_bgl_zone_has_daylight_saving_time_over_the_corpus() -> None:
    zone = ZoneInfo(DATASETS["bgl"].ts_zone)

    assert datetime(2005, 6, 3, 15, 42, tzinfo=zone).utcoffset() == timedelta(hours=-7)
    assert datetime(2006, 1, 4, 8, 0, tzinfo=zone).utcoffset() == timedelta(hours=-8)


def test_ts_is_local_tells_a_local_ts_from_a_utc_one() -> None:
    assert DATASETS["bgl"].ts_is_local is True
    assert DATASETS["openstack"].ts_is_local is False


def test_the_ownership_table_is_flagged_as_a_stub() -> None:
    assert datasets.OWNERSHIP_IS_STUB is True
    assert "TODO(organiser)" in datasets.OWNERSHIP_NOTE
    assert datasets.OWNERSHIP_STUB_SOURCE == "stub"


def test_the_registry_lists_every_canonical_table_and_index_name() -> None:
    tables, indexes = datasets.CANONICAL_TABLES, datasets.CANONICAL_INDEXES

    assert tables == {
        "bgl_entry",
        "os_entry",
        "pattern_dict",
        "pattern",
        "incident",
        "os_instance",
        "ownership",
    }
    assert indexes == {"bgl_entry_idx", "os_entry_idx"}
