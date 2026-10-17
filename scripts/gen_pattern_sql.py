#!/usr/bin/env python3
"""Generate the Tier 1 Pattern SQL from the Loghub-2.0 template files (plan T9, #6).

Usage:
    uv run python scripts/gen_pattern_sql.py [--data-dir DIR] [--schema S]   # print the SQL

scripts/derive.py calls `load_templates` and `tier1_sql` at derive time and puts the result into
sql/derive/20_patterns.sql (placeholder ${pattern_tier1}); this command only prints it for
inspection. Neither the template files nor the generated SQL are committed: the files come from
`make fetch-data` (Loghub-2.0, Zenodo 8275861) into SLM_DATA_DIR.

Tier 1 is one SQL `CASE` chain per Dataset, wrapped in a macro `<schema>.pattern_tier1_<dataset>(m)`
that returns the Loghub-2.0 EventId of a message, or NULL when no template matches:

  - every template becomes an anchored RE2 pattern (regexp_full_match): literal text escaped, each
    whitespace run as \\s+, and each <*> as \\S+ (strict: one whitespace-free token, like
    Loghub-2.0's own matcher) or .* (loose: any text, also empty);
  - all strict patterns come before all loose ones, so a loose pattern only decides when no strict
    one matches (the hybrid matcher of #6 that agreed with 99.9999% of the gold labels);
  - within each half, the most specific template comes first: more literal characters, then fewer
    wildcards, then the EventId.

A temporary table pattern_tier1_template(dataset, event_id, template_text) carries the template
texts for the derive session. With Tier 1 off (SLM_PATTERN_TIER1=0) the macros return NULL and the
table is empty, so every message gets a Tier 2 (g10 mask) Pattern.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DATASETS = ("bgl", "openstack")
TEMPLATE_FILES: Mapping[str, str] = {
    "bgl": "lh2/BGL/BGL_full.log_templates.csv",
    "openstack": "lh2/OpenStack/OpenStack_full.log_templates.csv",
}
HEADER = ["EventId", "EventTemplate", "Occurrences"]
WILDCARD = "<*>"
STRICT = r"\S+"
LOOSE = r".*"
RE2_SPECIAL = frozenset("\\.^$|?*+()[]{}")
TOKEN_RE = re.compile(r"(<\*>|\s+)")
NOT_LITERAL_RE = re.compile(r"<\*>|\s+")
TEMPLATE_TABLE = "pattern_tier1_template"


class TemplateFileError(Exception):
    """A template file that is not a Loghub-2.0 *_templates.csv."""


class TemplatesMissingError(Exception):
    """Tier 1 is on, but the Loghub-2.0 template files are not in SLM_DATA_DIR."""


@dataclass(frozen=True)
class Template:
    event_id: str
    text: str


# ----------------------------------------------------------------- template files


def read_templates(path: Path) -> list[Template]:
    """The templates of one Loghub-2.0 *_templates.csv, in file order."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != HEADER:
            raise TemplateFileError(f"{path} has header {header}; expected {','.join(HEADER)}")
        templates: list[Template] = []
        seen: set[str] = set()
        for row in reader:
            if len(row) != len(HEADER):
                raise TemplateFileError(f"{path} line {reader.line_num}: expected {len(HEADER)} fields")
            event_id, text = row[0], row[1]
            if event_id in seen:
                raise TemplateFileError(f"{path} lists EventId {event_id} twice")
            seen.add(event_id)
            templates.append(Template(event_id, text))
    return templates


def load_templates(data_dir: Path) -> dict[str, list[Template]]:
    """The templates of every Dataset; TemplatesMissingError names the missing files."""
    paths = {dataset: data_dir / TEMPLATE_FILES[dataset] for dataset in DATASETS}
    if missing := [str(path) for path in paths.values() if not path.is_file()]:
        raise TemplatesMissingError(
            "Tier 1 Patterns need the Loghub-2.0 template files, and these are missing: "
            + ", ".join(missing)
            + ". Run `make fetch-data` (SLM_DATA_DIR), or set SLM_PATTERN_TIER1=0 to give every "
            "Log Entry a Tier 2 (generic mask) Pattern instead."
        )
    return {dataset: read_templates(path) for dataset, path in paths.items()}


