## Pass rate per assertion

| Assertion | Pass | N | Rate |
|---|---|---|---|
| answer.forbid_regex | 9 | 9 | 100% |
| citations.number_in_cited_entry | 37 | 54 | 69% |
| citations.numbers_in_cited_block | 53 | 54 | 98% |
| citations.numbers_near_citation | 28 | 54 | 52% |
| citations.resolve | 54 | 54 | 100% |
| glossary.no_entry_count_as_incidents | 54 | 54 | 100% |
| infra.tool_errors | 54 | 54 | 100% |
| ledger.one_entry_per_call | 54 | 54 | 100% |
| manager.no_invented_impact | 27 | 27 | 100% |
| numbers.golden_in_answer | 18 | 18 | 100% |
| numbers.grounded_in_ledger | 34 | 54 | 63% |
| numbers.grounded_or_derived | 44 | 54 | 81% |
| structure.sections | 54 | 54 | 100% |
| tools.any_of | 18 | 18 | 100% |
| tools.args | 27 | 27 | 100% |
| tools.forbidden | 17 | 18 | 94% |
| tools.only_mcp | 54 | 54 | 100% |
| tools.required | 36 | 36 | 100% |
| unknowns.flagged | 27 | 27 | 100% |

## Per question (all hard checks pass)

| Question | Model | Backend | Runs | All-pass | Tool sets across repeats | Calls | $/run (mean) | Wall s (mean) |
|---|---|---|---|---|---|---|---|---|
| adv-01 | default | cli | 2 | 0/2 | count_by,describe_corpus,trend | [4, 6] | 0.0756 | 20.3 |
| adv-01 | haiku | cli | 3 | 1/3 | count_by,describe_corpus / count_by,describe_corpus,trend / describe_corpus,search_logs,trend | [3, 4, 2] | 0.0015 | 20.7 |
| adv-01 | haiku | sdk | 1 | 0/1 | count_by,describe_corpus,trend | [3] | 0.0018 | 12.8 |
| adv-01 | sonnet | cli | 3 | 3/3 | count_by,describe_corpus,trend | [3, 3, 3] | 0.0259 | 20.1 |
| adv-02 | default | cli | 2 | 0/2 | count_by,describe_corpus | [3, 3] | 0.0478 | 16.1 |
| adv-02 | haiku | cli | 3 | 1/3 | count_by,describe_corpus / describe_corpus | [1, 2, 1] | 0.0007 | 11.5 |
| adv-02 | haiku | sdk | 1 | 1/1 | describe_corpus | [1] | 0.0008 | 9.4 |
| adv-02 | sonnet | cli | 3 | 0/3 | count_by,describe_corpus | [2, 2, 2] | 0.0142 | 12.5 |
| eng-01 | default | cli | 2 | 0/2 | count_by,get_context,search_logs / count_by,get_context,search_logs,trend | [4, 6] | 0.1131 | 27.6 |
| eng-01 | haiku | cli | 3 | 0/3 | count_by,get_context,search_logs | [3, 3, 3] | 0.0021 | 17.7 |
| eng-01 | haiku | sdk | 1 | 0/1 | count_by,get_context,search_logs | [3] | 0.0022 | 12.7 |
| eng-01 | sonnet | cli | 3 | 0/3 | get_context,search_logs | [2, 2, 2] | 0.0328 | 17.9 |
| mgr-04 | default | cli | 2 | 1/2 | count_by,describe_corpus,trend / count_by,trend | [6, 7] | 0.0827 | 22.2 |
| mgr-04 | haiku | cli | 3 | 2/3 | count_by / count_by,describe_corpus | [7, 8, 4] | 0.0020 | 49.7 |
| mgr-04 | haiku | sdk | 1 | 1/1 | count_by | [2] | 0.0013 | 11.7 |
| mgr-04 | sonnet | cli | 3 | 3/3 | count_by,describe_corpus | [7, 7, 7] | 0.0373 | 50.2 |
| tl-01 | default | cli | 2 | 0/2 | count_by,describe_corpus,trend | [5, 4] | 0.0642 | 22.4 |
| tl-01 | haiku | cli | 3 | 3/3 | count_by / count_by,describe_corpus | [1, 2, 2] | 0.0008 | 12.7 |
| tl-01 | haiku | sdk | 1 | 0/1 | count_by,describe_corpus | [2] | 0.0012 | 11.3 |
| tl-01 | sonnet | cli | 3 | 3/3 | count_by,describe_corpus | [2, 2, 2] | 0.0140 | 12.7 |
| tl-10 | default | cli | 2 | 1/2 | count_by,describe_corpus / count_by,describe_corpus,trend | [2, 3] | 0.0394 | 18.0 |
| tl-10 | haiku | cli | 3 | 1/3 | count_by,describe_corpus | [2, 2, 2] | 0.0010 | 39.4 |
| tl-10 | haiku | sdk | 1 | 0/1 | count_by,describe_corpus | [2] | 0.0011 | 11.5 |
| tl-10 | sonnet | cli | 3 | 2/3 | count_by,describe_corpus | [2, 2, 2] | 0.0140 | 39.5 |

