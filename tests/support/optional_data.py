"""The optional Loghub-2.0 data, and why a test that needs it skips without it.

Loghub-2.0 (Zenodo 8275861) is a separate download from the Base Corpus (Zenodo 8196385). Only
Pattern Tier 1 and its scorer need it:

- the template files SLM_DATA_DIR/lh2/*/*_templates.csv (`make fetch-data`), which a derive with
  Pattern Tier 1 on reads;
- the label tables lh2_bgl and lh2_openstack in SLM_RAW_SCHEMA (`make load-dev`), which the scorer
  reads.

A missing piece is a skip with the reason, also under SLM_REQUIRE_DB=1: that flag stays strict for
the database, the Base Corpus and the derive, not for an optional download (#66).
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

import gen_pattern_sql
from serenedb_log_mcp.config import load_settings

LOGHUB2_TABLES = ("lh2_bgl", "lh2_openstack")


def templates_problem(data_dir: Path | None = None) -> str | None:
    """Why Pattern Tier 1 cannot be derived from `data_dir` (default SLM_DATA_DIR), or None."""
    data_dir = load_settings().data_dir if data_dir is None else data_dir
    missing = [
        str(Path(name).relative_to("lh2"))
        for name in gen_pattern_sql.TEMPLATE_FILES.values()
        if not (data_dir / name).is_file()
    ]
    if not missing:
        return None
    return (
        f"Loghub-2.0 templates not found in {data_dir / 'lh2'} (SLM_DATA_DIR): {', '.join(missing)}; "
        "run `make fetch-data`"
    )


def derive_with_tier1() -> bool:
    """Whether a derive in the db tests runs Pattern Tier 1: SLM_PATTERN_TIER1 is on (the default)
    and the template files are there. Without them the derive runs with Tier 1 off, and only the
    Tier 1 tests skip."""
    settings = load_settings()
    return settings.pattern_tier1 and templates_problem(settings.data_dir) is None


def tier1_patterns_problem(database_url: str, schema: str, data_dir: Path | None = None) -> str | None:
    """Why the Pattern corpus tests cannot check Tier 1 in `schema`, or None.

    They read the Patterns a derive built, not the template files, so a schema with Tier 1 Patterns
    is enough (on a shared instance, someone else may have derived it). Without such Patterns the
    template files decide: with them a schema without Tier 1 is a derive to redo, a real failure;
    without them it is the missing optional download, a skip.
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE template_source = 'loghub2'").format(
                sql.Identifier(schema, "pattern")
            )
        ).fetchone()
    if row and row[0] > 0:
        return None
    if (problem := templates_problem(data_dir)) is None:
        return None
    return f"schema {schema!r} has no Tier 1 Pattern: {problem}, then `make derive`"
