#!/usr/bin/env python3
"""Write docs/tools.md, the tool reference, from the live tool registry (T8).

Usage (see `make help`):
    uv run python scripts/gen_tool_docs.py            # write docs/tools.md
    uv run python scripts/gen_tool_docs.py --check    # exit 1 when docs/tools.md is stale

The reference is what an MCP client receives from `initialize`, `tools/list` and `prompts/list` of
an in-process server, so it can never describe a tool other than the one Claude sees: for every
tool its name, title, annotations, the description verbatim, the inputs (type, required, default,
description), the limits from the input schema and the output fields. An output schema that
several tools share, the Evidence envelope, is documented once. The server is built without a
`--role` pin and needs no database. tests/unit/test_gen_tool_docs.py fails when the committed file
differs from what this script writes, so a tool change needs `make docs` in the same commit.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp_types import Prompt, Tool

from serenedb_log_mcp.config import Settings, get_settings, set_settings
from serenedb_log_mcp.server import create_server

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "docs" / "tools.md"
WRAP = 100
"""Fenced text wider than this is wrapped, so long instruction paragraphs read without scrolling."""

Schema = Mapping[str, Any]


@dataclass(frozen=True)
class Registry:
    """What a client sees of the server: its name, version, instructions, tools and prompts."""

    server_name: str
    version: str
    instructions: str | None
    tools: Sequence[Tool]
    prompts: Sequence[Prompt]


# ----------------------------------------------------------------- collecting


@contextmanager
def _unpinned() -> Iterator[None]:
    """Default settings while the server is built, so a `--role` pin cannot change the reference."""
    previous = get_settings()
    set_settings(Settings())
    try:
        yield
    finally:
        set_settings(previous)


def collect() -> Registry:
    """List the tools and prompts of a fresh in-process server, as a client receives them."""

    async def listing() -> Registry:
        with _unpinned():
            server = create_server()
        async with Client(server, mode="legacy") as client:
            info = client.server_info
            tools = (await client.list_tools()).tools
            prompts = (await client.list_prompts()).prompts
            return Registry(
                server_name=info.name if info else server.name,
                version=info.version if info else "",
                instructions=client.instructions,
                tools=list(tools),
                prompts=list(prompts),
            )

    return anyio.run(listing)


# ----------------------------------------------------------------- schema helpers


def _resolve(schema: Schema, defs: Schema) -> Schema:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = defs.get(ref.removeprefix("#/$defs/"), {})
        return target if isinstance(target, Mapping) else {}
    return schema


def schema_type(schema: Schema, defs: Schema) -> str:
    """A short, readable type: 'one of `a`, `b`', 'list of string', 'string or null'."""
    schema = _resolve(schema, defs)
    if "enum" in schema:
        return "one of " + ", ".join(f"`{value}`" for value in schema["enum"])
    if "anyOf" in schema:
        return " or ".join(schema_type(option, defs) for option in schema["anyOf"])
    kind = schema.get("type")
    if kind == "array":
        items = schema.get("items")
        return f"list of {schema_type(items, defs)}" if isinstance(items, Mapping) and items else "list"
    if isinstance(kind, str):
        return kind
    if "properties" in schema:
        return "object"
    return "any"


def schema_limits(schema: Schema) -> list[str]:
    """The bounds an input schema sets: value ranges, text lengths and list sizes."""
    if "anyOf" in schema:
        return [limit for option in schema["anyOf"] for limit in schema_limits(option)]
    limits = []
    low, high = schema.get("minimum"), schema.get("maximum")
    if low is not None and high is not None:
        limits.append(f"{low} to {high}")
    elif low is not None:
        limits.append(f"at least {low}")
    elif high is not None:
        limits.append(f"at most {high}")
    if "exclusiveMinimum" in schema:
        limits.append(f"greater than {schema['exclusiveMinimum']}")
    if "exclusiveMaximum" in schema:
        limits.append(f"less than {schema['exclusiveMaximum']}")
    if "minLength" in schema and schema["minLength"] > 0:
        limits.append(f"at least {schema['minLength']} characters")
    if "maxLength" in schema:
        limits.append(f"at most {schema['maxLength']} characters")
    if "minItems" in schema and schema["minItems"] > 0:
        limits.append(f"at least {schema['minItems']} items")
    if "maxItems" in schema:
        limits.append(f"at most {schema['maxItems']} items")
    return limits


def _cell(text: object) -> str:
    """Text for one Markdown table cell or paragraph: one line, pipes escaped, and `<...>` outside
    code spans written as entities so it is not read as an HTML tag (inside one it stays literal)."""
    parts = " ".join(str(text).split()).replace("|", "\\|").split("`")
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace("<", "&lt;").replace(">", "&gt;")
    return "`".join(parts)


def _default(schema: Schema) -> str:
    if "default" not in schema:
        return ""
    return _cell(f"`{json.dumps(schema['default'], ensure_ascii=False)}`")


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces as hyphens."""
    kept = "".join(c for c in heading.lower() if c.isalnum() or c in " -_")
    return kept.replace(" ", "-")


