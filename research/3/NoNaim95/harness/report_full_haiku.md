## Pass rate per assertion

| Assertion | Pass | N | Rate |
|---|---|---|---|
| answer.forbid_regex | 1 | 1 | 100% |
| citations.number_in_cited_entry | 22 | 36 | 61% |
| citations.numbers_in_cited_block | 31 | 36 | 86% |
| citations.numbers_near_citation | 12 | 36 | 33% |
| citations.resolve | 36 | 36 | 100% |
| glossary.no_entry_count_as_incidents | 36 | 36 | 100% |
| infra.tool_errors | 36 | 36 | 100% |
| ledger.one_entry_per_call | 36 | 36 | 100% |
| manager.no_invented_impact | 16 | 16 | 100% |
| numbers.golden_in_answer | 16 | 16 | 100% |
| numbers.grounded_in_ledger | 20 | 36 | 56% |
| numbers.grounded_or_derived | 25 | 36 | 69% |
| structure.sections | 36 | 36 | 100% |
| tools.any_of | 14 | 14 | 100% |
| tools.args | 24 | 24 | 100% |
| tools.forbidden | 4 | 4 | 100% |
| tools.only_mcp | 36 | 36 | 100% |
| tools.required | 22 | 22 | 100% |
| unknowns.flagged | 6 | 6 | 100% |

## Per question (all hard checks pass)

| Question | Model | Backend | Runs | All-pass | Tool sets across repeats | Calls | $/run (mean) | Wall s (mean) |
|---|---|---|---|---|---|---|---|---|
| adv-01 | haiku | cli | 1 | 0/1 | describe_corpus,trend | [2] | 0.0014 | 11.2 |
| adv-02 | haiku | cli | 1 | 0/1 | count_by,describe_corpus | [2] | 0.0010 | 10.6 |
| adv-03 | haiku | cli | 1 | 1/1 | search_logs | [1] | 0.0006 | 8.5 |
| adv-04 | haiku | cli | 1 | 1/1 | count_by,describe_corpus | [2] | 0.0009 | 10.2 |
| adv-05 | haiku | cli | 1 | 0/1 | count_by,describe_corpus | [3] | 0.0011 | 9.6 |
| adv-06 | haiku | cli | 1 | 1/1 | describe_corpus | [1] | 0.0006 | 6.9 |
| eng-01 | haiku | cli | 1 | 0/1 | count_by,get_context,search_logs | [3] | 0.0023 | 17.6 |
| eng-02 | haiku | cli | 1 | 0/1 | get_context | [1] | 0.0017 | 16.8 |
| eng-03 | haiku | cli | 1 | 0/1 | count_by,search_logs,trend | [5] | 0.0033 | 24.3 |
| eng-04 | haiku | cli | 1 | 0/1 | count_by,search_logs | [2] | 0.0026 | 15.8 |
| eng-05 | haiku | cli | 1 | 0/1 | count_by,get_context,search_logs | [3] | 0.0032 | 15.7 |
| eng-06 | haiku | cli | 1 | 0/1 | count_by,search_logs | [4] | 0.0019 | 15.8 |
| eng-07 | haiku | cli | 1 | 0/1 | count_by,get_context,search_logs | [3] | 0.0025 | 110.5 |
| eng-08 | haiku | cli | 1 | 0/1 | get_context,search_logs | [2] | 0.0027 | 29.0 |
| eng-09 | haiku | cli | 1 | 0/1 | get_context,search_logs | [3] | 0.0029 | 52.6 |
| eng-10 | haiku | cli | 1 | 0/1 | count_by,get_context,search_logs | [5] | 0.0044 | 63.9 |
| mgr-01 | haiku | cli | 1 | 1/1 | count_by,describe_corpus,trend | [11] | 0.0033 | 18.2 |
| mgr-02 | haiku | cli | 1 | 0/1 | describe_corpus,trend | [2] | 0.0012 | 15.7 |
| mgr-03 | haiku | cli | 1 | 0/1 | count_by,describe_corpus,search_logs,trend | [12] | 0.0040 | 20.2 |
| mgr-04 | haiku | cli | 1 | 0/1 | count_by,describe_corpus | [5] | 0.0018 | 14.3 |
| mgr-05 | haiku | cli | 1 | 0/1 | count_by,describe_corpus | [2] | 0.0013 | 10.8 |
| mgr-06 | haiku | cli | 1 | 0/1 | count_by,search_logs,trend | [5] | 0.0030 | 14.3 |
| mgr-07 | haiku | cli | 1 | 0/1 | count_by,describe_corpus,trend | [7] | 0.0030 | 17.2 |
| mgr-08 | haiku | cli | 1 | 0/1 | count_by,describe_corpus,trend | [11] | 0.0036 | 19.2 |
| mgr-09 | haiku | cli | 1 | 0/1 | count_by | [2] | 0.0015 | 11.5 |
| mgr-10 | haiku | cli | 1 | 0/1 | count_by | [2] | 0.0011 | 11.0 |
| tl-01 | haiku | cli | 1 | 0/1 | count_by | [1] | 0.0007 | 15.7 |
| tl-02 | haiku | cli | 1 | 0/1 | trend | [1] | 0.0008 | 14.4 |
| tl-03 | haiku | cli | 1 | 0/1 | count_by,describe_corpus | [3] | 0.0012 | 29.9 |
| tl-04 | haiku | cli | 1 | 1/1 | count_by | [1] | 0.0007 | 11.3 |
| tl-05 | haiku | cli | 1 | 0/1 | count_by | [1] | 0.0015 | 17.9 |
| tl-06 | haiku | cli | 1 | 0/1 | describe_corpus,trend | [2] | 0.0013 | 19.1 |
| tl-07 | haiku | cli | 1 | 0/1 | count_by,describe_corpus | [2] | 0.0016 | 14.2 |
| tl-08 | haiku | cli | 1 | 0/1 | describe_corpus | [1] | 0.0006 | 8.5 |
| tl-09 | haiku | cli | 1 | 0/1 | describe_corpus,trend | [2] | 0.0013 | 13.8 |
| tl-10 | haiku | cli | 1 | 0/1 | count_by,describe_corpus | [2] | 0.0010 | 9.9 |

