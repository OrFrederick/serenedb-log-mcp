Regrade of out_baseline_sonnet55_k3.jsonl using the #42 grader fixes (no model calls; 114 trials replayed from stored ledgers).

Overall trial pass rate: raw 15% -> corrected 25%.

| Role | Questions | Trials | Trial pass rate | pass@3 | pass^3 | Tool-choice pass | Mean cost/trial | Mean calls |
|---|---|---|---|---|---|---|---|---|
| Engineer | 13 | 39 | 36% | 46% | 15% | 92% | $0.116 | 4.7 |
| Manager | 13 | 39 | 13% | 31% | 0% | 100% | $0.094 | 6.3 |
| Team Lead | 12 | 36 | 25% | 42% | 8% | 100% | $0.065 | 2.9 |

Failing checks (trial count): numbers=78, golden=18, tools=3, no_invented_impact=3
