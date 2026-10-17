"""Tools never name a canonical table or index; they call corpus.datasets.relation() (plan 2.7).

The names come from the registry, so a table added there is checked here automatically. Names
with an underscore (bgl_entry, os_entry_idx, pattern_dict, ...) are flagged anywhere in a tool
module. The plain words pattern, incident and ownership are also ordinary English and enum values
(count_by(group_by='pattern')), so they are flagged only where code names a relation: after FROM,
JOIN, INTO, UPDATE or TABLE (optionally schema-qualified), as a string argument of Identifier(),
or assigned to a name that contains "table" or "index".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from serenedb_log_mcp.corpus.datasets import CANONICAL_INDEXES, CANONICAL_TABLES

TOOLS_DIR = Path(__file__).parents[2] / "src" / "serenedb_log_mcp" / "tools"

_NAMES = sorted(CANONICAL_TABLES | CANONICAL_INDEXES, key=len, reverse=True)
_ANYWHERE = re.compile(r"\b(" + "|".join(re.escape(n) for n in _NAMES if "_" in n) + r")\b")
_PLAIN = "|".join(re.escape(n) for n in _NAMES if "_" not in n)
_AFTER_SQL_KEYWORD = re.compile(
    r"(?i:\b(?:FROM|JOIN|INTO|UPDATE|TABLE))\s+(?:[\\\"\w{}$]+\.)?[\\\"]*(" + _PLAIN + r")\b"
)
# sql.Identifier(schema, "incident"): the form a tool author would copy from datasets.py.
_IDENTIFIER_ARGUMENT = re.compile(r"\bIdentifier\([^)]*?[\"'](" + _PLAIN + r")[\"']")
# INCIDENTS_TABLE = "incident", ownership_table: str = "ownership"
_ASSIGNED_TO_A_TABLE_NAME = re.compile(
    r"(?i:\b\w*(?:table|index)\w*)\s*(?::\s*[\w\[\]| ]+)?=\s*[\"'](" + _PLAIN + r")[\"']"
)
_PATTERNS = (_ANYWHERE, _AFTER_SQL_KEYWORD, _IDENTIFIER_ARGUMENT, _ASSIGNED_TO_A_TABLE_NAME)


def canonical_names_in(source: str) -> list[str]:
    """The canonical table and index names that `source` mentions, in order of appearance."""
    hits = {(m.start(1), m.group(1)) for pattern in _PATTERNS for m in pattern.finditer(source)}
    return [name for _, name in sorted(hits)]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('sql.SQL("SELECT count(*) FROM slm.bgl_entry")', ["bgl_entry"]),
        ('"SELECT * FROM os_entry_idx WHERE message @@ %s"', ["os_entry_idx"]),
        ('TABLE = "bgl_entry"', ["bgl_entry"]),
        ('"SELECT team FROM ownership WHERE component = %s"', ["ownership"]),
        ('"... JOIN {schema}.ownership o ON o.component = e.component"', ["ownership"]),
        ('"select * from slm.incident"', ["incident"]),
        ('"SELECT * FROM \\"slm\\".\\"pattern\\""', ["pattern"]),
        ('"FROM pattern_dict d JOIN pattern p USING (template_id)"', ["pattern_dict", "pattern"]),
        ('f"SELECT * FROM {schema}.os_instance"', ["os_instance"]),
        ('sql.SQL("SELECT * FROM {t}").format(t=sql.Identifier(schema, "incident"))', ["incident"]),
        ('sql.Identifier("slm", "ownership")', ["ownership"]),
        ("sql.Identifier(settings.slm_schema, 'pattern')", ["pattern"]),
        ('INCIDENTS_TABLE = "incident"', ["incident"]),
        ('ownership_table: str = "ownership"', ["ownership"]),
    ],
)
def test_the_scanner_finds_canonical_names(source: str, expected: list[str]) -> None:
    assert canonical_names_in(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        'rel = relation(dataset, role, scope)\nsql.SQL("SELECT count(*) FROM {t}").format(t=rel.table)',
        'group_by: Literal["component", "team", "pattern", "rack"]',
        '"""List the Incidents of one Pattern, with their Spread and Shape."""',
        "JOIN {owners} o ON o.component = e.{component}",
        "rel.ownership_table, rel.patterns_table, rel.incidents_table, rel.pattern_dict_table",
        '"pattern_key", "template_id", "family_id", "instance_ref"',
        "Pick ids from the Pattern catalogue",
    ],
)
def test_the_scanner_ignores_resolver_use_and_plain_words(source: str) -> None:
    assert canonical_names_in(source) == []


def tool_modules() -> list[Path]:
    return sorted(TOOLS_DIR.rglob("*.py"))


def test_the_tools_package_exists_and_is_scanned() -> None:
    # If tools/ moved, the parametrized test below would silently become a skip.
    assert (TOOLS_DIR / "__init__.py").is_file()
    assert tool_modules()


@pytest.mark.parametrize("module", tool_modules(), ids=lambda p: str(p.relative_to(TOOLS_DIR)))
def test_no_tool_module_names_a_canonical_table_or_index(module: Path) -> None:
    names = canonical_names_in(module.read_text())

    assert names == [], (
        f"{module.name} names {', '.join(sorted(set(names)))}; "
        "get table and index names from corpus.datasets.relation() instead"
    )
