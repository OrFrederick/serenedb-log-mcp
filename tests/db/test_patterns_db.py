"""The Pattern dictionary step (T9) against a real SereneDB, on the Loghub 2k samples.

Each Tier gets its own throwaway schemas: a copy of the sample raw tables (from the `derived_sample`
fixture) plus a few synthetic rows, derived once with Pattern Tier 1 on and once with it off. The
Tier 1 tests need the Loghub-2.0 template files in SLM_DATA_DIR (`make fetch-data`) and skip
without them, also under SLM_REQUIRE_DB; the Tier 2 tests run either way.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import optional_data
import psycopg
import pytest
from psycopg import sql

import derive
import gen_pattern_sql
from serenedb_log_mcp.config import load_settings


class Schemas(Protocol):
    raw: str
    slm: str


@dataclass(frozen=True)
class T9Schemas:
    raw: str
    slm: str
    tier1: bool


BGL_TS = datetime(2005, 11, 25, 17, 36, 33, 459319)
# (line_no, alert_tag, message). The two EDRAM lines are the wording drift of #1 (Loghub-2.0 E218
# and E112); the Kill line is in no Loghub-2.0 template, because Loghub-2.0 parsed it differently.
SYNTHETIC_BGL: tuple[tuple[int, str, str | None], ...] = (
    (950_001, "-", None),
    (950_002, "-", "   "),
    (950_003, "-", "3 L3 EDRAM error(s) (dcr 0x0157) detected and corrected"),
    (950_004, "-", "1 L3 EDRAM error(s) (dcr 0x0157) detected and corrected over 754 seconds"),
    (950_005, "-", "Kill job 20251 timed out. Block freed."),
    (950_006, "-", "3 L3 EDRAM error(s) (dcr 0x0157) detected and corrected"),
)
SYNTHETIC_OS_NULL_LINE = 950_101

# The six "over N seconds" template pairs of #6 (BGL EventIds).
OVER_N_SECONDS_PAIRS = (
    ("E218", "E112"),
    ("E63", "E34"),
    ("E70", "E40"),
    ("E78", "E43"),
    ("E119", "E67"),
    ("E191", "E92"),
)


def rows(
    database_url: str, query: sql.Composable | str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(query, params).fetchall()]


def one(database_url: str, query: sql.Composable | str, params: list[Any] | None = None) -> tuple[Any, ...]:
    result = rows(database_url, query, params)
    assert len(result) == 1
    return result[0]


def q(template: str, schema: str, **tables: str) -> sql.Composed:
    """Format {name} placeholders as schema-qualified tables of `schema`."""
    return sql.SQL(template).format(**{k: sql.Identifier(schema, v) for k, v in tables.items()})


def run_derive(tier1: bool, *argv: str) -> int:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SLM_PATTERN_TIER1", "1" if tier1 else "0")
        return derive.main(list(argv))


def templates_or_skip() -> dict[str, list[gen_pattern_sql.Template]]:
    """The Loghub-2.0 templates; a Pattern Tier 1 test skips without them, even with SLM_REQUIRE_DB."""
    if (problem := optional_data.templates_problem()) is not None:
        pytest.skip(problem)
    return gen_pattern_sql.load_templates(load_settings().data_dir)


def _drop(database_url: str, *schemas: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        for schema in schemas:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _copy_raw_with_synthetic_rows(database_url: str, source: str, target: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(target)))
        for table in ("bgl", "openstack"):
            conn.execute(
                sql.SQL("CREATE TABLE {} AS SELECT * FROM {}").format(
                    sql.Identifier(target, table), sql.Identifier(source, table)
                )
            )
        for line_no, tag, message in SYNTHETIC_BGL:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (line_no, alert_tag, epoch, ts, node, component, level, message, raw) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                ).format(sql.Identifier(target, "bgl")),
                [
                    line_no,
                    tag,
                    1132968993,
                    BGL_TS,
                    "R35-M1-NA-C:J09-U01",
                    "KERNEL",
                    "INFO",
                    message,
                    "synthetic",
                ],
            )
        conn.execute(
            sql.SQL(
                "INSERT INTO {} (line_no, source_file, log_file, ts, pid, level, component, request_id, "
                "instance_id, message, raw) SELECT %s, source_file, log_file, ts, pid, 'ERROR', "
                "'oslo_service.periodic_task', NULL, NULL, NULL, 'synthetic' FROM {} ORDER BY line_no LIMIT 1"
            ).format(sql.Identifier(target, "openstack"), sql.Identifier(target, "openstack")),
            [SYNTHETIC_OS_NULL_LINE],
        )


def _derived(database_url: str, source_raw: str, tier1: bool) -> Iterator[T9Schemas]:
    suffix = uuid.uuid4().hex[:8]
    schemas = T9Schemas(raw=f"scratch_t9_{suffix}_raw", slm=f"scratch_t9_{suffix}", tier1=tier1)
    try:
        _copy_raw_with_synthetic_rows(database_url, source_raw, schemas.raw)
        assert run_derive(tier1, "--schema", schemas.slm, "--raw-schema", schemas.raw) == 0
        yield schemas
    finally:
        _drop(database_url, schemas.slm, schemas.raw)


@pytest.fixture(scope="session")
def tier1_sample(database_url: str, derived_sample: Schemas) -> Iterator[T9Schemas]:
    templates_or_skip()
    yield from _derived(database_url, derived_sample.raw, tier1=True)


@pytest.fixture(scope="session")
def tier2_sample(database_url: str, derived_sample: Schemas) -> Iterator[T9Schemas]:
    yield from _derived(database_url, derived_sample.raw, tier1=False)


@pytest.fixture(params=["tier1", "tier2"])
def sample(request: pytest.FixtureRequest) -> T9Schemas:
    schemas: T9Schemas = request.getfixturevalue(f"{request.param}_sample")
    return schemas


ENTRY_TABLES = (("bgl", "bgl_entry"), ("openstack", "os_entry"))


# ----------------------------------------------------------------- the dictionary


def test_every_log_entry_gets_a_pattern(database_url: str, sample: T9Schemas) -> None:
    for _, table in ENTRY_TABLES:
        (missing,) = one(
            database_url,
            q("SELECT count(*) FROM {t} "
              "WHERE template_id IS NULL OR family_id IS NULL OR pattern_key IS NULL",
              sample.slm, t=table),
        )  # fmt: skip
        assert missing == 0, table


def test_the_dictionary_has_one_row_per_distinct_message(database_url: str, sample: T9Schemas) -> None:
    for dataset, table in ENTRY_TABLES:
        (messages,) = one(
            database_url, q("SELECT count(*) FROM (SELECT DISTINCT message FROM {t}) m", sample.slm, t=table)
        )
        entries, distinct = one(
            database_url,
            q(
                "SELECT count(*), "
                "(SELECT count(*) FROM (SELECT DISTINCT message FROM {d} WHERE dataset = %s) m) "
                "FROM {d} WHERE dataset = %s",
                sample.slm,
                d="pattern_dict",
            ),
            [dataset, dataset],
        )
        assert entries == distinct == messages, dataset


def test_log_entries_carry_the_pattern_of_their_message(database_url: str, sample: T9Schemas) -> None:
    for dataset, table in ENTRY_TABLES:
        (wrong,) = one(
            database_url,
            q(
                "SELECT count(*) FROM {t} e JOIN {d} d ON d.dataset = %s "
                "AND (d.message = e.message OR (d.message IS NULL AND e.message IS NULL)) "
                "WHERE e.template_id <> d.template_id OR e.family_id <> d.family_id",
                sample.slm,
                t=table,
                d="pattern_dict",
            ),
            [dataset],
        )
        assert wrong == 0, dataset


def family_text(text: str) -> str:
    return re.sub(r" over <\*> seconds$", "", text)


def test_ids_are_content_hashes_of_the_dataset_and_template_text(
    database_url: str, sample: T9Schemas
) -> None:
    result = rows(
        database_url,
        q(
            "SELECT DISTINCT dataset, template_text, template_id, family_id FROM {d}",
            sample.slm,
            d="pattern_dict",
        ),
    )

    assert result
    for dataset, text, template_id, family_id in result:
        assert template_id == hashlib.md5(f"{dataset}:{text}".encode()).hexdigest()[:16]
        assert family_id == hashlib.md5(f"{dataset}:{family_text(text)}".encode()).hexdigest()


def test_null_and_blank_messages_get_the_empty_pattern(database_url: str, sample: T9Schemas) -> None:
    result = rows(
        database_url,
        q(
            "SELECT dataset, message, template_source, template_text, event_id FROM {d} "
            "WHERE message IS NULL OR trim(message) = '' ORDER BY 1, 2 NULLS FIRST",
            sample.slm,
            d="pattern_dict",
        ),
    )

    assert result == [
        ("bgl", None, "empty", "", None),
        ("bgl", "   ", "empty", "", None),
        ("openstack", None, "empty", "", None),
    ]
    (sources,) = one(
        database_url,
        q("SELECT count(*) FROM {d} WHERE template_source = 'empty' AND trim(message) <> ''", sample.slm,
          d="pattern_dict"),
    )  # fmt: skip
    assert sources == 0


def test_the_over_n_seconds_drift_is_one_pattern_family(database_url: str, sample: T9Schemas) -> None:
    result = rows(
        database_url,
        q(
            "SELECT entry_id, template_id, family_id FROM {t} WHERE entry_id IN (950003, 950004, 950006) "
            "ORDER BY entry_id",
            sample.slm,
            t="bgl_entry",
        ),
    )

    (_, short_id, short_family), (_, long_id, long_family), (_, again_id, again_family) = result
    assert short_family == long_family == again_family
    assert short_id == again_id != long_id


# ----------------------------------------------------------------- pattern_key


def test_pattern_key_is_the_alert_category_for_alert_lines_and_the_template_id_otherwise(
    database_url: str, sample: T9Schemas
) -> None:
    bgl = one(
        database_url,
        q(
            "SELECT count(*) FILTER (WHERE alert_tag IS NOT NULL AND pattern_key = alert_tag), "
            "count(alert_tag), count(*) FILTER (WHERE alert_tag IS NULL AND pattern_key = template_id), "
            "count(*) FILTER (WHERE alert_tag IS NULL) FROM {t}",
            sample.slm,
            t="bgl_entry",
        ),
    )
    os_ = one(
        database_url,
        q(
            "SELECT count(*) FILTER (WHERE pattern_key = template_id), count(*) FROM {t}",
            sample.slm,
            t="os_entry",
        ),
    )

    assert bgl[1] > 0
    assert bgl[0] == bgl[1]
    assert bgl[2] == bgl[3] > 0
    assert os_[0] == os_[1] > 0


# ----------------------------------------------------------------- the Pattern catalogue


def test_the_pattern_table_has_one_row_per_pattern_with_its_counts(
    database_url: str, sample: T9Schemas
) -> None:
    for dataset, table in ENTRY_TABLES:
        expected = rows(
            database_url,
            q(
                "SELECT template_id, count(*), min(ts), max(ts) FROM {t} GROUP BY 1 ORDER BY 1",
                sample.slm,
                t=table,
            ),
        )
        actual = rows(
            database_url,
            q(
                "SELECT template_id, entries, first_seen, last_seen FROM {p} WHERE dataset = %s ORDER BY 1",
                sample.slm,
                p="pattern",
            ),
            [dataset],
        )
        assert actual == expected, dataset


def test_the_pattern_table_keeps_the_dictionary_texts(database_url: str, sample: T9Schemas) -> None:
    (mismatches,) = one(
        database_url,
        q(
            "SELECT count(*) FROM {p} p JOIN {d} d "
            "ON d.dataset = p.dataset AND d.template_id = p.template_id "
            "WHERE d.template_text <> p.template_text OR d.template_source <> p.template_source "
            "OR d.family_id <> p.family_id OR d.event_id IS DISTINCT FROM p.event_id",
            sample.slm,
            p="pattern",
            d="pattern_dict",
        ),
    )
    assert mismatches == 0


def test_component_and_level_are_the_most_frequent_ones(database_url: str, sample: T9Schemas) -> None:
    for column in ("component", "level"):
        for dataset, table in ENTRY_TABLES:
            counts: dict[str, dict[str | None, int]] = {}
            for template_id, value, n in rows(
                database_url,
                sql.SQL("SELECT template_id, {c}, count(*) FROM {t} GROUP BY 1, 2").format(
                    c=sql.Identifier(column), t=sql.Identifier(sample.slm, table)
                ),
            ):
                counts.setdefault(template_id, {})[value] = n
            actual = dict(
                rows(
                    database_url,
                    sql.SQL("SELECT template_id, {c} FROM {p} WHERE dataset = %s").format(
                        c=sql.Identifier(column), p=sql.Identifier(sample.slm, "pattern")
                    ),
                    [dataset],
                )
            )
            for template_id, by_value in counts.items():
                best = max(by_value.values())
                winners = sorted(v for v, n in by_value.items() if n == best and v is not None)
                expected = winners[0] if winners else None
                assert actual[template_id] == expected, (dataset, column, template_id)


# ----------------------------------------------------------------- Tier 1 and Tier 2


def sources(database_url: str, schemas: T9Schemas) -> dict[tuple[str, str], int]:
    result = rows(
        database_url,
        q("SELECT dataset, template_source, count(*) FROM {d} GROUP BY 1, 2", schemas.slm, d="pattern_dict"),
    )
    return {(dataset, source): n for dataset, source, n in result}


def test_tier2_alone_uses_only_the_generic_masks(database_url: str, tier2_sample: T9Schemas) -> None:
    assert {source for _, source in sources(database_url, tier2_sample)} == {"sqlmask", "empty"}
    (event_ids,) = one(database_url, q("SELECT count(event_id) FROM {d}", tier2_sample.slm, d="pattern_dict"))
    assert event_ids == 0
    (masked,) = one(
        database_url,
        q(
            "SELECT d.template_text FROM {t} e JOIN {d} d ON d.dataset = 'bgl' AND d.message = e.message "
            "WHERE e.entry_id = 950003",
            tier2_sample.slm,
            t="bgl_entry",
            d="pattern_dict",
        ),
    )
    # g10 masks every token with a digit (3, L3, 0x0157) and collapses "<*> <*>".
    assert masked == "<*> EDRAM error(s) (dcr <*>) detected and corrected"


def test_tier1_gives_the_loghub2_template_to_the_sample_messages(
    database_url: str, tier1_sample: T9Schemas
) -> None:
    by_source = sources(database_url, tier1_sample)
    result = dict(
        rows(
            database_url,
            q(
                "SELECT e.entry_id, d.event_id FROM {t} e "
                "JOIN {d} d ON d.dataset = 'bgl' AND d.message = e.message "
                "WHERE e.entry_id IN (950003, 950004, 950005)",
                tier1_sample.slm,
                t="bgl_entry",
                d="pattern_dict",
            ),
        )
    )

    assert result == {950_003: "E218", 950_004: "E112", 950_005: None}
    # The 2k samples are Loghub lines, so almost every distinct message has a Loghub-2.0 template.
    for dataset in ("bgl", "openstack"):
        assert by_source[(dataset, "loghub2")] > 0.95 * sum(
            n for (d, _), n in by_source.items() if d == dataset
        )
    (mismatch,) = one(
        database_url,
        q(
            "SELECT count(*) FROM {d} WHERE (template_source = 'loghub2') <> (event_id IS NOT NULL)",
            tier1_sample.slm,
            d="pattern_dict",
        ),
    )
    assert mismatch == 0


def reference_event_id(templates: list[gen_pattern_sql.Template], message: str) -> str | None:
    """#6's hybrid matcher: the most specific strict template, else the most specific loose one."""
    for wildcard in (gen_pattern_sql.STRICT, gen_pattern_sql.LOOSE):
        for template in gen_pattern_sql.ordered(templates):
            if re.fullmatch(gen_pattern_sql.to_regex(template.text, wildcard), message, re.ASCII):
                return template.event_id
    return None


