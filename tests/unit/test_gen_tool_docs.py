"""scripts/gen_tool_docs.py writes docs/tools.md from the live tool registry (T8)."""

from pathlib import Path
from typing import Any

import pytest
from mcp_types import Prompt, PromptArgument, Tool, ToolAnnotations

import gen_tool_docs
from gen_tool_docs import Registry, render, schema_limits, schema_type
from serenedb_log_mcp.config import get_settings, load_settings, set_settings
from serenedb_log_mcp.server import create_server

ROOT = Path(__file__).parents[2]
TOOLS_MD = ROOT / "docs" / "tools.md"

ROLE_DEFS = {"Role": {"enum": ["engineer", "team_lead", "manager"], "type": "string", "description": "x"}}


def tool(name: str = "search", **overrides: Any) -> Tool:
    fields: dict[str, Any] = {
        "name": name,
        "title": f"Title of {name}",
        "description": f"Purpose of {name}.\nInputs: role.\nOutputs: rows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"$ref": "#/$defs/Role", "description": "Who is asking."},
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Rows.",
                },
            },
            "required": ["role"],
            "$defs": ROLE_DEFS,
        },
        "annotations": ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        ),
        "meta": {"anthropic/alwaysLoad": True},
    }
    fields.update(overrides)
    return Tool(**fields)


def envelope(title: str = "Evidence") -> dict[str, Any]:
    return {
        "title": title,
        "type": "object",
        "properties": {
            "evidence_id": {"type": "string", "description": "Cite it as [E3]."},
            "data_scope": {"$ref": "#/$defs/DataScope", "description": "What it covered."},
        },
        "required": ["evidence_id"],
        "$defs": {
            "DataScope": {
                "type": "object",
                "properties": {
                    "datasets": {"type": "array", "items": {"type": "string"}, "description": "D."}
                },
            }
        },
    }


def registry(*tools: Tool, prompts: tuple[Prompt, ...] = (), instructions: str | None = "Hi.") -> Registry:
    return Registry(
        server_name="serenedb-log", version="9.9.9", instructions=instructions, tools=tools, prompts=prompts
    )


# ------------------------------------------------------------------ schema types and limits


def test_type_of_an_enum_names_every_value() -> None:
    assert schema_type({"type": "string", "enum": ["bgl", "openstack"]}, {}) == "one of `bgl`, `openstack`"


def test_type_follows_a_ref_into_the_defs() -> None:
    assert schema_type({"$ref": "#/$defs/Role"}, ROLE_DEFS) == "one of `engineer`, `team_lead`, `manager`"


def test_type_of_an_optional_value_says_it_may_be_null() -> None:
    assert schema_type({"anyOf": [{"type": "string"}, {"type": "null"}]}, {}) == "string or null"


def test_type_of_a_list_names_its_items() -> None:
    schema = {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}

    assert schema_type(schema, {}) == "list of string or null"


def test_type_of_an_object_without_a_type_is_any() -> None:
    assert schema_type({}, {}) == "any"


def test_limits_of_a_bounded_integer() -> None:
    assert schema_limits({"type": "integer", "minimum": 1, "maximum": 50}) == ["1 to 50"]


def test_limits_of_a_one_sided_number() -> None:
    assert schema_limits({"type": "number", "minimum": 0}) == ["at least 0"]
    assert schema_limits({"type": "number", "maximum": 600}) == ["at most 600"]


def test_limits_of_text_and_lists_inside_an_optional_value() -> None:
    text = {"anyOf": [{"type": "string", "maxLength": 500}, {"type": "null"}]}
    items = {"anyOf": [{"type": "array", "maxItems": 100, "items": {"type": "string"}}, {"type": "null"}]}

    assert schema_limits(text) == ["at most 500 characters"]
    assert schema_limits(items) == ["at most 100 items"]


def test_an_unbounded_value_has_no_limits() -> None:
    assert schema_limits({"type": "string"}) == []


# ------------------------------------------------------------------ rendering


def test_a_tool_section_has_its_name_title_and_verbatim_description() -> None:
    out = render(registry(tool("search_logs")))

    assert "## `search_logs`" in out
    assert "Title of search_logs" in out
    assert "```text\nPurpose of search_logs.\nInputs: role.\nOutputs: rows.\n```" in out


def test_inputs_table_states_type_requirement_default_and_limits() -> None:
    out = render(registry(tool()))

    assert "| `role` | one of `engineer`, `team_lead`, `manager` | yes |  | Who is asking. |" in out
    assert "| `limit` | integer | no | `20` | Rows. |" in out
    assert "- `limit`: 1 to 50" in out


def test_table_cells_escape_pipes_and_join_lines() -> None:
    schema = {
        "type": "object",
        "properties": {"role": {"type": "string", "description": "a | b\nc"}},
        "required": ["role"],
    }

    out = render(registry(tool(input_schema=schema)))

    assert "| `role` | string | yes |  | a \\| b c |" in out