Total runs 36, total $0.0674, mean $0.0019/run, median wall 15.7s, max wall 110.5s

## Pass rate per assertion x model

| Assertion | gate? | haiku/cli |
|---|---|---|
| answer.forbid_regex | gate | 1/1 |
| citations.number_in_cited_entry | style | 22/36 |
| citations.numbers_in_cited_block | gate | 31/36 |
| citations.numbers_near_citation | style | 12/36 |
| citations.resolve | gate | 36/36 |
| glossary.no_entry_count_as_incidents | gate | 36/36 |
| infra.tool_errors | style | 36/36 |
| ledger.one_entry_per_call | gate | 36/36 |
| manager.no_invented_impact | gate | 16/16 |
| numbers.golden_in_answer | gate | 16/16 |
| numbers.grounded_in_ledger | style | 20/36 |
| numbers.grounded_or_derived | gate | 25/36 |
| structure.sections | gate | 36/36 |
| tools.any_of | gate | 14/14 |
| tools.args | gate | 24/24 |
| tools.forbidden | gate | 4/4 |
| tools.only_mcp | gate | 36/36 |
| tools.required | gate | 22/22 |
| unknowns.flagged | gate | 6/6 |

## Gate pass (all gate checks) per question x model, with repeat consistency

| Question | haiku/cli |
|---|---|
| adv-01 | 0/1 |
| adv-02 | 0/1 |
| adv-03 | 1/1 |
| adv-04 | 1/1 |
| adv-05 | 1/1 |
| adv-06 | 1/1 |
| eng-01 | 1/1 |
| eng-02 | 1/1 |
| eng-03 | 1/1 |
| eng-04 | 0/1 |
| eng-05 | 1/1 |
| eng-06 | 0/1 |
| eng-07 | 0/1 |
| eng-08 | 0/1 |
| eng-09 | 0/1 |
| eng-10 | 1/1 |
| mgr-01 | 1/1 |
| mgr-02 | 0/1 |
| mgr-03 | 1/1 |
| mgr-04 | 1/1 |
| mgr-05 | 0/1 |
| mgr-06 | 1/1 |
| mgr-07 | 1/1 |
| mgr-08 | 1/1 |
| mgr-09 | 1/1 |
| mgr-10 | 0/1 |
| tl-01 | 0/1 |
| tl-02 | 0/1 |
| tl-03 | 1/1 |
| tl-04 | 1/1 |
| tl-05 | 0/1 |
| tl-06 | 1/1 |
| tl-07 | 0/1 |
| tl-08 | 1/1 |
| tl-09 | 0/1 |
| tl-10 | 1/1 |