def test_the_sql_chain_agrees_with_the_python_reference_matcher(
    database_url: str, tier1_sample: T9Schemas
) -> None:
    templates = templates_or_skip()
    for dataset in ("bgl", "openstack"):
        result = rows(
            database_url,
            q(
                "SELECT message, event_id FROM {d} WHERE dataset = %s AND template_source <> 'empty'",
                tier1_sample.slm,
                d="pattern_dict",
            ),
            [dataset],
        )
        assert len(result) > 100
        wrong = [(m, e) for m, e in result if reference_event_id(templates[dataset], m) != e]
        assert wrong == [], dataset


def test_tier1_template_texts_are_the_loghub2_texts(database_url: str, tier1_sample: T9Schemas) -> None:
    templates = templates_or_skip()
    texts = {(d, t.event_id): t.text for d, found in templates.items() for t in found}
    result = rows(
        database_url,
        q(
            "SELECT DISTINCT dataset, event_id, template_text FROM {d} WHERE template_source = 'loghub2'",
            tier1_sample.slm,
            d="pattern_dict",
        ),
    )

    assert result
    assert all(texts[(dataset, event_id)] == text for dataset, event_id, text in result)


def test_the_six_over_n_seconds_template_pairs_each_share_one_family_id(
    database_url: str, tier1_sample: T9Schemas
) -> None:
    texts = {t.event_id: t.text for t in templates_or_skip()["bgl"]}
    ids = [e for pair in OVER_N_SECONDS_PAIRS for e in pair]
    family = sql.Identifier(tier1_sample.slm, "pattern_family_id")
    template = sql.Identifier(tier1_sample.slm, "pattern_template_id")
    query = sql.SQL("SELECT {template}('bgl', %s), {family}('bgl', %s)").format(
        template=template, family=family
    )
    result = {e: one(database_url, query, [texts[e], texts[e]]) for e in ids}

    for short, long in OVER_N_SECONDS_PAIRS:
        assert texts[long].endswith(" over <*> seconds"), long
        assert result[short][1] == result[long][1], (short, long)
        assert result[short][0] != result[long][0], (short, long)
    assert len({family_id for _, family_id in result.values()}) == len(OVER_N_SECONDS_PAIRS)


