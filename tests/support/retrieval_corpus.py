"""The synthetic corpus of the retrieval tools (T5): T6's aggregation corpus plus request ids.

BGL is tests/support/aggregation_corpus.py (see its numbers) plus two INFO lines of Component DST
on NODE_DST that share one ts, 2005-07-10 01:30:00.5, but lie an hour apart in epoch_utc, as two
lines do in the hour BGL's local time repeats at the daylight-saving fall-back.

OpenStack adds what get_context's request and instance scopes need:
- the 2 nova.compute.manager ERROR lines for U1 (2017-05-16 20:00:00 and 20:00:05) carry request
  REQ;
- one more line in REQ at 20:00:02, "Terminating instance": instance_id U1, but no UUID in its
  message and no instance_ref, so only `instance_id = x` finds it;
- one API line in REQ_OTHER at 2017-05-14 20:00 with no instance at all.

The instance scope of U1 therefore holds 15 Log Entries: the 12 "VM Started" lines of U1 and the 2
ERROR lines name it in their message, and the Terminating line has it as instance_id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import aggregation_corpus
from aggregation_corpus import OS_FIRST, U1, _bgl, _os

REQ = "req-11111111-aaaa-bbbb-cccc-000000000001"
REQ_OTHER = "req-22222222-aaaa-bbbb-cccc-000000000002"
TERMINATING_TS = datetime(2017, 5, 16, 20, 0, 2)
ERROR_TS = (datetime(2017, 5, 16, 20, 0, 0), datetime(2017, 5, 16, 20, 0, 5))
U1_INSTANCE_SCOPE = 15
REQ_ENTRIES = 3
OS_TOTAL = aggregation_corpus.OS_TOTAL + 1

NODE_DST = "R02-M1-N0-C:J12-U11"
DST_TS = datetime(2005, 7, 10, 1, 30, 0, 500000)
DST_MESSAGES = ("before the fall-back", "after the fall-back")
BGL_TOTAL = aggregation_corpus.BGL_TOTAL + len(DST_MESSAGES)


def bgl_rows() -> list[dict[str, Any]]:
    rows = aggregation_corpus.bgl_rows()
    first_epoch = DST_TS.timestamp()
    for hour, message in enumerate(DST_MESSAGES):
        row = _bgl(DST_TS, NODE_DST, "DST", "INFO", None, message, None, None)
        row["epoch_utc"] = first_epoch + 3600 * hour
        rows.append(row)
    rows.sort(key=lambda row: (row["epoch_utc"], row["ts"]))
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def os_rows() -> list[dict[str, Any]]:
    rows = aggregation_corpus.os_rows()
    for row in rows:
        if row["level"] == "ERROR":
            row["request_id"] = REQ
        elif row["ts"] == OS_FIRST and row["instance_ref"] is None:
            row["request_id"] = REQ_OTHER
    terminating = _os(TERMINATING_TS, "nova.compute.manager", "INFO", None, "Terminating instance")
    terminating.update(instance_id=U1, request_id=REQ)
    rows.append(terminating)
    rows.sort(key=lambda row: row["ts"])
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


def build(database_url: str, schema: str) -> None:
    aggregation_corpus.build(database_url, schema, bgl=bgl_rows(), openstack=os_rows())


def drop(database_url: str, schema: str) -> None:
    aggregation_corpus.drop(database_url, schema)
