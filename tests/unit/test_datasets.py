"""The Dataset registry and the resolver relation(dataset, role, scope) (plan section 2.7)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from psycopg import sql

from serenedb_log_mcp import config
from serenedb_log_mcp.config import ROLES, Role, load_settings
from serenedb_log_mcp.corpus import datasets
from serenedb_log_mcp.corpus.datasets import (
    DATASET_NAMES,
    DATASETS,
    DatasetName,
    RoleAccessError,
    UnknownColumnError,
    UnknownDatasetError,
    relation,
)

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
    assert text(rel.patterns_index) == '"slm"."pattern_idx"'
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
def test_without_a_role_dsn_every_role_resolves_to_the_canonical_tables(role: config.RoleName | None) -> None:
    # T22's per-Role views apply only to a Role whose DATABASE_URL_<ROLE> is set.
    assert relation("bgl", role, settings=SLM) == relation("bgl", settings=SLM)


def test_an_unknown_role_is_an_error() -> None:
    with pytest.raises(ValueError, match="engineer, team_lead, manager"):
        relation("bgl", "ceo", settings=SLM)  # type: ignore[arg-type]


def test_a_scope_is_accepted_and_does_not_change_the_relation_yet() -> None:
    # T19 routes a Data Scope inside an agent index's slice to that index; until then it is ignored.
    assert relation("bgl", "engineer", object(), settings=SLM) == relation("bgl", settings=SLM)


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
    assert indexes == {"bgl_entry_idx", "os_entry_idx", "pattern_idx"}


# ----------------------------------------------------------------- per-Role views (T22)

ROLE_DSNS = {
    "DATABASE_URL_ENGINEER": "host=db user=slm_engineer password=e",
    "DATABASE_URL_TEAM_LEAD": "host=db user=slm_team_lead password=t",
    "DATABASE_URL_MANAGER": "host=db user=slm_manager password=m",
}
WITH_LOGINS = load_settings(ROLE_DSNS)


def test_role_schema_names_the_schema_of_a_roles_views() -> None:
    assert datasets.role_schema("slm", Role.ENGINEER) == "slm_engineer"
    assert datasets.role_schema("slm", Role.TEAM_LEAD) == "slm_team_lead"
    assert datasets.role_schema("scratch_x", Role.MANAGER) == "scratch_x_manager"


@pytest.mark.parametrize("role", ROLES)
def test_a_role_with_its_own_dsn_resolves_to_its_views(role: Role) -> None:
    rel = relation("bgl", role, settings=WITH_LOGINS)

    assert rel.schema == f"slm_{role.value}"
    assert rel.table_name == f"slm_{role.value}.bgl_entry"
    assert rel.views_for is role
    assert text(rel.ownership_table) == f'"slm_{role.value}"."ownership"'
    assert text(rel.patterns_table) == f'"slm_{role.value}"."pattern"'
    assert text(rel.instances_table) == f'"slm_{role.value}"."os_instance"'


def test_only_the_roles_with_a_dsn_get_views() -> None:
    settings = load_settings({"DATABASE_URL_MANAGER": ROLE_DSNS["DATABASE_URL_MANAGER"]})

    assert relation("bgl", "manager", settings=settings).table_name == "slm_manager.bgl_entry"
    assert relation("bgl", "engineer", settings=settings).table_name == "slm.bgl_entry"
    assert relation("bgl", None, settings=settings).table_name == "slm.bgl_entry"
    assert relation("bgl", "engineer", settings=settings).views_for is None


def test_the_role_views_follow_slm_schema() -> None:
    settings = load_settings({**ROLE_DSNS, "SLM_SCHEMA": "scratch_t22"})

    assert (
        relation("openstack", "team_lead", settings=settings).table_name == "scratch_t22_team_lead.os_entry"
    )


@pytest.mark.parametrize("role", [Role.ENGINEER, Role.TEAM_LEAD])
def test_the_engineer_and_the_team_lead_search_their_own_index(role: Role) -> None:
    rel = relation("bgl", role, settings=WITH_LOGINS)

    assert text(rel.index) == f'"slm_{role.value}"."bgl_entry_idx"'
    assert text(rel.column("message")) == '"message"'
    assert "message" in rel.columns


@pytest.mark.parametrize("dataset", DATASET_NAMES)
def test_the_managers_views_hold_no_log_entry_text(dataset: str) -> None:
    rel = relation(dataset, Role.MANAGER, settings=WITH_LOGINS)

    assert "message" not in rel.columns
    assert "message" not in rel.columns.values()
    assert text(rel.column("component")) == '"component"'
    assert text(rel.column("time")) == '"ts"'
    with pytest.raises(RoleAccessError) as excinfo:
        rel.column("message")
    message = str(excinfo.value)
    assert "'manager'" in message
    assert "no Log Entry text" in message
    assert "count_by" in message


@pytest.mark.parametrize("dataset", DATASET_NAMES)
def test_the_manager_has_no_full_text_index(dataset: str) -> None:
    rel = relation(dataset, Role.MANAGER, settings=WITH_LOGINS)

    with pytest.raises(RoleAccessError, match="full-text"):
        _ = rel.index


def test_the_manager_without_its_own_dsn_keeps_every_column() -> None:
    rel = relation("bgl", Role.MANAGER, settings=SLM)

    assert "message" in rel.columns
    assert text(rel.index) == '"slm"."bgl_entry_idx"'


def test_a_role_access_error_reaches_the_agent_as_a_tool_error() -> None:
    # Its text, not a generic retry hint, is what the agent reads (toolkit passes ToolError through).
    assert issubclass(RoleAccessError, ToolError)


def test_an_unknown_column_is_still_unknown_for_a_role() -> None:
    rel = relation("openstack", Role.MANAGER, settings=WITH_LOGINS)

    with pytest.raises(UnknownColumnError, match="openstack has no column 'rack'"):
        rel.column("rack")


def test_every_withheld_column_is_a_canonical_column() -> None:
    for role, withheld in datasets.ROLE_WITHHELD_COLUMNS.items():
        assert role in ROLES
        for dataset in DATASETS.values():
            assert withheld <= set(dataset.table_columns)
    assert datasets.ROLE_WITHHELD_COLUMNS[Role.MANAGER] == frozenset({"message"})
    assert Role.ENGINEER not in datasets.ROLE_WITHHELD_COLUMNS
    assert Role.TEAM_LEAD not in datasets.ROLE_WITHHELD_COLUMNS
