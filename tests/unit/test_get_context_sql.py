"""`get_context` SQL: point lookups and neighbours on the base table, never the inverted index."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.tools.get_context import Anchor, Context, anchor_statement, resolve_scope

SETTINGS = load_settings({}, slm_schema="slm")
BGL = relation("bgl", settings=SETTINGS)
OS = relation("openstack", settings=SETTINGS)
TS = datetime(2005, 7, 20, 10, 25)
NODE = "R00-M1-N1-C:J03-U11"
UUID = "b9000564-fe1a-409b-b8cc-1e88b294cd1d"


def bgl_anchor() -> Anchor:
    return Anchor(entry_id=42, ts=TS, epoch=1121880300.0, keys={"node": NODE})


def os_anchor(**keys: str | None) -> Anchor:
    base: dict[str, str | None] = {"request_id": None, "instance_id": None, "instance_ref": None}
    return Anchor(entry_id=7, ts=TS, epoch=1121880300.0, keys={**base, **keys})


def test_the_anchor_is_a_point_lookup_by_entry_id_on_the_base_table() -> None:
    statement = anchor_statement(OS, 7)

    assert statement.text == (
        'SELECT e."entry_id", e."ts", e."epoch_utc", e."request_id", e."instance_id", e."instance_ref" '
        'FROM "slm"."os_entry" AS e WHERE e."entry_id" = %s'
    )
    assert statement.params == (7,)


@pytest.mark.parametrize(
    ("anchor", "dataset", "expected"),
    [
        (bgl_anchor(), "bgl", ("node", NODE)),
        (os_anchor(request_id="req-1", instance_ref=UUID), "openstack", ("request", "req-1")),
        (os_anchor(instance_ref=UUID), "openstack", ("instance", UUID)),
        (os_anchor(instance_id=UUID), "openstack", ("instance", UUID)),
        (os_anchor(), "openstack", ("window", None)),
    ],
)
def test_auto_picks_the_narrowest_scope_the_anchor_has(
    anchor: Anchor, dataset: str, expected: tuple[str, str | None]
) -> None:
    assert resolve_scope(dataset, "auto", anchor) == expected


def test_a_scope_of_the_other_dataset_is_refused_with_the_valid_ones() -> None:
    with pytest.raises(ScopeError, match="scope='node' exists only in BGL"):
        resolve_scope("openstack", "node", os_anchor(request_id="req-1"))
    with pytest.raises(ScopeError, match="OpenStack"):
        resolve_scope("bgl", "request", bgl_anchor())


def test_a_scope_the_anchor_has_no_value_for_is_refused_with_an_alternative() -> None:
    with pytest.raises(ScopeError, match="no request_id"):
        resolve_scope("openstack", "request", os_anchor(instance_ref=UUID))


def test_the_instance_scope_matches_the_instance_id_or_the_uuid_in_the_message() -> None:
    context = Context(OS, os_anchor(instance_ref=UUID), "instance", UUID)

    statement = context.count_statement()
    assert statement.text == (
        'SELECT count(*) FROM "slm"."os_entry" AS e WHERE (e."instance_id" = %s OR e."message" LIKE %s)'
    )
    assert statement.params == (UUID, f"%{UUID}%")


def test_the_window_scope_bounds_the_time_as_logged_on_both_sides() -> None:
    start, end = TS - timedelta(seconds=60), TS + timedelta(seconds=60)
    context = Context(BGL, bgl_anchor(), "window", None, start=start, end=end)

    statement = context.count_statement()
    assert statement.text == 'SELECT count(*) FROM "slm"."bgl_entry" AS e WHERE e."ts" >= %s AND e."ts" < %s'
    assert statement.params == (start, end)


def test_the_rows_are_the_nearest_entries_before_and_from_the_anchor_in_utc_time_order() -> None:
    context = Context(BGL, bgl_anchor(), "node", NODE)

    statement = context.rows_statement(before=10, after=5)

    text = statement.text
    assert (
        '(e."epoch_utc", e."entry_id") < (%s, %s) ORDER BY e."epoch_utc" DESC, e."entry_id" DESC LIMIT %s'
        in text
    )
    assert '(e."epoch_utc", e."entry_id") >= (%s, %s) ORDER BY e."epoch_utc", e."entry_id" LIMIT %s' in text
    assert text.endswith("ORDER BY epoch, entry_id")
    assert statement.params == (NODE, 1121880300.0, 42, 10, NODE, 1121880300.0, 42, 6)


def test_no_context_statement_reads_the_inverted_index() -> None:
    contexts = [
        Context(BGL, bgl_anchor(), "node", NODE),
        Context(OS, os_anchor(instance_ref=UUID), "instance", UUID),
        Context(OS, os_anchor(request_id="r"), "request", "r", start=TS, end=TS + timedelta(seconds=1)),
    ]
    statements = [anchor_statement(BGL, 1), anchor_statement(OS, 1)]
    for context in contexts:
        statements += [context.count_statement(), context.rows_statement(before=1, after=1)]

    for statement in statements:
        assert "_idx" not in statement.text
        assert "@@" not in statement.text