- **haiku/cli**: gate pass 21/36 (58%); $0.0674 total, $0.0019/run; wall median 15.7s (max 110.5s); tool calls/run mean 3.2

## Failures

- adv-01 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 7, "ungrounded": ["2,500", "100"]}
- adv-01 r0 haiku cli: **numbers.grounded_or_derived** ["2,500"]
- adv-01 r0 haiku cli: **citations.number_in_cited_entry** ["2,500->['E2']", "100->['E2']"]
- adv-02 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 12, "ungrounded": ["988,477"]}
- adv-02 r0 haiku cli: **numbers.grounded_or_derived** ["988,477"]
- adv-02 r0 haiku cli: **citations.numbers_near_citation** ["3,735,813", "855,195", "112,355", "23,357", "19,213", "1,714", "306"]
- adv-05 r0 haiku cli: **citations.numbers_near_citation** ["186", "3,118"]
- eng-01 r0 haiku cli: **citations.numbers_near_citation** ["4998"]
- eng-02 r0 haiku cli: **citations.numbers_near_citation** ["100000", "99990", "99991", "99992", "99993", "99994", "99995", "99996", "99997", "99998"]
- eng-03 r0 haiku cli: **citations.numbers_near_citation** ["186"]
- eng-04 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 34, "ungrounded": ["65", "41", "42"]}
- eng-04 r0 haiku cli: **citations.numbers_near_citation** ["2048", "20", "201346", "201347", "201362", "201364", "201365", "41.81", "201366", "204"]
- eng-04 r0 haiku cli: **citations.numbers_in_cited_block** ["28"]
- eng-04 r0 haiku cli: **citations.number_in_cited_entry** ["65->['E1']"]
- eng-05 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 14, "ungrounded": ["35"]}
- eng-05 r0 haiku cli: **citations.numbers_near_citation** ["295", "33892", "33893", "33902", "33877", "33891", "15"]
- eng-05 r0 haiku cli: **citations.number_in_cited_entry** ["35->['E3']"]
- eng-06 r0 haiku cli: **citations.numbers_near_citation** ["222715", "222716", "222717", "222718", "222719"]
- eng-06 r0 haiku cli: **citations.numbers_in_cited_block** ["222715", "222716", "222717", "222718", "222719"]
- eng-07 r0 haiku cli: **citations.numbers_near_citation** ["3050899"]
- eng-07 r0 haiku cli: **citations.numbers_in_cited_block** ["3050899"]
- eng-08 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 14, "ungrounded": ["170"]}
- eng-08 r0 haiku cli: **numbers.grounded_or_derived** ["170"]
- eng-08 r0 haiku cli: **citations.numbers_near_citation** ["17742", "17737", "17747", "170"]
- eng-08 r0 haiku cli: **citations.numbers_in_cited_block** ["170"]
- eng-09 r0 haiku cli: **citations.numbers_near_citation** ["27179", "27179", "27180", "27200", "27180", "27181", "220", "27182", "27183", "6743"]
- eng-09 r0 haiku cli: **citations.numbers_in_cited_block** ["27179"]
- eng-10 r0 haiku cli: **citations.numbers_near_citation** ["45"]
- mgr-02 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 19, "ungrounded": ["251%"]}
- mgr-02 r0 haiku cli: **numbers.grounded_or_derived** ["251%"]
- mgr-02 r0 haiku cli: **citations.numbers_near_citation** ["251%", "18%", "48%", "12%", "6%", "42,542", "47,641", "44,859", "5%"]
- mgr-02 r0 haiku cli: **citations.number_in_cited_entry** ["251%->['E1', 'E2', 'E1', 'E1', 'E1', 'E1', 'E1', 'E1', 'E1', 'E1', 'E1']"]
- mgr-03 r0 haiku cli: **citations.numbers_near_citation** ["204,506", "3,118", "186", "98%"]
- mgr-04 r0 haiku cli: **citations.numbers_near_citation** ["626,450", "855,195"]
- mgr-05 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 22, "ungrounded": ["348,460", "348,460", "348,460", "288,000", "348,460"]}
- mgr-05 r0 haiku cli: **numbers.grounded_or_derived** ["288,000"]
- mgr-05 r0 haiku cli: **citations.numbers_near_citation** ["152,734", "3.2%", "63,491", "1.3%", "49,651", "1.0%", "31,531", "0.7%", "23,338", "0.5%"]
- mgr-05 r0 haiku cli: **citations.number_in_cited_entry** ["348,460->['E2']", "348,460->['E2', 'E1', 'E2', 'E2']"]
- mgr-06 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 17, "ungrounded": ["65", "42", "42", "42"]}
- mgr-06 r0 haiku cli: **citations.numbers_near_citation** ["41.81", "42.81", "201366", "201369"]
- mgr-06 r0 haiku cli: **citations.number_in_cited_entry** ["65->['E5']"]
- mgr-07 r0 haiku cli: **citations.numbers_near_citation** ["152,183", "344,398", "152,229", "28%", "128"]
- mgr-08 r0 haiku cli: **citations.numbers_near_citation** ["4,747,963", "855,195", "626,450", "73%", "228,536", "27%", "112,355", "548,475"]
- mgr-09 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 18, "ungrounded": ["300", "400"]}
- mgr-10 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 14, "ungrounded": ["26"]}
- mgr-10 r0 haiku cli: **numbers.grounded_or_derived** ["26"]
- mgr-10 r0 haiku cli: **citations.numbers_near_citation** ["26", "4,747,963", "207,820"]
- mgr-10 r0 haiku cli: **citations.number_in_cited_entry** ["26->['E1', 'E2', 'E1', 'E2']"]
- tl-01 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 10, "ungrounded": ["73.2%"]}
- tl-01 r0 haiku cli: **numbers.grounded_or_derived** ["73.2%"]
- tl-01 r0 haiku cli: **citations.number_in_cited_entry** ["73.2%->['E1']"]
- tl-02 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 27, "ungrounded": ["42,000", "70,000"]}
- tl-02 r0 haiku cli: **numbers.grounded_or_derived** ["42,000", "70,000"]
- tl-02 r0 haiku cli: **citations.number_in_cited_entry** ["42,000->['E1']", "70,000->['E1']"]
- tl-03 r0 haiku cli: **citations.number_in_cited_entry** ["0.01%->['E1']", "0.01%->['E1']"]
- tl-05 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 25, "ungrounded": ["348,460", "43.8%", "18.2%", "14.2%", "9.0%", "284,344", "82%", "63,845", "30", "2,500"]}
- tl-05 r0 haiku cli: **numbers.grounded_or_derived** ["43.8%", "18.2%", "14.2%", "9.0%", "284,344", "82%", "63,845", "2,500"]
- tl-05 r0 haiku cli: **citations.numbers_near_citation** ["152,734", "43.8%", "63,491", "18.2%", "49,651", "14.2%", "31,531", "9.0%", "23,338", "6.7%"]
- tl-05 r0 haiku cli: **citations.number_in_cited_entry** ["348,460->['E1']", "43.8%->['E1', 'E1', 'E1', 'E1', 'E1']", "18.2%->['E1', 'E1', 'E1', 'E1', 'E1']", "14.2%->['E1', 'E1', 'E1', 'E1', 'E1']", "9.0%->['E1', 'E1', 'E1', 'E1', 'E1']", "284,344->['E1']", "82%->['E1']", "63,845->['E1']", "30->['E1']", "2,500->['E1']"]
- tl-06 r0 haiku cli: **citations.numbers_near_citation** ["2,099", "7,001", "11", "8,659", "18", "4,962", "25", "1,830", "10,953", "36,259"]
- tl-07 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 34, "ungrounded": ["156,732", "1,600", "300", "100", "300"]}
- tl-07 r0 haiku cli: **numbers.grounded_or_derived** ["156,732", "1,600"]
- tl-07 r0 haiku cli: **citations.number_in_cited_entry** ["156,732->['E1']", "1,600->['E1']", "300->['E1']"]
- tl-08 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 3, "ungrounded": ["4,955,783"]}
- tl-08 r0 haiku cli: **citations.number_in_cited_entry** ["4,955,783->['E1']"]
- tl-09 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 16, "ungrounded": ["184"]}
- tl-09 r0 haiku cli: **numbers.grounded_or_derived** ["184"]
- tl-09 r0 haiku cli: **citations.numbers_near_citation** ["16"]
- tl-09 r0 haiku cli: **citations.number_in_cited_entry** ["0.09%->['E2']", "184->['E1']"]
- tl-10 r0 haiku cli: **citations.numbers_near_citation** ["626,450", "73%", "228,536", "27%", "206"]