Total runs 54, total $1.2931, mean $0.0239/run, median wall 17.6s, max wall 97.6s

## Pass rate per assertion x model

| Assertion | gate? | default/cli | haiku/cli | haiku/sdk | sonnet/cli |
|---|---|---|---|---|---|
| answer.forbid_regex | gate | 2/2 | 3/3 | 1/1 | 3/3 |
| citations.number_in_cited_entry | style | 4/12 | 15/18 | 3/6 | 15/18 |
| citations.numbers_in_cited_block | gate | 12/12 | 17/18 | 6/6 | 18/18 |
| citations.numbers_near_citation | style | 4/12 | 11/18 | 2/6 | 11/18 |
| citations.resolve | gate | 12/12 | 18/18 | 6/6 | 18/18 |
| glossary.no_entry_count_as_incidents | gate | 12/12 | 18/18 | 6/6 | 18/18 |
| infra.tool_errors | style | 12/12 | 18/18 | 6/6 | 18/18 |
| ledger.one_entry_per_call | gate | 12/12 | 18/18 | 6/6 | 18/18 |
| manager.no_invented_impact | gate | 6/6 | 9/9 | 3/3 | 9/9 |
| numbers.golden_in_answer | gate | 4/4 | 6/6 | 2/2 | 6/6 |
| numbers.grounded_in_ledger | style | 3/12 | 14/18 | 3/6 | 14/18 |
| numbers.grounded_or_derived | gate | 6/12 | 15/18 | 6/6 | 17/18 |
| structure.sections | gate | 12/12 | 18/18 | 6/6 | 18/18 |
| tools.any_of | gate | 4/4 | 6/6 | 2/2 | 6/6 |
| tools.args | gate | 6/6 | 9/9 | 3/3 | 9/9 |
| tools.forbidden | gate | 3/4 | 6/6 | 2/2 | 6/6 |
| tools.only_mcp | gate | 12/12 | 18/18 | 6/6 | 18/18 |
| tools.required | gate | 8/8 | 12/12 | 4/4 | 12/12 |
| unknowns.flagged | gate | 6/6 | 9/9 | 3/3 | 9/9 |

## Gate pass (all gate checks) per question x model, with repeat consistency

| Question | default/cli | haiku/cli | haiku/sdk | sonnet/cli |
|---|---|---|---|---|
| adv-01 | 0/2 | 2/3 (flaky) | 1/1 | 3/3 |
| adv-02 | 1/2 (flaky) | 2/3 (flaky) | 1/1 | 3/3 |
| eng-01 | 1/2 (flaky) | 2/3 (flaky) | 1/1 | 2/3 (flaky) |
| mgr-04 | 2/2 | 3/3 | 1/1 | 3/3 |
| tl-01 | 0/2 | 3/3 | 1/1 | 3/3 |
| tl-10 | 1/2 (flaky) | 2/3 (flaky) | 1/1 | 3/3 |

- **default/cli**: gate pass 5/12 (42%); $0.8457 total, $0.0705/run; wall median 21.6s (max 29.5s); tool calls/run mean 4.4

- **haiku/cli**: gate pass 14/18 (78%); $0.0244 total, $0.0014/run; wall median 17.5s (max 94.3s); tool calls/run mean 2.9

- **haiku/sdk**: gate pass 6/6 (100%); $0.0084 total, $0.0014/run; wall median 11.6s (max 12.8s); tool calls/run mean 2.2

- **sonnet/cli**: gate pass 17/18 (94%); $0.4147 total, $0.0230/run; wall median 17.5s (max 97.6s); tool calls/run mean 3.0

## Failures

