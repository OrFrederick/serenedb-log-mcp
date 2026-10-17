"""Unit tests for the deterministic graders (no model, no DB). Run: pytest harness -q"""
import json
from pathlib import Path

import checks as C
from evtrace import ToolCall, Trace, parse_stream_json
from run_eval import pass_at_k, pass_hat_k

LEDGER = [
    {"evidence_id": "E1", "tool": "count_by", "total_count": 348460, "returned_count": 3,
     "rows": [["KERNDTLB", 152734], ["KERNSTOR", 63491], ["APPSEV", 49651]]},
    {"evidence_id": "E2", "tool": "trend", "total_count": 152734, "returned_count": 2,
     "rows": [["2005-06-11 00:00:00", 152182], ["2005-06-05 00:00:00", 477]]},
]
GOOD = """## Observed Facts
- KERNDTLB is the largest BGL alert category with 152,734 Log Entries [E1], 43.8% of 348,460 alert Log Entries [E1].
- 152,182 of them fall on 2005-06-11 [E2].
## Hypotheses
- One node may be looping on the same fault.
## Unknowns
- The logs cannot establish financial cost or customer downtime.
## Next Step
- Check node R30-M0-N9-C:J16-U01 hardware history.
"""


def tr(text, calls=("count_by", "trend"), ledger=LEDGER):
    return Trace("t", final_text=text, tool_calls=[ToolCall(c, {"dataset": "bgl"}) for c in calls], ledger=ledger)


def test_good_answer_passes_everything():
    t = tr(GOOD)
    for chk in (C.check_sections(t), C.check_citations_resolve(t), C.check_numbers_grounded(t),
                C.check_no_invented_impact(t), C.check_no_incident_counts(t), C.check_golden(t, [152734])):
        assert chk[0], chk[1]


def test_missing_section():
    ok, d = C.check_sections(tr(GOOD.replace("## Unknowns", "## Notes")))
    assert not ok and "Unknowns" in d


def test_dangling_citation():
    assert not C.check_citations_resolve(tr(GOOD.replace("[E2]", "[E7]")))[0]


def test_hallucinated_number():
    ok, d = C.check_numbers_grounded(tr(GOOD.replace("152,182", "152,812")))
    assert not ok and "152,812" in d


def test_uncited_number():
    ok, d = C.check_numbers_grounded(tr(GOOD.replace("on 2005-06-11 [E2]", "on 2005-06-11")))
    assert not ok and "uncited" in d


def test_wrong_percentage():
    assert not C.check_numbers_grounded(tr(GOOD.replace("43.8%", "53.8%")))[0]


def test_dates_and_node_ids_are_not_numbers():
    assert C.check_numbers_grounded(tr(GOOD))[0]   # 2005-06-11, R30-M0-N9-C:J16-U01 ignored


def test_invented_cost_flagged_outside_unknowns():
    bad = GOOD.replace("## Hypotheses", "- This storm cost us about $40,000 and caused 6 hours of downtime.\n## Hypotheses")
    ok, d = C.check_no_invented_impact(tr(bad))
    assert not ok and "$4" in d


def test_cost_allowed_inside_unknowns():
    assert C.check_no_invented_impact(tr(GOOD))[0]
    assert C.check_states_unknown(tr(GOOD), "cost|financial")[0]


def test_incident_count_flagged():
    assert not C.check_no_incident_counts(tr(GOOD + "\nThere were 152,182 incidents."))[0]
    assert C.check_no_incident_counts(tr(GOOD + "\nThese are Log Entries, not incidents."))[0]


def test_tool_path_subsequence_and_args():
    t = Trace("t", tool_calls=[ToolCall("search_logs", {"dataset": "bgl", "text": "data TLB error"}),
                               ToolCall("count_by", {"dataset": "bgl", "group_by": "alert_label"}),
                               ToolCall("trend", {"dataset": "bgl", "bucket": "day"})])
    assert C.check_tools(t, must_call=["trend"], order=["search_logs", "trend"],
                         args={"search_logs": {"text": "re:TLB"}})[0]
    assert not C.check_tools(t, order=["trend", "search_logs"])[0]
    assert not C.check_tools(t, args={"trend": {"bucket": "month"}})[0]
    assert not C.check_tools(t, must_not_call=["count_by"])[0]
    assert not C.check_tools(t, max_calls=2)[0]


def test_pass_metrics():
    assert pass_at_k(3, 1, 3) == 1.0 and pass_hat_k(3, 1, 3) == 0.0
    assert pass_hat_k(3, 3, 3) == 1.0
    assert abs(pass_at_k(5, 2, 2) - 0.7) < 1e-9 and abs(pass_hat_k(5, 2, 2) - 0.1) < 1e-9


def test_stream_json_parser_on_real_auth_failure():
    # Recorded from a real `claude -p` run (expired OAuth): subtype=success but is_error=true.
    p = Path(__file__).parent.parent / "out_claude_p_run1.jsonl"
    t = parse_stream_json(p.read_text().splitlines(), "x")
    assert t.tools_offered == ["count_by", "search_logs", "trend"]
    assert t.is_error and t.tool_calls == []


