# #6 offline parser experiments (subagent report, saved by parent). Large outputs: not committed.

## Metrics
- GA, FGA, PA and FTA are ported from the loghub-2.0 code.
- The port reproduces the published full-data numbers exactly:
  - Drain BGL: 0.919 / 0.624 / 0.407
  - Drain OpenStack: 0.752 / 0.007
  - IPLoM BGL: 0.896 / 0.692

## Results
- **Drain3 0.9.11, default settings**
  - BGL: GA 0.925, FGA 0.639.
  - OpenStack: GA 0.345.
- **Drain3 0.9.11, tuned**
  - Settings: NODE / UUID / REQ / IP masks, plus one shared hex+num mask. BGL uses sim 0.7 and depth 4. OpenStack uses sim 0.9 and depth 5.
  - BGL: GA 0.946, FGA 0.852, PA 0.905. Speed 40k lines/s, 149 MB.
  - OpenStack: GA 0.972, FGA 0.938. Speed 63k lines/s.
  - The tuning used the ground-truth labels, so these scores are optimistic.
- **Rust drain3 crate 0.3.0**
  - Accuracy is the same as tuned Drain3.
  - Speed is 265–340k lines/s (BGL in 14–18 s).
  - Empty messages return `Err`.
- **logparser3 Brain**
  - OpenStack: 1.0 / 1.0.
  - BGL: FGA 0.312, 3.3 GB of memory, 352 s.
- **IPLoM, AEL and Spell:** worse than tuned Drain3. Spell took 1 h on OpenStack.
- **Masks plus GROUP BY**
  - BGL: GA 0.900, but FGA 0.029 (17,854 groups).
  - The in-DB agent's tuned SQL masks get much higher scores; see `../in_db/findings.md`.
- **Loghub-2.0 templates as an RE2 hybrid matcher**
  - Strict matching first, loose fallback second.
  - Coverage: 97.54% of raw BGL and 99.91% of raw OpenStack.
  - Speed: about 4 s for all of BGL.
  - 8 lines are ambiguous; ordering by specificity resolves all of them.
  - Deterministic.
  - The regex tables are in `results/*_templates_regex_strict.tsv`.

## Raw vs lh2
lh2 is a strict, order-preserving subset of the raw corpus. It removed 116,702 BGL lines:

| Removed | Lines |
|---|---|
| Empty | 34,470 |
| Hex dumps | 17,033 |
| No letters | 2,505 |
| Worded lines whose templates are absent from lh2 | 62,694 |

About 16.2k of the removed lines are alert lines: APPSEV 7,832, APPREAD 5,983, APPUNAV 2,048 and others.

On OpenStack, 188 lines were removed: 184 traceback lines and 4 pending-task lines.

## Determinism
- Drain is identical for the same input order. Shuffling the input changes cluster ids, while the partition stays 99.99% equal.
- So never store Drain's cluster_id. Use a content hash instead.

## Recommendation
1. Use the lh2 RE2 matcher as the primary method.
2. Run tuned Drain3 on the leftover lines, in `line_no` order, with ids of the form `D:<sha1(template)[:8]>` and an EMPTY id for empty messages.
3. Store `template_id` and `template_source` per Log Entry.
