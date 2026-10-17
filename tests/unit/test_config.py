"""Settings come from the environment variables in plan section 2.5."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from serenedb_log_mcp import config
from serenedb_log_mcp.config import ConfigError, Settings, error_detail, load_settings, safe_dsn


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
    assert s.reconnect_wait_s == 30.0
    assert s.max_idle_connections == 4
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
        "SLM_RECONNECT_WAIT_S": "45",
        "SLM_MAX_IDLE_CONNECTIONS": "2",
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
    assert s.reconnect_wait_s == 45.0
    assert s.max_idle_connections == 2


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


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan", "inf"])
def test_reconnect_wait_must_be_a_finite_positive_number(value: str) -> None:
    with pytest.raises(ConfigError, match="SLM_RECONNECT_WAIT_S"):
        load_settings({"SLM_RECONNECT_WAIT_S": value})


def test_zero_idle_connections_turns_the_pool_off() -> None:
    assert load_settings({"SLM_MAX_IDLE_CONNECTIONS": "0"}).max_idle_connections == 0


@pytest.mark.parametrize("value", ["abc", "-1", "2.5", "1e3"])
def test_max_idle_connections_must_be_a_whole_number_of_at_least_zero(value: str) -> None:
    with pytest.raises(ConfigError, match="SLM_MAX_IDLE_CONNECTIONS"):
        load_settings({"SLM_MAX_IDLE_CONNECTIONS": value})


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
        {"reconnect_wait_s": 0},
        {"reconnect_wait_s": float("nan")},
        {"max_idle_connections": -1},
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


@pytest.mark.parametrize("value", ["host=db port", "host='db", "postgresql://u@h:7890/db?bad_option=1"])
def test_malformed_database_url_names_the_variable(value: str) -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_settings({"DATABASE_URL": value})


def test_malformed_role_database_url_names_the_role_variable() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL_MANAGER"):
        load_settings({"DATABASE_URL_MANAGER": "host=db port"})


def test_database_url_error_never_echoes_the_value() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_settings({"DATABASE_URL": "host=db password=hunter2 port"})

    assert "hunter2" not in str(exc_info.value)


def test_uri_database_url_is_accepted() -> None:
    assert load_settings({"DATABASE_URL": "postgresql://postgres@localhost:7890/postgres"}).database_url


@pytest.mark.parametrize(
    ("dsn", "secret"),
    [
        ("host=h dbname=d password=hunter2", "hunter2"),
        ("host=h dbname=d sslpassword=keysecret", "keysecret"),
        ("postgresql://bob:hunter2@db.example:7890/postgres", "hunter2"),
        # libpq refuses this one and quotes the offending fragment, which is the password itself.
        ("postgres://bob:hun ter2@h/d", "hun ter2"),
    ],
)
def test_safe_dsn_never_shows_a_secret(dsn: str, secret: str) -> None:
    assert secret not in safe_dsn(dsn)


def test_safe_dsn_keeps_the_connection_target() -> None:
    out = safe_dsn("host=db.example port=7890 dbname=postgres user=bob password=hunter2")

    assert "host=db.example" in out
    assert "port=7890" in out
    assert "dbname=postgres" in out
    assert "user=bob" in out
    assert "password" not in out


def test_safe_dsn_reports_the_parse_error_without_the_value() -> None:
    out = safe_dsn("host=h nonsensetoken")

    assert "nonsensetoken" not in out
    assert out.startswith("<unparseable DSN:")
    assert "connection info string" in out


def test_safe_dsn_drops_the_parse_error_when_a_secret_could_still_show() -> None:
    # The password is a single character, so redacting the quoted fragments is not enough: any
    # English word in the libpq message contains an "a". The helper then reports the class only.
    assert safe_dsn("password=a nonsensetoken") == "<unparseable DSN: ProgrammingError>"


def test_safe_dsn_of_an_empty_dsn_says_so() -> None:
    assert safe_dsn("") == "<empty DSN>"


def test_error_detail_is_the_first_line_or_the_class_name() -> None:
    assert error_detail(ValueError("one\ntwo")) == "one"
    assert error_detail(ValueError("")) == "ValueError"


WARN_LOGGER = "serenedb_log_mcp.config"


def test_an_unknown_database_url_variable_is_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    # A misspelled DATABASE_URL_LEAD was silently ignored before, which hides an access-scoping
    # mistake: the Role keeps using the shared DATABASE_URL.
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        settings = load_settings({"DATABASE_URL_LEAD": "host=h dbname=d"})

    assert dict(settings.role_database_urls) == {}
    assert "DATABASE_URL_LEAD" in caplog.text
    assert "DATABASE_URL_ENGINEER" in caplog.text


def test_a_role_variable_in_the_wrong_case_is_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    # Only the upper-case spelling is read, so the lower-case one is ignored just like a typo.
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        settings = load_settings({"DATABASE_URL_engineer": "host=h dbname=d"})

    assert dict(settings.role_database_urls) == {}
    assert "DATABASE_URL_engineer" in caplog.text


def test_the_known_variables_are_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        settings = load_settings(
            {"DATABASE_URL": "host=a dbname=d", "DATABASE_URL_MANAGER": "host=b dbname=d"}
        )

    assert settings.database_url_for("manager") == "host=b dbname=d"
    assert caplog.text == ""


def test_the_unknown_variable_warning_never_echoes_the_dsn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        load_settings({"DATABASE_URL_LEAD": "host=h dbname=d password=hunter2"})

    assert "hunter2" not in caplog.text
    assert "host=h" not in caplog.text


def test_an_empty_unknown_variable_is_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    # An unset variable arrives as the empty string in some shells; that is not a misconfiguration.
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        load_settings({"DATABASE_URL_LEAD": "  "})

    assert caplog.text == ""
