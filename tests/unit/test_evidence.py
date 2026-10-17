"""The Evidence contract: Role, Data Scope and the Evidence envelope (plan section 2.3)."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from serenedb_log_mcp import config
from serenedb_log_mcp.evidence import DataScope, Evidence, Role, ScopeFilters


def evidence(**overrides: Any) -> Evidence:
    """A complete Evidence record; each test overrides only the fields it is about."""
    fields: dict[str, Any] = {
        "evidence_id": "E1",
        "tool": "count_by",
        "role": Role.TEAM_LEAD,
        "sql": ["SELECT alert_tag, count(*) FROM bgl GROUP BY 1 ORDER BY 2 DESC LIMIT %s"],
        "params": [[25]],
        "data_scope": DataScope(datasets=["bgl"]),
        "total_matches": 3,
        "returned": 3,
        "truncated": False,
        "query_ms": 126.2,
        "columns": ["alert_tag", "entries"],
        "rows": [["KERNDTLB", 152734], ["KERNSTOR", 63491], ["APPSEV", 49651]],
    }
    return Evidence(**(fields | overrides))


def test_the_role_names_have_exactly_one_definition() -> None:
    # config owns the Role enum and evidence re-exports it, so the two can never drift apart.
    assert Role is config.Role
    assert config.RoleName is Role
    assert [role.value for role in Role] == list(config.ROLES) == ["engineer", "team_lead", "manager"]
    # StrEnum, so a Role compares equal to the string the client sends.
    assert Role.ENGINEER == "engineer"
    assert Role("team_lead") is Role.TEAM_LEAD


def test_data_scope_records_the_resolved_and_the_requested_time_range() -> None:
    scope = DataScope(
        datasets=["bgl", "openstack"],
        time_from="2005-12-28T00:00:00",
        time_to="2006-01-04T00:00:00",
        time_input="last 7d",
        resolved_against="bgl corpus end 2006-01-04T00:00:00",
    )

    dumped = scope.model_dump(mode="json")

    assert dumped["datasets"] == ["bgl", "openstack"]
    assert dumped["time_from"] == "2005-12-28T00:00:00"
    assert dumped["time_input"] == "last 7d"
    assert dumped["resolved_against"] == "bgl corpus end 2006-01-04T00:00:00"


def test_a_data_scope_without_a_dataset_is_rejected() -> None:
    # "Never empty: every number is scoped to named Datasets" is part of the description, so the
    # model has to enforce it.
    with pytest.raises(ValidationError, match="at least 1 item"):
        DataScope(datasets=[])

    assert "minItems" in str(DataScope.model_json_schema()["properties"]["datasets"])


def test_data_scope_filters_include_the_shared_pattern_filters() -> None:
    scope = DataScope(
        datasets=["bgl"],
        filters=ScopeFilters(pattern_ids=["0a1b2c3d4e5f6071"], family_id="ciod_failure"),
    )

    assert scope.filters.pattern_ids == ["0a1b2c3d4e5f6071"]
    assert scope.filters.family_id == "ciod_failure"
    assert scope.model_dump(mode="json")["filters"]["pattern_ids"] == ["0a1b2c3d4e5f6071"]


def test_scope_filters_keep_tool_specific_filters() -> None:
    filters = ScopeFilters.of(query="ciod failed", components=["KERNEL"], family_id="ciod_failure")

    dumped = filters.model_dump(mode="json")

    assert dumped["query"] == "ciod failed"
    assert dumped["components"] == ["KERNEL"]
    assert dumped["family_id"] == "ciod_failure"
    assert dumped["pattern_ids"] is None


def test_an_empty_data_scope_still_names_its_datasets() -> None:
    with pytest.raises(ValidationError, match="datasets"):
        DataScope()  # type: ignore[call-arg]


def test_truncated_must_be_true_when_fewer_rows_came_back_than_matched() -> None:
    with pytest.raises(ValidationError, match="truncated"):
        evidence(total_matches=152_734, returned=20, truncated=False)


def test_truncated_must_be_false_when_every_match_came_back() -> None:
    with pytest.raises(ValidationError, match="truncated"):
        evidence(total_matches=3, returned=3, truncated=True)


def test_a_truncated_result_is_accepted() -> None:
    record = evidence(total_matches=152_734, returned=20, truncated=True, rows=[["KERNDTLB", 1]])

    assert record.truncated is True
    # The Role layer may withhold the rows, so `returned` counts rows, it does not mirror them.
    assert record.returned == 20


def test_evidence_id_must_be_a_citable_id() -> None:
    for bad in ("1", "E0", "E01", "e1", "[E1]", ""):
        with pytest.raises(ValidationError, match="evidence_id"):
            evidence(evidence_id=bad)


def test_unknown_envelope_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="total_count"):
        evidence(total_count=3)


def test_the_role_layer_fields_default_to_empty() -> None:
    record = evidence()

    assert record.summary == {}
    assert record.notes == []
    assert record.answer_frame == []
    assert record.not_measured == []


def test_the_envelope_round_trips_through_json() -> None:
    record = evidence(
        summary={"entries_total": 265_876, "share_pct": 57.4},
        notes=["Truncated: 20 of 152,734 returned."],
        answer_frame=["Observed Facts", "Hypotheses", "Unknowns", "Next Step"],
        not_measured=["financial loss", "customer impact"],
    )

    again = Evidence.model_validate(record.model_dump(mode="json"))

    assert again == record


def test_the_envelope_is_frozen_so_the_stored_record_cannot_be_edited_in_place() -> None:
    record = evidence()

    with pytest.raises(ValidationError):
        record.returned = 1


@pytest.mark.parametrize("model", [Evidence, DataScope, ScopeFilters])
def test_every_field_describes_itself_for_the_agent(model: type[BaseModel]) -> None:
    undocumented = [name for name, field in model.model_fields.items() if not field.description]

    assert undocumented == []