# ----------------------------------------------------------------- without the optional Loghub-2.0 files


def test_the_shared_sample_is_derived_with_tier1_exactly_when_the_templates_are_there(
    database_url: str, derived_sample: Schemas
) -> None:
    used = rows(
        database_url, q("SELECT DISTINCT template_source FROM {d}", derived_sample.slm, d="pattern_dict")
    )

    assert (("loghub2",) in used) is optional_data.derive_with_tier1()


def write_template_files(data_dir: Path) -> None:
    for name in gen_pattern_sql.TEMPLATE_FILES.values():
        (data_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (data_dir / name).write_text(",".join(gen_pattern_sql.HEADER) + "\n")


def test_a_schema_without_tier1_patterns_is_a_skip_only_without_the_template_files(
    database_url: str, tier2_sample: T9Schemas, tmp_path: Path
) -> None:
    problem = optional_data.tier1_patterns_problem(database_url, tier2_sample.slm, tmp_path)
    write_template_files(tmp_path)

    assert problem is not None
    assert f"schema {tier2_sample.slm!r} has no Tier 1 Pattern" in problem
    assert f"Loghub-2.0 templates not found in {tmp_path / 'lh2'}" in problem
    assert "make derive" in problem
    # With the files there, a schema without Tier 1 Patterns is a derive to redo, not a skip.
    assert optional_data.tier1_patterns_problem(database_url, tier2_sample.slm, tmp_path) is None


def test_a_schema_with_tier1_patterns_needs_no_template_files(
    database_url: str, tier1_sample: T9Schemas, tmp_path: Path
) -> None:
    assert optional_data.tier1_patterns_problem(database_url, tier1_sample.slm, tmp_path) is None


# ----------------------------------------------------------------- re-runs


def snapshot(database_url: str, schema: str) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "dict": rows(
            database_url,
            q(
                "SELECT dataset, message, template_id, template_text, template_source, event_id, family_id "
                "FROM {d} ORDER BY 1, 2 NULLS FIRST",
                schema,
                d="pattern_dict",
            ),
        ),
        "pattern": rows(database_url, q("SELECT * FROM {p} ORDER BY 1, 2", schema, p="pattern")),
        "bgl": rows(
            database_url,
            q(
                "SELECT entry_id, template_id, family_id, pattern_key FROM {t} ORDER BY 1",
                schema,
                t="bgl_entry",
            ),
        ),
        "os": rows(
            database_url,
            q(
                "SELECT entry_id, template_id, family_id, pattern_key FROM {t} ORDER BY 1",
                schema,
                t="os_entry",
            ),
        ),
    }


