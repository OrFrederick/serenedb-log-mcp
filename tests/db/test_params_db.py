"""The Pattern parameter step (sql/derive/25_params.sql) against a real SereneDB, on the Loghub 2k
samples derived by the `derived_sample` fixture."""

from __future__ import annotations

from typing import Any, Protocol

import psycopg
import pytest
from psycopg import sql

pytestmark = pytest.mark.db


class Schemas(Protocol):
    raw: str
    slm: str


def rows(database_url: str, query: sql.Composable) -> list[tuple[Any, ...]]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(query).fetchall()]


def fill(template: str, values: list[str]) -> str:
    """The template with its <*> slots replaced by `values`, left to right."""
    parts = template.split("<*>")
    assert len(parts) == len(values) + 1
    out = parts[0]
    for value, part in zip(values, parts[1:], strict=True):
        out += value + part
    return out


def entries_with_params(database_url: str, slm: str, dataset: str, table: str) -> list[tuple[Any, ...]]:
    return rows(
        database_url,
        sql.SQL(
            "SELECT e.entry_id, e.message, p.template_text, "
            "string_agg(ep.value, chr(1) ORDER BY ep.pos), count(ep.pos), max(ep.pos) "
            "FROM {entry} AS e JOIN {pattern} AS p ON p.dataset = {ds} AND p.template_id = e.template_id "
            "LEFT JOIN {param} AS ep ON ep.dataset = {ds} AND ep.entry_id = e.entry_id "
            "WHERE p.template_text LIKE '%<*>%' "
            "GROUP BY e.entry_id, e.message, p.template_text"
        ).format(
            entry=sql.Identifier(slm, table),
            pattern=sql.Identifier(slm, "pattern"),
            param=sql.Identifier(slm, "entry_param"),
            ds=sql.Literal(dataset),
        ),
    )


def test_every_slot_of_every_entry_has_a_value(database_url: str, derived_sample: Schemas) -> None:
    for dataset, table in (("bgl", "bgl_entry"), ("openstack", "os_entry")):
        result = entries_with_params(database_url, derived_sample.slm, dataset, table)
        assert result, f"no {dataset} Log Entry has a Pattern with a <*>"
        for entry_id, _message, template, _joined, count, top in result:
            slots = template.count("<*>")
            assert count == slots, (dataset, entry_id, template)
            assert top == slots, (dataset, entry_id, template)


def test_the_values_put_back_into_the_template_give_the_message(
    database_url: str, derived_sample: Schemas
) -> None:
    for dataset, table in (("bgl", "bgl_entry"), ("openstack", "os_entry")):
        for entry_id, message, template, joined, _count, _top in entries_with_params(
            database_url, derived_sample.slm, dataset, table
        ):
            values = joined.split("\x01")
            # Templates hold single spaces where messages may hold any whitespace run (\s+).
            assert fill(template, values).split() == message.split(), (dataset, entry_id, template, values)


def test_the_status_parameter_of_an_api_request_is_its_http_status(
    database_url: str, derived_sample: Schemas
) -> None:
    result = rows(
        database_url,
        sql.SQL(
            "SELECT e.http_status, ep.value FROM {entry} AS e "
            "JOIN {param} AS ep ON ep.dataset = 'openstack' AND ep.entry_id = e.entry_id "
            "AND ep.context = 'status' "
            "WHERE e.http_status IS NOT NULL"
        ).format(
            entry=sql.Identifier(derived_sample.slm, "os_entry"),
            param=sql.Identifier(derived_sample.slm, "entry_param"),
        ),
    )
    assert result
    assert all(str(status) == value for status, value in result)


def test_slots_are_numbered_from_one_without_gaps(database_url: str, derived_sample: Schemas) -> None:
    result = rows(
        database_url,
        sql.SQL(
            "SELECT p.template_text, count(*), min(s.pos), max(s.pos), max(s.slots) "
            "FROM {slot} AS s JOIN {pattern} AS p USING (dataset, template_id) GROUP BY p.template_text"
        ).format(
            slot=sql.Identifier(derived_sample.slm, "pattern_param"),
            pattern=sql.Identifier(derived_sample.slm, "pattern"),
        ),
    )
    assert result
    for template, count, low, high, slots in result:
        assert (count, low, high, slots) == (template.count("<*>"), 1, template.count("<*>"), count), template