def _fenced(text: str) -> str:
    lines = []
    for line in text.strip("\n").splitlines():
        if len(line) <= WRAP:
            lines.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        bullet = "  " if line.lstrip().startswith("- ") else ""
        lines.extend(textwrap.wrap(line, WRAP, initial_indent="", subsequent_indent=indent + bullet))
    return "```text\n" + "\n".join(lines) + "\n```"


def _fields(schema: Schema, defs: Schema, prefix: str = "") -> Iterator[tuple[str, str, bool, str]]:
    """(dotted name, type, required, description) for each field, nested models expanded."""
    required = set(schema.get("required", ()))
    for name, field in schema.get("properties", {}).items():
        dotted = f"{prefix}{name}"
        resolved = _resolve(field, defs)
        yield dotted, schema_type(field, defs), name in required, field.get("description", "")
        if "properties" in resolved and "enum" not in resolved:
            yield from _fields(resolved, defs, prefix=f"{dotted}.")


# ----------------------------------------------------------------- rendering


def _annotations(tool: Tool) -> str:
    notes = []
    hints = tool.annotations
    if hints is not None:
        notes.append("read-only" if hints.read_only_hint else "not read-only")
        if hints.destructive_hint:
            notes.append("destructive")
        if hints.idempotent_hint:
            notes.append("idempotent")
        notes.append("open world" if hints.open_world_hint else "closed world")
    if (tool.meta or {}).get("anthropic/alwaysLoad"):
        notes.append("`anthropic/alwaysLoad` (Claude Code loads it without ToolSearch)")
    return ", ".join(notes) if notes else "none"


def _output_table(schema: Schema) -> list[str]:
    defs = schema.get("$defs", {})
    rows = ["| Field | Type | Required | Description |", "|---|---|---|---|"]
    for name, kind, required, description in _fields(schema, defs):
        rows.append(f"| `{name}` | {_cell(kind)} | {'yes' if required else 'no'} | {_cell(description)} |")
    return rows


def _output_title(tool: Tool) -> str | None:
    schema = tool.output_schema
    return str(schema["title"]) if schema and "title" in schema else None


def _tool_section(tool: Tool, shared: set[str]) -> list[str]:
    schema = tool.input_schema
    defs = schema.get("$defs", {})
    required = set(schema.get("required", ()))
    out = [f"## `{tool.name}`", ""]
    if tool.title:
        out += [f"**{tool.title}**", ""]
    out += [f"Annotations: {_annotations(tool)}.", "", "Description, as the agent reads it:", ""]
    out += [_fenced(tool.description or "(no description)"), "", "### Inputs", ""]
    properties = schema.get("properties", {})
    if properties:
        out += ["| Name | Type | Required | Default | Description |", "|---|---|---|---|---|"]
        for name, field in properties.items():
            out.append(
                f"| `{name}` | {_cell(schema_type(field, defs))} | {'yes' if name in required else 'no'} "
                f"| {_default(field)} | {_cell(field.get('description', ''))} |"
            )
    else:
        out.append("None.")
    limits = [(name, schema_limits(_resolve(field, defs))) for name, field in properties.items()]
    out += ["", "### Limits", ""]
    bounded = [f"- `{name}`: {', '.join(found)}" for name, found in limits if found]
    out += bounded or ["No bounds in the input schema; the description states any other limits."]
    out += ["", "### Output", ""]
    title = _output_title(tool)
    if tool.output_schema is None:
        out.append("No output schema; the description states what the result holds.")
    elif title in shared:
        out.append(
            f"`structuredContent` is the [{title}](#{_anchor('Output: ' + title)}) object; "
            "the description says what its rows and summary hold for this tool."
        )
    else:
        out += [
            f"`structuredContent` is a `{title or 'object'}` object:",
            "",
            *_output_table(tool.output_schema),
        ]
    return [*out, ""]