# ----------------------------------------------------------------- regexes and order


def to_regex(template: str, wildcard: str) -> str:
    """The RE2 pattern for a template: literals escaped, whitespace runs as \\s+, <*> as `wildcard`."""
    parts = []
    for part in TOKEN_RE.split(template):
        if part == WILDCARD:
            parts.append(wildcard)
        elif part.isspace():
            parts.append(r"\s+")
        elif part:
            parts.append("".join("\\" + c if c in RE2_SPECIAL else c for c in part))
    return "".join(parts)


def literal_chars(template: str) -> int:
    """How many characters of the template are neither wildcards nor whitespace."""
    return len(NOT_LITERAL_RE.sub("", template))


def ordered(templates: Sequence[Template]) -> list[Template]:
    """Most specific first: more literal characters, then fewer wildcards, then the EventId."""
    return sorted(templates, key=lambda t: (-literal_chars(t.text), t.text.count(WILDCARD), t.event_id))


# ----------------------------------------------------------------- SQL


def sql_literal(text: str) -> str:
    """A SQL string literal; backslashes stay as they are (standard-conforming strings)."""
    return "'" + text.replace("'", "''") + "'"


def case_chain(templates: Sequence[Template], arg: str) -> str:
    """CASE over `arg`: every strict pattern, then every loose one, each half most specific first."""
    order = ordered(templates)
    whens = [
        f"WHEN regexp_full_match({arg}, {sql_literal(to_regex(t.text, wildcard))}) "
        f"THEN {sql_literal(t.event_id)}"
        for wildcard in (STRICT, LOOSE)
        for t in order
    ]
    return "CASE " + " ".join(whens) + " END"


def tier1_sql(schema: str, templates: Mapping[str, Sequence[Template]] | None) -> str:
    """The Tier 1 statements for `schema` (already quoted), without a trailing semicolon.

    `templates` None means Tier 1 is off: the macros return NULL and the template table is empty.
    """
    statements = []
    for dataset in DATASETS:
        body = "CAST(NULL AS VARCHAR)"
        if templates is not None and templates.get(dataset):
            body = case_chain(templates[dataset], "m")
        statements.append(f"CREATE OR REPLACE MACRO {schema}.pattern_tier1_{dataset}(m) AS {body}")
    rows = [
        f"({sql_literal(dataset)}, {sql_literal(t.event_id)}, {sql_literal(t.text)})"
        for dataset in DATASETS
        for t in (templates or {}).get(dataset, ())
    ]
    if rows:
        statements.append(
            f"CREATE OR REPLACE TEMPORARY TABLE {TEMPLATE_TABLE} AS SELECT * FROM (VALUES "
            + ", ".join(rows)
            + ") AS t(dataset, event_id, template_text)"
        )
    else:
        statements.append(
            f"CREATE OR REPLACE TEMPORARY TABLE {TEMPLATE_TABLE} "
            "(dataset VARCHAR, event_id VARCHAR, template_text VARCHAR)"
        )
    return ";\n".join(statements)


# ----------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen_pattern_sql.py", description="Print the Tier 1 Pattern SQL generated from Loghub-2.0."
    )
    parser.add_argument("--data-dir", type=Path, help="default: SLM_DATA_DIR")
    parser.add_argument("--schema", help="default: SLM_SCHEMA")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from serenedb_log_mcp.config import SCHEMA_RE, ConfigError, load_settings

    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        data_dir = args.data_dir or settings.data_dir
        schema = args.schema or settings.slm_schema
        if not SCHEMA_RE.fullmatch(schema):
            raise ConfigError(f"schema {schema!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
        print(tier1_sql(f'"{schema}"', load_templates(data_dir)) + ";")
    except (TemplatesMissingError, TemplateFileError, ConfigError, OSError) as exc:
        print(f"gen_pattern_sql: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
