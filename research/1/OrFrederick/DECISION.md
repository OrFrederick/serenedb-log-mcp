## Decision proposal

**Chosen option:** An **Incident** is a maximal run of Log Entries with the same **Pattern** (BGL alert category, otherwise the template id from #6), merged across sources within `merge_scope` (default `all`; also `node`, `midplane`, `rack`), where consecutive entries are at most `gap_s` apart (default 300 s, measured on UTC time). Every Incident carries a **Spread** (distinct nodes, node cards, midplanes, racks, `top_node_share`) and a **Shape** label (single-node storm, single-node isolated, one midplane, multi-midplane, machine-wide). A **Burst** is the same rule per source. Derived views: Recurrence/Chronic/New Pattern, **Cascade** (always a Hypothesis, with lift and a minimum lag above 1 s), optional **Episode**, and for OpenStack **Request Failure**, **Latency Degradation** and **Phase-duration anomaly**. One `list_incidents` tool (`filter, pattern_key, gap_s, merge_scope, sort`) computes Incidents at query time over the filtered set; the Pattern id column and the default Incident table are precomputed at load time.

**Rationale:** Both Claudes independently reproduced Oliner & Stearley's published BGL count of 1,202 distinct failures from 348,460 alert lines exactly at 300 s (plateau 298–311 s), and I re-ran it again (1,202). The definition separates the cases the brief asks about: one node in a loop (KERNDTLB, 152,182 lines = 1 Incident on 1 node), machine-wide problems (APPSEV, 51 of 136 Incidents on ≥ 64 of 128 midplanes), chronic trickles (KERNMC, 32 of 32 weeks) and cascades. The spatial rule dominates the count (~150×) and the gap barely does (~1.8×), so `merge_scope` is a parameter. Query-time grouping is fast on filtered or alert-level data (0.3–1.2 s with Spread), but not over all 4.7M rows (37–79 s with Spread under shared load), hence the load-time precomputation.

**Rejected options:**
- Raw line counts: one looping card is 44% of all BGL alert lines.
- Fixed time buckets: split Incidents at bucket edges; kept for trend charts only.
- Per-node Bursts as the headline unit: 190,290 at 300 s, overcounts systemic problems ~150×.
- O&S filter at the stated T = 5 s: gives 1,643, not the published 1,202.
- Component as the Pattern key: merges unrelated messages (one KERNEL "Incident" of 249,864 lines).
- Fully query-time over the whole corpus: 37–79 s with Spread.
- Change-point or ML models (PELT, BOCPD, DeepLog): not expressible as SQL Evidence; optional later.

**Decisions the humans must make:**
1. Add Pattern, Burst, Incident, Spread, Shape, Recurrence/Chronic/New Pattern, Cascade, Episode, Request Failure, Latency Degradation and Phase-duration anomaly to GLOSSARY.md (and decide whether "Pattern" or #6's term names the key).
2. Expose `gap_s` as a tool parameter (stable band 60–600 s) or fix it at 300 s.
3. Choose the lead story for #5: the one-node DTLB loop or the machine-wide APPSEV recurrence.
