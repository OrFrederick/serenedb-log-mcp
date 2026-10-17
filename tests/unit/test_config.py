"""Settings come from the environment variables in plan section 2.5."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from serenedb_log_mcp import config
from serenedb_log_mcp.config import ConfigError, Settings, load_settings


def test_defaults_when_environment_is_empty() -> None:
    s = load_settings({})

    assert s.database_url == "host=localhost port=7890 dbname=postgres user=postgres"
    assert s.slm_schema == "slm"
    assert s.raw_schema == "public"
    assert s.data_dir == Path("data")
    assert s.query_timeout_s == 15.0
    assert s.client_binding is False
    assert s.ledger_path is None
    assert s.export_dir == Path("exports")
    assert s.pattern_tier1 is True
    assert s.role is None


def test_reads_every_variable_from_the_environment() -> None:
    env = {
        "DATABASE_URL": "host=db port=1 dbname=x user=y",
        "DATABASE_URL_ENGINEER": "host=db user=eng",
        "DATABASE_URL_TEAM_LEAD": "host=db user=lead",
        "DATABASE_URL_MANAGER": "host=db user=mgr",
        "SLM_SCHEMA": "slm_t10",
        "SLM_RAW_SCHEMA": "scratch_raw",
        "SLM_DATA_DIR": "/srv/data",
        "SLM_QUERY_TIMEOUT_S": "2.5",
        "SLM_CLIENT_BINDING": "1",
        "LEDGER_PATH": "/tmp/ledger.jsonl",
        "SLM_EXPORT_DIR": "/tmp/exports",
        "SLM_PATTERN_TIER1": "0",
    }

    s = load_settings(env)

    assert s.database_url == "host=db port=1 dbname=x user=y"
    assert s.database_url_for("engineer") == "host=db user=eng"
    assert s.database_url_for("team_lead") == "host=db user=lead"
    assert s.database_url_for("manager") == "host=db user=mgr"
    assert s.slm_schema == "slm_t10"
    assert s.raw_schema == "scratch_raw"
    assert s.data_dir == Path("/srv/data")
    assert s.query_timeout_s == 2.5
    assert s.client_binding is True
    assert s.ledger_path == Path("/tmp/ledger.jsonl")
    assert s.export_dir == Path("/tmp/exports")
    assert s.pattern_tier1 is False


def test_role_database_url_falls_back_to_database_url() -> None:
    s = load_settings({"DATABASE_URL": "host=shared", "DATABASE_URL_MANAGER": "host=mgr"})

    assert s.database_url_for("engineer") == "host=shared"
    assert s.database_url_for("team_lead") == "host=shared"
    assert s.database_url_for("manager") == "host=mgr"


def test_empty_values_count_as_unset() -> None:
    s = load_settings({"DATABASE_URL": "", "LEDGER_PATH": "", "SLM_SCHEMA": ""})

    assert s.database_url == "host=localhost port=7890 dbname=postgres user=postgres"
    assert s.ledger_path is None
    assert s.slm_schema == "slm"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_flags(value: str) -> None:
    assert load_settings({"SLM_CLIENT_BINDING": value}).client_binding is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_falsy_flags(value: str) -> None:
    assert load_settings({"SLM_PATTERN_TIER1": value}).pattern_tier1 is False


@pytest.mark.parametrize("value", ["maybe", "2"])
def test_invalid_flag_names_the_variable(value: str) -> None:
    with pytest.raises(ConfigError, match="SLM_CLIENT_BINDING"):
        load_settings({"SLM_CLIENT_BINDING": value})


@pytest.mark.parametrize("variable", ["SLM_SCHEMA", "SLM_RAW_SCHEMA"])
@pytest.mark.parametrize("value", ["Slm", "1slm", "slm-x", "slm;drop", "public.x"])
def test_schemas_must_be_plain_lowercase_identifiers(variable: str, value: str) -> None:
    with pytest.raises(ConfigError, match=variable):
        load_settings({variable: value})


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_query_timeout_must_be_a_positive_number(value: str) -> None:
    with pytest.raises(ConfigError, match="SLM_QUERY_TIMEOUT_S"):
        load_settings({"SLM_QUERY_TIMEOUT_S": value})


def test_role_pin_is_an_override_not_an_environment_variable() -> None:
    s = load_settings({}, role="manager")

    assert s.role == "manager"


def test_settings_are_immutable() -> None:
    s = load_settings({})

    with pytest.raises(ValueError, match="frozen"):
        s.slm_schema = "other"  # type: ignore[misc]


def test_get_settings_reads_the_process_environment_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", None)
    monkeypatch.setenv("SLM_SCHEMA", "slm_from_env")

    first = config.get_settings()
    monkeypatch.setenv("SLM_SCHEMA", "changed_later")

    assert first.slm_schema == "slm_from_env"
    assert config.get_settings() is first


def test_set_settings_replaces_the_active_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", None)
    pinned = Settings(role="engineer")

    config.set_settings(pinned)

    assert config.get_settings() is pinned


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
def test_query_timeout_must_be_finite(value: str) -> None:
    with pytest.raises(ConfigError, match="SLM_QUERY_TIMEOUT_S"):
        load_settings({"SLM_QUERY_TIMEOUT_S": value})


def test_unknown_settings_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Settings(no_such_field=1)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "fields",
    [
        {"slm_schema": "Bad-x"},
        {"raw_schema": "x;y"},
        {"query_timeout_s": 0},
        {"query_timeout_s": float("nan")},
        {"query_timeout_s": float("inf")},
    ],
)
def test_settings_validate_fields_however_they_are_built(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**fields)  # type: ignore[arg-type]


def test_overrides_are_validated_and_named_in_the_error() -> None:
    with pytest.raises(ConfigError, match="slm_schema"):
        load_settings({}, slm_schema="Bad-x")


def test_passwords_stay_out_of_repr_and_str() -> None:
    s = load_settings(
        {
            "DATABASE_URL": "host=db user=u password=secret1",
            "DATABASE_URL_MANAGER": "host=db user=m password=secret2",
        }
    )

    for text in (repr(s), str(s)):
        assert "secret1" not in text
        assert "secret2" not in text
    assert s.database_url_for("manager") == "host=db user=m password=secret2"


def test_role_database_urls_cannot_be_changed() -> None:
    s = load_settings({"DATABASE_URL_MANAGER": "host=mgr"})

    with pytest.raises(TypeError):
        s.role_database_urls["engineer"] = "host=other"  # type: ignore[index]
    assert s.database_url_for("engineer") == s.database_url


@pytest.mark.filterwarnings("error")
def test_settings_still_serialise() -> None:
    s = load_settings({"DATABASE_URL_MANAGER": "host=mgr"})

    assert s.model_dump()["role_database_urls"] == {"manager": "host=mgr"}
    assert '"role_database_urls":{"manager":"host=mgr"}' in s.model_dump_json()