def test_table_cells_escape_angle_brackets_so_they_are_not_read_as_html() -> None:
    schema = {
        "type": "object",
        "properties": {"role": {"type": "string", "description": "each as '<Section>: <text>'"}},
        "required": ["role"],
    }

    out = render(registry(tool(input_schema=schema)))

    assert "each as '&lt;Section&gt;: &lt;text&gt;'" in out


def test_angle_brackets_inside_code_spans_stay_as_written() -> None:
    schema = envelope()
    schema["description"] = "Cite it as `[E<n>]`, never <guess>."

    out = render(registry(tool("a", output_schema=schema), tool("b", output_schema=schema)))

    assert "Cite it as `[E<n>]`, never &lt;guess&gt;." in out


def test_annotations_and_always_load_are_stated() -> None:
    out = render(registry(tool()))

    assert "read-only" in out
    assert "idempotent" in out
    assert "anthropic/alwaysLoad" in out


def test_a_destructive_tool_is_marked_as_such() -> None:
    hints = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)

    out = render(registry(tool(annotations=hints)))

    assert "not read-only" in out
    assert "destructive" in out


def test_a_shared_output_schema_is_documented_once_and_referenced() -> None:
    out = render(registry(tool("a", output_schema=envelope()), tool("b", output_schema=envelope())))

    assert out.count("## Output: Evidence") == 1
    assert out.count("`structuredContent` is the [Evidence](#output-evidence) object") == 2
    assert "| `evidence_id` | string | yes | Cite it as [E3]. |" in out
    # Nested models are listed with their own fields.
    assert "| `data_scope.datasets` | list of string | no | D. |" in out


def test_a_unique_output_schema_is_documented_inline() -> None:
    out = render(registry(tool("a", output_schema=envelope("RoleBrief"))))

    assert "## Output: RoleBrief" not in out
    assert "| `evidence_id` | string | yes | Cite it as [E3]. |" in out


def test_a_tool_without_output_schema_says_so() -> None:
    out = render(registry(tool("a")))

    assert "No output schema" in out


def test_prompts_are_listed_with_their_arguments() -> None:
    prompt = Prompt(
        name="engineer",
        description="Answer as the Engineer.",
        arguments=[PromptArgument(name="focus", description="One token.", required=False)],
    )

    out = render(registry(tool(), prompts=(prompt,)))

    assert "### `engineer`" in out
    assert "Answer as the Engineer." in out
    assert "| `focus` | no | One token. |" in out


def test_overview_lists_every_tool_in_registry_order() -> None:
    out = render(registry(tool("zeta"), tool("alpha")))

    assert out.index("| [`zeta`](#zeta)") < out.index("| [`alpha`](#alpha)")


def test_document_says_it_is_generated_and_names_the_server() -> None:
    out = render(registry(tool()))

    assert out.startswith("# Tool reference\n")
    assert "make docs" in out
    assert "`serenedb-log` 9.9.9" in out
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_instructions_are_included_verbatim() -> None:
    out = render(registry(tool(), instructions="Cite [E<n>] next to every number."))

    assert "Cite [E<n>] next to every number." in out


# ------------------------------------------------------------------ the live registry


def test_collect_reads_every_tool_and_prompt_of_the_server() -> None:
    found = gen_tool_docs.collect()

    async_names = {t.name for t in found.tools}
    assert {"search_logs", "count_by", "get_role_brief"} <= async_names
    assert {p.name for p in found.prompts} == {"engineer", "team_lead", "manager"}
    assert found.server_name == create_server().name


def test_collect_ignores_a_role_pin_and_restores_it() -> None:
    set_settings(load_settings({}, role="manager"))

    found = gen_tool_docs.collect()

    assert {p.name for p in found.prompts} == {"engineer", "team_lead", "manager"}
    assert get_settings().role == "manager"


def test_committed_tools_md_matches_the_live_registry() -> None:
    expected = render(gen_tool_docs.collect())

    assert TOOLS_MD.is_file(), "docs/tools.md is missing; run `make docs`"
    assert TOOLS_MD.read_text(encoding="utf-8") == expected, "docs/tools.md is stale; run `make docs`"


# ------------------------------------------------------------------ command line


def test_main_writes_the_file(tmp_path: Path) -> None:
    target = tmp_path / "tools.md"

    assert gen_tool_docs.main(["--output", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == render(gen_tool_docs.collect())


def test_check_fails_on_a_stale_file_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "tools.md"
    target.write_text("old\n", encoding="utf-8")

    assert gen_tool_docs.main(["--check", "--output", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "old\n"
    assert "make docs" in capsys.readouterr().err


def test_check_passes_on_a_fresh_file(tmp_path: Path) -> None:
    target = tmp_path / "tools.md"
    gen_tool_docs.main(["--output", str(target)])

    assert gen_tool_docs.main(["--check", "--output", str(target)]) == 0
