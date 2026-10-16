## Decision proposal
**Chosen option:** The worked investigation is **"Start-up path failures"**.
- **Lead:** the BGL `/p/gb1` Lustre mount-failure wave (KERNMNTF), 2005-08-31 18:41 to 2005-09-16 20:35. It has 26,426 Log Entries and forms 83 Incidents on 12 days under the #1 definition (gap 300 s, `merge_scope=all`). 7 of those Incidents hit all 1,024 I/O nodes.
- **Second Dataset:** the 4 labelled OpenStack slow spawns. Timing finds 4 of 4; text search finds none. The unlabelled d7d68893 is shown as a Hypothesis.
- **Manager trap:** the KERNDTLB single-node storm, 152,182 entries that form 1 Incident.
- **Manager answer:** two Data Scopes.
  - Inside the window, the filesystem mount problem is priority 1.
  - Over the whole Base Corpus, the answer is the #4 top-5 band: KERNMC, APPSEV, KERNRTSP, KERNMNTF and KERNSOCK. It holds in 97 to 100% of 2,000 random weightings.
- **Numbers:** every storyline number comes from #1 Incidents and is pinned by golden SQL.

**Rationale:** Both Claudes now lead with the `/p/gb1` wave, and NoNaim95's Deliberation withdrew the DTLB-led version. It is the only candidate that carries all three Roles on one problem:
- one searchable phrase for the Engineer;
- recurrence and Spread for the Team Lead: 83 Incidents, up to 1,024 nodes, a sharp start and end;
- a systemic item for the Manager, set against the loud single-node storm.

The OpenStack anomalies add the second Dataset with labelled ground truth. They also show something only aggregation can find, because their text has no ERROR lines. The remaining differences are settled by measurement:
- KERNMNTF as a category is still active to 2006-01-03, so it stays in the whole-corpus band.
- APPSEV is rising since November, but the multiplier depends on the baseline.
- The full-text syntax gap is a SereneDB version difference.

**Rejected options:**
- KERNDTLB storm as lead: one node on one day, with no Team Lead or Manager story. It stays as the trap.
- OpenStack slow spawns alone: 4 instances in 2 h 17 m, so there is no recurrence.
- KERNSTOR partition storm: 8 Incidents in 5 days. It is probably one job's core dumps, which is hard to tell as a story.
- APPSEV chronic as lead: no clear before and after, and no single drill-down target. It stays in the Manager band.

**Decisions the humans must make:**
1. Which SereneDB version the organisers run. The expression `CREATE TEXT SEARCH DICTIONARY … AS split_text(...)` fails on 26.09.1.3 and is documented as new in 26.10.1. This decides the full-text dictionary DDL for the Engineer phrase search.
2. Whether to accept the two-Data-Scope Manager answer, with a top-5 band instead of a single #1, on stage.
3. Whether to show the KERNDTLB trap (about 30 s) and the optional `get_context` cameo inside the 5-minute demo.
4. Whether the thematic cross-Dataset link ("start-up path") is enough for minimum requirement 2. The README must say it is not causal.