def _prompt_section(prompt: Prompt) -> list[str]:
    out = [f"### `{prompt.name}`", "", prompt.description or "(no description)", ""]
    if prompt.arguments:
        out += ["| Argument | Required | Description |", "|---|---|---|"]
        for argument in prompt.arguments:
            out.append(
                f"| `{argument.name}` | {'yes' if argument.required else 'no'} | "
                f"{_cell(argument.description or '')} |"
            )
    else:
        out.append("No arguments.")
    return [*out, ""]


def render(registry: Registry) -> str:
    """The Markdown reference for `registry`."""
    titles = Counter(title for tool in registry.tools if (title := _output_title(tool)))
    shared = {title for title, count in titles.items() if count > 1}
    out = [
        "# Tool reference",
        "",
        f"Generated by `scripts/gen_tool_docs.py` from the live registry of the MCP server "
        f"`{registry.server_name}` {registry.version}. Do not edit it by hand: run `make docs`, and a "
        "test fails while this file is stale.",
        "",
        "Every tool takes a required `role` (`engineer`, `team_lead` or `manager`), which changes what it "
        "returns. Every Evidence tool returns its result in `structuredContent` and records it in the "
        "Evidence Ledger under an id such as `E3`. In Claude Code the tools appear as "
        f"`mcp__{registry.server_name}__<tool>`.",
        "",
        "## Tools",
        "",
        "| Tool | Title | Output |",
        "|---|---|---|",
    ]
    for tool in registry.tools:
        title = _output_title(tool)
        output = f"[{title}](#{_anchor('Output: ' + title)})" if title in shared else (title or "none")
        out.append(f"| [`{tool.name}`](#{_anchor(tool.name)}) | {_cell(tool.title or '')} | {output} |")
    out.append("")
    for tool in registry.tools:
        out += _tool_section(tool, shared)
    for title in sorted(shared):
        schema = next(
            t.output_schema for t in registry.tools if _output_title(t) == title and t.output_schema
        )
        users = ", ".join(f"`{t.name}`" for t in registry.tools if _output_title(t) == title)
        out += [f"## Output: {title}", "", f"Returned by {users}.", ""]
        if description := schema.get("description"):
            out += [_cell(description), ""]
        out += [*_output_table(schema), ""]
    if registry.prompts:
        out += [
            "## Prompts",
            "",
            "The Role playbooks as MCP prompts. In Claude Code they are slash commands, for example "
            f"`/mcp__{registry.server_name}__engineer`; each argument is one optional token.",
            "",
        ]
        for prompt in registry.prompts:
            out += _prompt_section(prompt)
    if registry.instructions:
        out += [
            "## Server instructions",
            "",
            "Sent to the client at `initialize`, verbatim:",
            "",
            _fenced(registry.instructions),
            "",
        ]
    return "\n".join(out).rstrip("\n") + "\n"


# ----------------------------------------------------------------- command line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gen_tool_docs.py", description=__doc__.splitlines()[1])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="default: docs/tools.md")
    parser.add_argument("--check", action="store_true", help="exit 1 when the file is stale; write nothing")
    args = parser.parse_args(argv)
    text = render(collect())
    target: Path = args.output
    current = target.read_text(encoding="utf-8") if target.is_file() else None
    if args.check:
        if current != text:
            print(f"{target} is stale; run `make docs`.", file=sys.stderr)
            return 1
        print(f"{target} is up to date.")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