@pytest.fixture
def own_derive(database_url: str, tier2_sample: T9Schemas) -> Iterator[T9Schemas]:
    """A fresh Tier 2 derive of the T9 raw copy into its own schema, dropped afterwards."""
    schemas = T9Schemas(raw=tier2_sample.raw, slm=f"scratch_t9_{uuid.uuid4().hex[:8]}", tier1=False)
    try:
        assert run_derive(False, "--schema", schemas.slm, "--raw-schema", schemas.raw) == 0
        yield schemas
    finally:
        _drop(database_url, schemas.slm)


def test_rerunning_only_the_pattern_step_changes_nothing(database_url: str, own_derive: T9Schemas) -> None:
    before = snapshot(database_url, own_derive.slm)

    assert run_derive(False, "--schema", own_derive.slm, "--only", "20_patterns") == 0

    assert snapshot(database_url, own_derive.slm) == before
    # The inverted index still answers on the updated columns.
    template_id = before["bgl"][0][1]
    (indexed,) = one(
        database_url,
        q("SELECT count(*) FROM {i} WHERE template_id @@ %s", own_derive.slm, i="bgl_entry_idx"),
        [template_id],
    )
    (scanned,) = one(
        database_url,
        q("SELECT count(*) FROM {t} WHERE template_id = %s", own_derive.slm, t="bgl_entry"),
        [template_id],
    )
    assert indexed == scanned > 0