- adv-01 r0 None cli: **numbers.grounded_in_ledger** {"numbers": 23, "ungrounded": ["1,500", "40,000"]}
- adv-01 r0 None cli: **numbers.grounded_or_derived** ["1,500", "40,000"]
- adv-01 r0 None cli: **citations.numbers_near_citation** ["1,240,932", "381,561", "152,669", "101,380", "95,881", "79,781", "548,475", "535,054", "13,409", "12"]
- adv-01 r0 None cli: **citations.number_in_cited_entry** ["1,500->['E2']", "40,000->['E2']"]
- adv-01 r1 None cli: **numbers.grounded_in_ledger** {"numbers": 27, "ungrounded": ["1,500", "40,000", "250"]}
- adv-01 r1 None cli: **numbers.grounded_or_derived** ["1,500", "40,000", "250"]
- adv-01 r1 None cli: **citations.numbers_near_citation** ["535,054", "548,475", "152,659", "63,488", "5,983", "381,561", "152,669", "101,380", "95,881"]
- adv-01 r1 None cli: **citations.number_in_cited_entry** ["28->['E1']", "1,500->['E2']", "40,000->['E2']"]
- adv-01 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 17, "ungrounded": ["2,500", "24", "27"]}
- adv-01 r0 haiku cli: **numbers.grounded_or_derived** ["2,500"]
- adv-01 r0 haiku cli: **citations.number_in_cited_entry** ["2,500->['E2']", "12->['E2']", "24->['E2']", "27->['E2']"]
- adv-01 r1 haiku cli: **citations.numbers_near_citation** ["685,088", "548,475", "2,568", "2,434", "2,351", "15"]
- adv-01 r0 haiku sdk: **numbers.grounded_in_ledger** {"numbers": 11, "ungrounded": ["500"]}
- adv-01 r0 haiku sdk: **citations.numbers_near_citation** ["535,054", "13,409", "500"]
- adv-01 r0 haiku sdk: **citations.number_in_cited_entry** ["500->['E1', 'E2', 'E3', 'E2']"]
- adv-02 r0 None cli: **numbers.grounded_in_ledger** {"numbers": 31, "ungrounded": ["348,460", "348,460", "41", "22", "265,876", "348,460", "22"]}
- adv-02 r0 None cli: **numbers.grounded_or_derived** ["41", "265,876"]
- adv-02 r0 None cli: **citations.numbers_near_citation** ["4,399,503", "348,460", "41", "20", "22", "855,195"]
- adv-02 r0 None cli: **citations.number_in_cited_entry** ["348,460->['E1', 'E2', 'E2']", "41->['E1', 'E2', 'E2']", "22->['E3']"]
- adv-02 r1 None cli: **numbers.grounded_in_ledger** {"numbers": 19, "ungrounded": ["348,460", "348,460", "348,460"]}
- adv-02 r1 None cli: **citations.numbers_near_citation** ["152,734", "63,491", "49,651", "31,531", "23,338", "6,145", "5,983", "626,450", "228,536", "206"]
- adv-02 r1 None cli: **citations.number_in_cited_entry** ["348,460->['E2']"]
- adv-02 r1 haiku cli: **numbers.grounded_in_ledger** {"numbers": 9, "ungrounded": ["988,477"]}
- adv-02 r1 haiku cli: **numbers.grounded_or_derived** ["988,477"]
- adv-02 r2 haiku cli: **citations.numbers_near_citation** ["4,747,963", "4,747,963"]
- adv-02 r0 sonnet cli: **numbers.grounded_in_ledger** {"numbers": 12, "ungrounded": ["967,550"]}
- adv-02 r0 sonnet cli: **citations.numbers_near_citation** ["3,735,813", "855,195", "112,355", "23,357", "19,213", "1,714", "306"]
- adv-02 r1 sonnet cli: **citations.numbers_near_citation** ["3,735,813", "855,195", "112,355", "23,357", "19,213", "1,714", "306"]
- adv-02 r2 sonnet cli: **citations.numbers_near_citation** ["3,735,813", "855,195", "112,355", "23,357", "19,213", "1,714", "306"]
- eng-01 r0 None cli: **numbers.grounded_in_ledger** {"numbers": 14, "ungrounded": ["74"]}
- eng-01 r0 None cli: **citations.numbers_near_citation** ["13,409", "6,803", "65641", "74"]
- eng-01 r0 None cli: **citations.number_in_cited_entry** ["74->['E3']"]
- eng-01 r1 None cli: **tools.forbidden** ["trend"]
- eng-01 r1 None cli: **numbers.grounded_in_ledger** {"numbers": 28, "ungrounded": ["74", "70"]}
- eng-01 r1 None cli: **citations.numbers_near_citation** ["13,409", "6,803", "4988", "65641", "74", "4988", "5019", "5020", "5027", "5028"]
- eng-01 r1 None cli: **citations.number_in_cited_entry** ["74->['E3']", "4988->['E4', 'E5']", "5019->['E4', 'E5']", "5020->['E4', 'E5']", "5027->['E4', 'E5']", "5028->['E4', 'E5']", "65640->['E4', 'E5']"]
- eng-01 r0 haiku cli: **citations.numbers_near_citation** ["4980", "4983", "4984", "4987"]
- eng-01 r1 haiku cli: **citations.numbers_near_citation** ["20,217", "4978", "4983", "31", "4984", "4987"]
- eng-01 r2 haiku cli: **citations.numbers_near_citation** ["4983", "4984", "4985", "4986", "4987", "4989", "4990", "4991", "4992", "4993"]
- eng-01 r2 haiku cli: **citations.numbers_in_cited_block** ["4983", "4984", "4985", "4986", "4987", "4989", "4990", "4991", "4992", "4993"]
- eng-01 r0 sonnet cli: **numbers.grounded_in_ledger** {"numbers": 17, "ungrounded": ["36"]}
- eng-01 r0 sonnet cli: **citations.numbers_near_citation** ["20,217", "4988", "4980", "4987", "4980", "4996", "4989", "4996"]
- eng-01 r0 sonnet cli: **citations.number_in_cited_entry** ["36->['E2']"]
- eng-01 r1 sonnet cli: **numbers.grounded_in_ledger** {"numbers": 13, "ungrounded": ["255"]}
- eng-01 r1 sonnet cli: **numbers.grounded_or_derived** ["255"]
- eng-01 r1 sonnet cli: **citations.numbers_near_citation** ["20,217", "4988", "4980", "4987", "4989", "4996", "255"]
- eng-01 r1 sonnet cli: **citations.number_in_cited_entry** ["255->['E1', 'E1', 'E2', 'E2', 'E2', 'E2']"]
- eng-01 r2 sonnet cli: **numbers.grounded_in_ledger** {"numbers": 21, "ungrounded": ["36", "20,200"]}
- eng-01 r2 sonnet cli: **citations.numbers_near_citation** ["20,217", "4980", "4983", "36", "31", "4984", "4987", "257", "385", "65"]
- eng-01 r2 sonnet cli: **citations.number_in_cited_entry** ["36->['E1', 'E1', 'E2', 'E2']"]
- eng-01 r0 haiku sdk: **numbers.grounded_in_ledger** {"numbers": 18, "ungrounded": ["40"]}
- eng-01 r0 haiku sdk: **citations.numbers_near_citation** ["4980", "4987"]
- eng-01 r0 haiku sdk: **citations.number_in_cited_entry** ["20->['E3']", "40->['E3']"]
- mgr-04 r1 None cli: **citations.number_in_cited_entry** ["4,324,651->['E6']"]
- mgr-04 r2 haiku cli: **citations.numbers_near_citation** ["4,324,651", "626,450"]
- tl-01 r0 None cli: **numbers.grounded_in_ledger** {"numbers": 39, "ungrounded": ["32k", "535k", "30"]}
- tl-01 r0 None cli: **numbers.grounded_or_derived** ["32k", "535k"]
- tl-01 r0 None cli: **citations.numbers_near_citation** ["535k"]
- tl-01 r0 None cli: **citations.number_in_cited_entry** ["32k->['E5']", "535k->['E3', 'E3', 'E5', 'E3', 'E5', 'E4', 'E4', 'E4', 'E4', 'E4', 'E4']"]
- tl-01 r1 None cli: **numbers.grounded_in_ledger** {"numbers": 36, "ungrounded": ["40k", "80k"]}
- tl-01 r1 None cli: **numbers.grounded_or_derived** ["40k", "80k"]
- tl-01 r1 None cli: **citations.numbers_near_citation** ["37"]
- tl-01 r0 haiku sdk: **numbers.grounded_in_ledger** {"numbers": 11, "ungrounded": ["854,986"]}
- tl-01 r0 haiku sdk: **citations.numbers_near_citation** ["626,450", "73.3%", "228,536", "26.7%", "206", "0.02%"]
- tl-01 r0 haiku sdk: **citations.number_in_cited_entry** ["854,986->['E1']"]
- tl-10 r1 None cli: **numbers.grounded_in_ledger** {"numbers": 17, "ungrounded": ["42k"]}
- tl-10 r1 None cli: **numbers.grounded_or_derived** ["42k"]
- tl-10 r0 haiku cli: **numbers.grounded_in_ledger** {"numbers": 8, "ungrounded": ["855,186"]}
- tl-10 r0 haiku cli: **numbers.grounded_or_derived** ["855,186"]
- tl-10 r0 haiku cli: **citations.numbers_near_citation** ["626,450", "228,536", "206"]
- tl-10 r0 haiku cli: **citations.number_in_cited_entry** ["855,186->['E1']"]
- tl-10 r1 haiku cli: **numbers.grounded_in_ledger** {"numbers": 8, "ungrounded": ["854,986"]}
- tl-10 r1 haiku cli: **citations.number_in_cited_entry** ["854,986->['E1']"]
- tl-10 r2 sonnet cli: **citations.numbers_near_citation** ["626,450", "228,536", "206"]
- tl-10 r0 haiku sdk: **citations.numbers_near_citation** ["626,450", "73.3%", "855,195", "228,536", "26.7%", "206"]
