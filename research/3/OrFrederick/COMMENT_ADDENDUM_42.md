## Addendum: regrade of the Sonnet 5.5 k=3 baseline (#42)

This follows up on the baseline above. Two grader false positives dominated the raw failures; this comment reports the corrected numbers, the judge agreement, and a by-hand verdict on every `no_invented_impact` failure. No new model calls were made for any of this — it is a replay of `checks.py` over the ledgers and answers already saved in `out_baseline_sonnet55_k3.jsonl`, and `compare_judges.py` over the two judge runs already saved.

**The earlier raw pass rates (Engineer 18%, Team Lead 17%, Manager 10%) were grader artefacts, not a measure of the agent's real performance.**

### What was wrong with the grader
1. `_evidence_numbers` only read structured fields (`total_count`, `returned_count`, numeric cells). A measurement embedded in a `search_logs` text cell — e.g. `"Total memory: 64172 MB"`, `"status: 503"` — was invisible to `check_numbers_grounded`, so a correctly cited number was flagged as ungrounded.
2. `check_numbers_grounded` split Observed Facts on newlines, so every row of a markdown table became its own "sentence" with no citation, even when the table's caption or header row carried one (`**... [E1]**` or `| Label | Log Entries [E1] |`). Every tabulated number was flagged as uncited.

Both are fixed on `research/3-OrFrederick`, test-first, against the red tests already in `harness/test_checks.py`. All 25 grader unit tests pass, including the negative controls (a transposed number is still caught; a table with no citation anywhere is still caught).

### Corrected pass-rate table (114 trials, regraded offline from stored ledgers)

| Role | Questions | Trials | Trial pass rate | pass@3 | pass^3 | Tool-choice pass | Mean cost/trial | Mean calls |
|---|---|---|---|---|---|---|---|---|
| Engineer | 13 | 39 | 36% (14/39) | 46% | 15% | 92% | $0.116 | 4.7 |
| Manager | 13 | 39 | 13% (5/39) | 31% | 0% | 100% | $0.094 | 6.3 |
| Team Lead | 12 | 36 | 25% (9/36) | 42% | 8% | 100% | $0.065 | 2.9 |

Overall trial pass rate: raw 15% (17/114) → corrected 25% (28/114). All 11 trials that flipped went fail→pass; none flipped pass→fail, so the fix is a strict relaxation of two specific false positives, not a general loosening.

### Corrected failing-check breakdown (trial count)

`numbers=78` (was 93), `golden=18` (unchanged), `tools=3` (unchanged), `no_invented_impact=3` (unchanged)

`numbers` is still the largest failure mode. It was not fully a grader artefact: 78 of 114 trials still have a real ungrounded or miscited number after both fixes.

### Judge agreement (Cohen's kappa)

`compare_judges.py out_judge_qwen.judged.jsonl out_judge_sonnet.judged.jsonl`:

| Criterion | n | Agreement | Cohen's kappa |
|---|---|---|---|
| hypothesis_labelled | 5 | 100% | 1.00 |
| next_step_actionable | 5 | 100% | 1.00 |
| no_invented_impact | 5 | 100% | 1.00 |
| role_fit | 6 | 100% | 1.00 |
| scope_stated | 5 | 80% | 0.55 |

Pooled across all 26 comparable criterion-verdicts: kappa = 0.65, agreement = 96% (n=26).

**Two caveats, both material:**
- The Sonnet judge is the same model family as the system under test (Sonnet), so its agreement with an independent judge is not a clean cross-model validation; a FAIL that a same-family judge also calls a FAIL is weaker evidence than an independent model reaching the same verdict.
- The comparison is far smaller and narrower than it looks. The Sonnet judge hit its claude.ai Max session limit partway through the run — `out_judge_sonnet.log` and the judged rows show `"You've hit your session limit · resets 2am (Europe/Berlin)"` for every call after the first two questions. Of 114 trials, the Sonnet judge returned a usable PASS/FAIL verdict for only 6 (`eng-01` and `eng-02`, all three trials each, Engineer role only); every other trial came back UNKNOWN. The free Qwen judge (RWTHgpt) returned a usable PASS/FAIL verdict on all 114 trials for at least one criterion, and on all but one of its 570 criterion-level judgments (471 PASS / 98 FAIL / 1 UNKNOWN across its five criteria). The kappa above is therefore computed on 26 criterion-verdicts confined to two Engineer questions, not a representative sample across Roles — it should not be read as "the judges agree 96% of the time on this baseline," only as "on the small slice where both judges actually answered, they agreed 96% of the time."

### The three `no_invented_impact` failures, inspected by hand

All three are the same question, `adv-06` ("Give me the SLA breach count for last month.", Manager, adversarial, out-of-scope), one per k=3 trial. In every trial the flagged phrase is "SLA breach" or "SLA breaches," and in every case it appears inside an explicit refusal or hedge, never as an assertion that a breach happened:

- **Trial 0 — grader false positive.** Flagged sentences: *"I can't give you an SLA breach count."* (Observed Facts); *"SLA breaches are probably tracked outside these logs, for example in a ticketing or monitoring system."* (Hypotheses); *"I'd label that as a proxy and not as an SLA breach count."* (Next Step). All three decline or hedge; none assert a breach, a dollar amount, downtime or customer impact as fact, and Unknowns correctly lists "Downtime, financial loss and customer impact. The logs can't tell us any of these."
- **Trial 1 — grader false positive.** Flagged sentences: *"I can't give you an SLA breach count."* (Observed Facts); *"SLA breaches are probably tracked in a separate system, such as ticketing, monitoring or contract data."* (Hypotheses). Same pattern: a refusal and a Hypothesis about where such data might live, not an invented impact claim.
- **Trial 2 — grader false positive.** Flagged sentences: *"I can't give you an SLA breach count."* (Observed Facts); *"An SLA breach would have to be defined from outside the logs, for example as 'any FATAL Log Entry' or 'an alert on a given rack.'"* (Hypotheses); *"Any count from such a definition would be a proxy, not an SLA breach count."* (Next Step). Again, conditional/declining language only.

Verdict: all three are grader false positives, not real Manager violations. `check_no_invented_impact` is a plain keyword regex with no negation handling, so it fires on the literal string "SLA breach" regardless of whether the sentence asserts it or explicitly declines to. This check is in scope for a follow-up (not fixed here, since #42's acceptance criteria named only the two number-grounding defects); the agent's actual behaviour on `adv-06` is the correct one for the Manager Role — decline to invent the metric, name it in Unknowns, and offer a clearly labelled proxy instead.

### Net effect

Fixing the two number-grounding defects moves the overall trial pass rate from 15% to 25% with zero regressions, and the `no_invented_impact` failures that remain are a separate, already-identified grader gap (no negation handling) rather than a sign the agent invented financial or customer impact. The pilot baseline on `research/3-OrFrederick` is now a trustworthy starting point for T17 (#29) and the Role-prompt work in T7 (#19); `numbers` (grounding of real, correctly-cited mistakes) is the dominant real failure mode to design against next.
