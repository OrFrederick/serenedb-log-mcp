"""scripts/access.py and sql/access/roles.sql (T22): per-Role views, LOGIN roles and GRANTs.

What the SQL does to a live server is tested in tests/db/access/; these tests check the rendered
statements and the configuration rules without a database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import access
import derive
from serenedb_log_mcp.config import Role, load_settings
from serenedb_log_mcp.corpus.datasets import DATASETS, ROLE_WITHHELD_COLUMNS, role_schema

ROOT = Path(__file__).parents[2]
DSNS = {
    "DATABASE_URL": "host=db port=7890 dbname=postgres user=postgres",
    "DATABASE_URL_ENGINEER": "host=db port=7890 dbname=postgres user=slm_engineer password=pw-e",
    "DATABASE_URL_TEAM_LEAD": "host=db port=7890 dbname=postgres user=slm_team_lead password=pw-t",
    "DATABASE_URL_MANAGER": "host=db port=7890 dbname=postgres user=slm_manager password=it's-m",
}


def statements(slm: str = "slm") -> list[str]:
    return access.statements(slm, access.logins(load_settings(DSNS)))


def normalised(statement: str) -> str:
    return " ".join(statement.split())


def plain(statement: str) -> str:
    """One line without identifier quotes (the SQL names `${role_schema}.bgl_entry`)."""
    return normalised(statement).replace('"', "")


# ----------------------------------------------------------------- the logins


def test_each_role_logs_in_as_the_user_and_password_of_its_dsn() -> None:
    found = access.logins(load_settings(DSNS))

    assert {role: login.user for role, login in found.items()} == {
        Role.ENGINEER: "slm_engineer",
        Role.TEAM_LEAD: "slm_team_lead",
        Role.MANAGER: "slm_manager",
    }
    assert found[Role.MANAGER].password == "it's-m"
    assert "it's-m" not in repr(found[Role.MANAGER])


@pytest.mark.parametrize(
    "missing", ["DATABASE_URL_ENGINEER", "DATABASE_URL_TEAM_LEAD", "DATABASE_URL_MANAGER"]
)
def test_every_role_needs_its_dsn(missing: str) -> None:
    env = {key: value for key, value in DSNS.items() if key != missing}

    with pytest.raises(access.AccessError, match=missing):
        access.logins(load_settings(env))


def test_a_login_needs_a_password() -> None:
    env = {**DSNS, "DATABASE_URL_MANAGER": "host=db user=slm_manager"}

    with pytest.raises(access.AccessError, match="DATABASE_URL_MANAGER has no password"):
        access.logins(load_settings(env))


@pytest.mark.parametrize("user", ["postgres", "manager", "SLM_manager", "slm-manager", "slm_"])
def test_a_login_must_be_a_dedicated_slm_role(user: str) -> None:
    # The script drops and re-creates the role, so it never touches one it does not own.
    env = {**DSNS, "DATABASE_URL_MANAGER": f"host=db user={user} password=x"}

    with pytest.raises(access.AccessError, match="slm_"):
        access.logins(load_settings(env))


def test_a_login_cannot_be_the_administrator_of_database_url() -> None:
    env = {
        **DSNS,
        "DATABASE_URL": "host=db user=slm_admin",
        "DATABASE_URL_MANAGER": "host=db user=slm_admin password=x",
    }

    with pytest.raises(access.AccessError, match="DATABASE_URL"):
        access.logins(load_settings(env))


def test_two_roles_cannot_share_a_login() -> None:
    env = {**DSNS, "DATABASE_URL_TEAM_LEAD": DSNS["DATABASE_URL_ENGINEER"]}

    with pytest.raises(access.AccessError, match="slm_engineer"):
        access.logins(load_settings(env))


# ----------------------------------------------------------------- the rendered SQL


def test_every_placeholder_is_filled() -> None:
    assert not any("${" in statement for statement in statements())


def test_passwords_are_quoted_sql_literals() -> None:
    creates = [s for s in statements() if s.startswith("CREATE ROLE")]

    assert creates == [
        "CREATE ROLE \"slm_engineer\" LOGIN PASSWORD 'pw-e'",
        "CREATE ROLE \"slm_team_lead\" LOGIN PASSWORD 'pw-t'",
        "CREATE ROLE \"slm_manager\" LOGIN PASSWORD 'it''s-m'",
    ]


def test_redact_hides_every_password() -> None:
    for statement in statements():
        assert "pw-e" not in access.redact(statement)
        assert "it''s-m" not in access.redact(statement)
    assert (
        access.redact("CREATE ROLE \"x\" LOGIN PASSWORD 'it''s'") == "CREATE ROLE \"x\" LOGIN PASSWORD '***'"
    )


def test_grants_name_only_the_roles_own_schema_and_one_object_each() -> None:
    grants = [plain(s) for s in statements() if s.startswith("GRANT")]

    assert grants
    for grant in grants:
        # SereneDB rejects GRANT ... ON a, b (measured on 26.10.1), and no login gets the base schema.
        assert re.fullmatch(r"GRANT (USAGE ON SCHEMA|SELECT ON) [a-z_]+(\.[a-z_]+)? TO slm_[a-z_]+", grant)
        assert " slm." not in grant
        assert "SCHEMA slm " not in grant
    for role in Role:
        own = role_schema("slm", role)
        login = f"slm_{role.value}"
        mine = [g for g in grants if g.endswith(f"TO {login}")]
        assert f"GRANT USAGE ON SCHEMA {own} TO {login}" in mine
        assert all(f" {own}" in g for g in mine)


VIEWS = ("bgl_entry", "os_entry", "ownership", "pattern", "pattern_dict", "os_instance", "incident")


@pytest.mark.parametrize("role", list(Role))
def test_each_role_gets_a_view_of_every_derived_table_and_select_on_it(role: Role) -> None:
    rendered = [plain(s) for s in statements()]
    own = role_schema("slm", role)

    for view in VIEWS:
        assert any(s.startswith(f"CREATE VIEW {own}.{view} AS SELECT ") for s in rendered), view
        assert f"GRANT SELECT ON {own}.{view} TO slm_{role.value}" in rendered


def view_columns(role: Role, view: str) -> list[str]:
    own = role_schema("slm", role)
    for statement in statements():
        text = plain(statement)
        if text.startswith(f"CREATE VIEW {own}.{view} AS SELECT "):
            columns = text.split(" AS SELECT ", 1)[1].split(" FROM ", 1)[0]
            return [column.strip() for column in columns.split(",")]
    raise AssertionError(f"no view {own}.{view}")


@pytest.mark.parametrize(("view", "dataset"), [("bgl_entry", "bgl"), ("os_entry", "openstack")])
def test_the_managers_entry_views_hold_every_canonical_column_but_the_withheld(
    view: str, dataset: str
) -> None:
    expected = [c for c in DATASETS[dataset].table_columns if c not in ROLE_WITHHELD_COLUMNS[Role.MANAGER]]  # type: ignore[index]

    assert view_columns(Role.MANAGER, view) == expected
    assert "message" not in view_columns(Role.MANAGER, view)


def test_the_managers_pattern_dictionary_view_has_no_message() -> None:
    assert "message" not in view_columns(Role.MANAGER, "pattern_dict")
    assert "template_id" in view_columns(Role.MANAGER, "pattern_dict")


@pytest.mark.parametrize("role", [Role.ENGINEER, Role.TEAM_LEAD])
def test_the_roles_that_read_text_see_every_column(role: Role) -> None:
    for view in VIEWS:
        assert view_columns(role, view) == ["*"]


def index_columns(text: str, index: str) -> list[str]:
    match = re.search(rf"CREATE INDEX {index} ON \S+ USING inverted \((.*?)\)", " ".join(text.split()))
    assert match, index
    return [column.strip() for column in match.group(1).split(",")]


@pytest.mark.parametrize("index", ["bgl_entry_idx", "os_entry_idx"])
def test_the_role_indexes_index_what_the_canonical_index_does(index: str) -> None:
    canonical = index_columns((ROOT / "sql" / "derive" / "90_indexes.sql").read_text(), index)
    rendered = [plain(s) for s in statements()]

    for role in (Role.ENGINEER, Role.TEAM_LEAD):
        own = role_schema("slm", role)
        [create] = [s for s in rendered if s.startswith(f"CREATE INDEX {index} ON {own}.")]
        assert index_columns(create, index) == [c.replace("${slm}", "slm") for c in canonical]


def test_the_manager_gets_no_full_text_index_over_log_entries() -> None:
    own = role_schema("slm", Role.MANAGER)
    indexes = [s for s in statements() if s.startswith("CREATE INDEX") and f" {own}." in plain(s)]

    # Only the Pattern catalogue's index: Pattern texts name the problems the Manager ranks.
    assert [plain(s).split(" ON ")[1].split(" ")[0] for s in indexes] == [f"{own}.pattern"]


def test_the_drop_statements_remove_the_views_and_the_logins_only() -> None:
    dropped = access.drop_statements("slm", access.logins(load_settings(DSNS)))

    assert dropped == [
        'DROP SCHEMA IF EXISTS "slm_engineer" CASCADE',
        'DROP SCHEMA IF EXISTS "slm_team_lead" CASCADE',
        'DROP SCHEMA IF EXISTS "slm_manager" CASCADE',
        'DROP ROLE IF EXISTS "slm_engineer"',
        'DROP ROLE IF EXISTS "slm_team_lead"',
        'DROP ROLE IF EXISTS "slm_manager"',
    ]


def test_the_views_follow_the_schema() -> None:
    rendered = "\n".join(plain(s) for s in statements("scratch_t22"))

    assert "CREATE SCHEMA scratch_t22_manager" in rendered
    assert "FROM scratch_t22.bgl_entry" in rendered
    assert " slm." not in rendered


def test_roles_sql_splits_into_statements() -> None:
    text = (ROOT / "sql" / "access" / "roles.sql").read_text()

    vector_table = access.vector_table_statements("slm")
    assert len(derive.split_statements(text)) + len(vector_table) == len(statements())


def test_the_vector_table_is_created_before_its_views() -> None:
    # A schema derived before find_patterns (T26) has no pattern_vector; make access creates it.
    rendered = statements()
    first_view = next(i for i, s in enumerate(rendered) if "pattern_vector AS SELECT" in s)
    assert rendered[0].startswith('CREATE TABLE IF NOT EXISTS "slm".pattern_vector')
    assert first_view > 0


# ----------------------------------------------------------------- make targets


def make(*args: str) -> str:
    import shutil
    import subprocess

    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    proc = subprocess.run(["make", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
    return proc.stdout


def test_make_help_lists_the_access_targets() -> None:
    out = make("help")

    assert "access " in out
    assert "access-check" in out
    assert "access-drop" in out


@pytest.mark.parametrize(
    ("target", "flag"),
    [("access", "scripts/access.py\n"), ("access-check", "--check"), ("access-drop", "--drop")],
)
def test_the_access_targets_run_the_script(target: str, flag: str) -> None:
    out = make("-n", target)

    assert "scripts/access.py" in out
    assert flag in out