def test_switching_tier1_on_recomputes_the_patterns_but_keeps_alert_categories(
    database_url: str, own_derive: T9Schemas
) -> None:
    templates_or_skip()
    before = snapshot(database_url, own_derive.slm)

    assert run_derive(True, "--schema", own_derive.slm, "--only", "20_patterns") == 0

    after = snapshot(database_url, own_derive.slm)
    assert {row[4] for row in after["dict"]} == {"loghub2", "sqlmask", "empty"}
    changed = [b for b, a in zip(before["bgl"], after["bgl"], strict=True) if b[1] != a[1]]
    assert changed
    alerts = dict(
        rows(
            database_url,
            q(
                "SELECT entry_id, alert_tag FROM {t} WHERE alert_tag IS NOT NULL",
                own_derive.slm,
                t="bgl_entry",
            ),
        )
    )
    for entry_id, template_id, _, pattern_key in after["bgl"]:
        assert pattern_key == alerts.get(entry_id, template_id), entry_id
    for _, template_id, _, pattern_key in after["os"]:
        assert pattern_key == template_id


# ----------------------------------------------------------------- the merge-gate scorer

# Labelled lines on top of the Tier 1 dictionary's own choices: one line whose gold label differs
# from what the chain picks, and one Loghub-2.0 line that no template matches (as with the
# truncated BGL lines of E7 in the full corpus).
EXTRA_LABELS = (
    ("3 L3 EDRAM error(s) (dcr 0x0157) detected and corrected", "E112"),
    ("Kill job 20251 timed out. Block freed.", "E278"),
)


