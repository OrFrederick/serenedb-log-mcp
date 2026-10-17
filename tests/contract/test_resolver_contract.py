"""Tools never name a canonical table or index; they call corpus.datasets.relation() (plan 2.7).

The names come from the registry, so a table added there is checked here automatically. Names
with an underscore (bgl_entry, os_entry_idx, pattern_dict, ...) are flagged anywhere in a tool
module. The plain words pattern, incident and ownership are also ordinary English and enum values
(count_by(group_by='pattern')), so they are flagged only where SQL names a relation: after FROM,
JOIN, INTO, UPDATE or TABLE, optionally schema-qualified.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from serenedb_log_mcp.corpus.datasets import CANONICAL_INDEXES, CANONICAL_TABLES

TOOLS_DIR = Path(__file__).parents[2] / "src" / "serenedb_log_mcp" / "tools"

_NAMES = sorted(CANONICAL_TABLES | CANONICAL_INDEXES, key=len, reverse=True)
_ANYWHERE = re.compile(r"\b(" + "|".join(re.escape(n) for n in _NAMES if "_" in n) + r")\b")
_AFTER_SQL_KEYWORD = re.compile(
    r"(?i:\b(?:FROM|JOIN|INTO|UPDATE|TABLE))\s+"
    r"(?:[\\\"\w{}$]+\.)?[\\\"]*(" + "|".join(re.escape(n) for n in _NAMES if "_" not in n) + r")\b"
)


def canonical_names_in(source: str) -> list[str]:
    """The canonical table and index names that `source` mentions, in order of appearance."""
    hits = [(m.start(1), m.group(1)) for m in _ANYWHERE.finditer(source)]
    hits += [(m.start(1), m.group(1)) for m in _AFTER_SQL_KEYWORD.finditer(source)]
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


def test_the_tools_package_exists() -> None:
    assert (TOOLS_DIR / "__init__.py").is_file()


@pytest.mark.parametrize("module", tool_modules(), ids=lambda p: str(p.relative_to(TOOLS_DIR)))
def test_no_tool_module_names_a_canonical_table_or_index(module: Path) -> None:
    names = canonical_names_in(module.read_text())

    assert names == [], (
        f"{module.name} names {', '.join(sorted(set(names)))}; "
        "get table and index names from corpus.datasets.relation() instead"
    )