def test_stream_json_parser_tool_roundtrip():
    lines = [
        {"type": "system", "subtype": "init", "tools": ["mcp__logs__count_by"], "model": "m"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "mcp__logs__count_by",
                                                       "input": {"dataset": "bgl", "group_by": "level"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                  "content": [{"type": "text", "text": "{\"evidence_id\":\"E1\"}"}]}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "done [E1]", "total_cost_usd": 0.01,
         "num_turns": 2, "duration_ms": 900},
    ]
    t = parse_stream_json([json.dumps(x) for x in lines])
    assert [c.name for c in t.tool_calls] == ["count_by"] and t.tool_calls[0].args["group_by"] == "level"
    assert "E1" in t.tool_calls[0].result and t.cost_usd == 0.01 and not t.is_error


def test_kappa():
    from judge import agreement
    a = agreement([True, True, False, False], [True, True, False, False])
    assert a["kappa"] == 1.0 and a["tpr"] == 1.0
    b = agreement([True, True, True, False, False, False], [True, True, False, False, False, True])
    assert abs(b["kappa"] - (1 / 3)) < 1e-9


def test_line_numbers_are_identifiers_not_measurements():
    text = GOOD.replace("- 152,182 of them", "- Lines 222716–222765 and line 222715 are on the same node. 152,182 of them")
    assert C.check_numbers_grounded(tr(text))[0]


def test_uncited_count_still_fails_next_to_line_number():
    text = GOOD.replace("on 2005-06-11 [E2]", "on 2005-06-11, see line 222715")
    assert not C.check_numbers_grounded(tr(text))[0]


def test_ip_addresses_and_http_codes_are_not_measurements():
    text = GOOD.replace("- 152,182 of them",
                        "- Requests from 10.11.10.1 got HTTP 503 and status: 200, len: 323. 152,182 of them")
    assert C.check_numbers_grounded(tr(text))[0]


def test_uncited_count_with_http_code_still_fails():
    text = GOOD.replace("on 2005-06-11 [E2]", "on 2005-06-11 with HTTP 503")
    assert not C.check_numbers_grounded(tr(text))[0]


# --- regressions found in the Sonnet 5.5 k=3 baseline (grader false positives) ---
TEXT_LEDGER = LEDGER + [
    # Real shape of a search_logs Evidence row: the measurement sits in the log content cell.
    {"evidence_id": "E3", "tool": "search_logs", "total_count": 28, "returned_count": 28,
     "rows": [[201288, "2017-05-14 21:07:37.103000", "nova.compute.claims", "Total memory: 64172 MB, used: 512.00 MB"],
              [52312, "2017-05-16 06:25:02.870000", "nova.osapi_compute.wsgi.server",
               '10.11.10.1 "GET /v2/servers/detail HTTP/1.1" status: 503 len: 323 time: 0.0080531']]},
]


def test_numbers_inside_evidence_text_cells_count_as_grounded():
    """Baseline false positive: "64172 not found in ['E3']" although the log line says so."""
    text = GOOD.replace("- 152,182 of them fall on 2005-06-11 [E2].",
                        "- The host reported 64172 MB of total memory [E3].\n- Two requests returned 503 [E3].")
    ok, d = C.check_numbers_grounded(tr(text, ledger=TEXT_LEDGER))
    assert ok, d


def test_number_absent_from_evidence_text_cells_still_fails():
    text = GOOD.replace("- 152,182 of them fall on 2005-06-11 [E2].",
                        "- The host reported 64999 MB of total memory [E3].")
    ok, d = C.check_numbers_grounded(tr(text, ledger=TEXT_LEDGER))
    assert not ok and "64999" in d


TABLE_ANSWER = """## Observed Facts
**BGL alert labels, 348,460 Log Entries in total [E1]**

| Label | Log Entries |
|---|---|
| KERNDTLB | 152,734 |
| KERNSTOR | 63,491 |

## Hypotheses
- One node may be looping.
## Unknowns
- Cost is unknown.
## Next Step
- Check the node.
"""


def test_table_rows_inherit_citation_from_caption():
    """Baseline false positive: "uncited number 152,734: '| 1 | KERNDTLB | 152,734 |'"."""
    ok, d = C.check_numbers_grounded(tr(TABLE_ANSWER))
    assert ok, d


def test_table_rows_inherit_citation_from_header_row():
    text = TABLE_ANSWER.replace("**BGL alert labels, 348,460 Log Entries in total [E1]**",
                                "Breakdown:").replace("| Label | Log Entries |", "| Label | Log Entries [E1] |")
    ok, d = C.check_numbers_grounded(tr(text))
    assert ok, d


def test_inherited_citation_does_not_excuse_a_wrong_number():
    ok, d = C.check_numbers_grounded(tr(TABLE_ANSWER.replace("152,734", "152,999")))
    assert not ok and "152,999" in d


def test_table_without_any_citation_is_still_uncited():
    ok, d = C.check_numbers_grounded(tr(TABLE_ANSWER.replace(
        "**BGL alert labels, 348,460 Log Entries in total [E1]**", "Breakdown:")))
    assert not ok and "uncited" in d