@pytest.fixture(scope="session")
def labelled(database_url: str, tier1_sample: T9Schemas) -> T9Schemas:
    """lh2_bgl and lh2_openstack in the Tier 1 raw schema: every Tier 1 message with its EventId."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        for dataset, table in (("bgl", "lh2_bgl"), ("openstack", "lh2_openstack")):
            conn.execute(
                sql.SQL(
                    "CREATE OR REPLACE TABLE {labels} AS "
                    "SELECT row_number() OVER (ORDER BY message) AS line_id, message AS content, event_id, "
                    "template_text AS event_template FROM {d} "
                    "WHERE dataset = %s AND template_source = 'loghub2'"
                ).format(
                    labels=sql.Identifier(tier1_sample.raw, table),
                    d=sql.Identifier(tier1_sample.slm, "pattern_dict"),
                ),
                [dataset],
            )
        for line_id, (content, event_id) in enumerate(EXTRA_LABELS, start=1_000_000):
            conn.execute(
                sql.SQL("INSERT INTO {} VALUES (%s, %s, %s, 'gold template')").format(
                    sql.Identifier(tier1_sample.raw, "lh2_bgl")
                ),
                [line_id, content, event_id],
            )
    return tier1_sample


def score_args(schemas: T9Schemas, *extra: str) -> list[str]:
    return ["--schema", schemas.slm, "--labels-schema", schemas.raw, *extra]


def test_the_scorer_lists_every_disagreeing_template_and_fails_the_gate(
    labelled: T9Schemas, capsys: pytest.CaptureFixture[str]
) -> None:
    import score_patterns

    assert score_patterns.main(score_args(labelled)) == 1

    out = capsys.readouterr().out
    assert "| E112 | E218 | 1 | 1 |" in out
    assert "| E278 | none (Tier 2) | 1 | 1 |" in out
    assert "- openstack: no disagreeing template." in out
    assert "FAIL bgl" in out
    assert re.search(r"\| openstack \| [\d,]+ \| [\d,]+ \| 100% \| 100% \| 100% \|", out)
    assert re.search(r"- bgl: ([\d,]+) of \1 pattern_dict rows carry the EventId", out)
    assert "| bgl | loghub2 |" in out
    assert "| bgl | sqlmask |" in out
    assert "| bgl | empty |" in out


def test_the_scorer_passes_with_a_lower_minimum(
    labelled: T9Schemas, capsys: pytest.CaptureFixture[str]
) -> None:
    import score_patterns

    assert score_patterns.main(score_args(labelled, "--min-agreement", "0.9")) == 0

    assert "- PASS" in capsys.readouterr().out


def test_the_scorer_refuses_a_schema_derived_without_tier1(
    labelled: T9Schemas, tier2_sample: T9Schemas, capsys: pytest.CaptureFixture[str]
) -> None:
    import score_patterns

    args = ["--schema", tier2_sample.slm, "--labels-schema", labelled.raw]

    assert score_patterns.main(args) == 2

    assert "SLM_PATTERN_TIER1=0" in capsys.readouterr().out


def test_the_scorer_names_missing_labels(tier2_sample: T9Schemas, capsys: pytest.CaptureFixture[str]) -> None:
    import score_patterns

    assert score_patterns.main(score_args(tier2_sample)) == 2

    assert "make load-dev" in capsys.readouterr().err
