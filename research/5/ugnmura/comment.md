## Research: ugnmura's Codex

**Recommendation:** Lead with **four injected-anomaly OpenStack VM executions whose explicitly instance-tagged Log Entries are all INFO**, and use BGL as the short, measured contrast showing why raw error volume is a poor investigation priority. Search for “build instance,” aggregate every recorded build, then inspect the four labelled instances. Present a BGL single-node repetition group beside a much smaller but broadly distributed category group. These are independent historical examples within the same Base Corpus, not a claimed cross-Dataset causal chain. This supports distinct Engineer, Team Lead and Manager decisions while keeping ground-truth claims precise.

**Confidence:** High for the four upstream labels and measured descriptive comparisons; medium for this editorial demo choice. BGL categories validate alert tagging, not a list of distinct operational Incidents. No source establishes customer impact, downtime, financial loss or the precise cause of every candidate.

### Options compared

Scores are editorial judgments, **not measured operational severity**. Criteria are support for all Roles, search plus aggregation, before/after clarity, external verification and presentation clarity.

| Rank / candidate | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| **1. OpenStack labelled VM build degradation** | Strong across all criteria | Four independently labelled anomalous instance IDs; duration anomaly is invisible to ERROR-only filtering; clear instance drill-down and population comparison | Only four labelled examples; network-throttling paper is context, not per-UUID cause annotation; no customer-impact data | `openstack-profile.json`, `serenedb-openstack-results.json`: `labelled_instance_sessions`, `build_rows`, threshold queries; [upstream labels](https://github.com/aaron-y-chen/deeplog/blob/master/example/data/OpenStack/anomaly_labels.txt) |
| **2. BGL KERNSTOR spatial group, June 14** | Strong for spread and prioritisation | 20,480 Log Entries on 20,480 nodes, 20 racks, 40 midplanes over 534 epoch seconds; concise exact-message search | Same-category overlap does not establish one shared underlying failure; no affected-user verification | `bgl_top_groups_5`, `bgl_top_groups_300`; full `bgl-profile.json` |
| **3. BGL KERNDTLB one-node repetition, June 11** | Strong Engineer story and anti-volume demonstration | 152,182 Log Entries from one node over 22,908 epoch seconds; same group at 5s and 300s; clear concentration | Repetition alone does not prove a software loop or identify hardware failure cause; less cross-Component breadth | Same recorded group queries; node R30-M0-N9-C:J16-U01 |
| **4. BGL KERNMNTF recurring Lustre-mount failure messages** | Good recurrence view | 31,531 labelled Log Entries; category text is intelligible; counts/trends support Team Lead | 237 category groups at 5s versus 105 at 300s shows threshold dependence; exact incidents/causes remain unverified | `bgl_labels`, `bgl_weekly`, `bgl_gap_*_category`; [2007 paper](https://doi.org/10.1109/DSN.2007.103), Table 4 |

The issue asks for “real Incidents.” The supported result is four **verified benchmark-anomalous instance executions** and ranked **real candidate temporal groups**. The available BGL annotations do not justify upgrading every group to a verified Incident. The two BGL groups above are stable at two tested gaps, which makes them good demonstrations without solving that ground-truth limitation.

### Experiments run

Work is under `research/5/ugnmura/` on `research/5-ugnmura`; shared incident-model experiments are under `research/1/ugnmura/` on `research/1-ugnmura`. [Replay scripts and profiles](https://github.com/OrFrederick/serenedb-log-mcp/tree/research/5-ugnmura/research/5/ugnmura) accompany this branch. Raw corpora were streamed without extraction. Inputs, hashes, SereneDB version and exact research schema are recorded in issue #2's `research/2/ugnmura/load_results.json` on `research/2-ugnmura`. This uses a **throwaway research loader**; the organiser-supplied loader and Ownership Table are still unavailable.

**Data Scope:** Whole raw Base Corpus, BGL 4,747,963 plus OpenStack 207,820 = **4,955,783 Log Entries**. BGL contains 348,460 category-labelled alert entries. No sample-file counts are extrapolated. Source display times remain naive; BGL epoch seconds are preserved separately. OpenStack has 8 unstructured physical lines retained in whole-corpus totals.

#### The selected case

| Labelled instance UUID | Build duration, seconds | Explicitly tagged entries | Level / Components |
|---|---:|---:|---|
| 544fd51c-4edc-4780-baae-ba1d80a0acfc | 42.81 | 25 | All INFO; 3 Components |
| ae651dff-c7ad-43d6-ac96-bbcd820ccca8 | 30.32 | 25 | All INFO; 3 Components |
| a445709b-6ad0-40ec-8860-bec60b6ca0c2 | 51.59 | 25 | All INFO; 3 Components |
| 1643649d-2f42-4303-bfcd-7798baec19f9 | 32.12 | 25 | All INFO; 3 Components |

The Components are `nova.compute.claims`, `nova.virt.libvirt.driver`, and `nova.compute.manager`. The abnormal source file has **18,159 INFO + 275 WARNING = 18,434 entries and zero ERROR/CRITICAL entries**. The four UUIDs are independently listed in the archive's `anomaly_labels.txt` and upstream DeepLog's file, blob SHA `446cbb36e68cdd960170927cefad0299ec0d5c68`.

All **2,066** “Took … seconds to build instance.” entries were compared. The **193 unlabelled builds in the same abnormal file** have median **20.61s**, p99 **22.68s**, maximum **27.61s**. The **1,869 builds in the two normal files** have median **20.54s**, p99 **22.3156s**, maximum **23.34s**. Report the comparison groups separately: different files/times do not constitute a controlled causal experiment. Quantiles use linear interpolation over all returned build rows, and all raw durations are retained.

Threshold sensitivity demonstrates the limitation: strict **>22s** selects 4 labelled plus 52 unlabelled builds; **>23s** selects 4+2; **>24s** selects 4+1; **>30s** selects 4+0; **>35s** selects 2+0; **>50s** selects 1+0; **>60s** selects 0+0. The >30s separation is retrospective and in-sample, not a production anomaly detector claim.

HTTP observations were profiled separately by normalised endpoint and status. There are **9,416**, **26,624**, and **62,693** parseable HTTP duration entries in abnormal, normal1 and normal2 respectively. Their overall p99 response durations are approximately **0.5092s, 0.5108s, 0.5174s**. These are request-response durations, not VM build times. The abnormal file includes 369 HTTP 404 responses and no 5xx, so 404 counts alone are also an unsuitable proxy for the four benchmark anomalies. A route/request-status drill-down must preserve endpoint scope and denominator.

#### Query and evidence map

`verify_serenedb.py` contains the exact executed SQL and bound parameters; JSON results retain rows and elapsed seconds. The key queries are:

- `whole_base_corpus`: `GROUP BY dataset` over every raw Log Entry, demonstrating exact corpus scope.
- `openstack_levels`: counts per source file and level, demonstrating why ERROR-only retrieval misses this case.
- `build_rows`: all build-duration entries, ordered by duration; reconstructs every comparison and quantile.
- `labelled_instance_sessions`: exact UUID membership, first/last source timestamps, tagged-line counts and distinct Components.
- `http_endpoint_profile`: separate endpoint/status/response-duration denominators.
- `bgl_top_groups_5` and `_300`: cross-node candidate groups with entry count, nodes, racks, midplanes and bounds.
- `bgl_weekly`: complete weekly/category trends; supports recurrence rather than extrapolating from retrieved samples.

Full-text retrieval must use the docs-verified SereneDB inverted-index relation, search “build instance” with source/time filters, count all matches, and return bounded samples with truncation metadata. The final product must retain the SQL and index-refresh state. Full-text execution also completed: the indexed phrase has 2,066 matches over OpenStack, 197 in the abnormal file on May14, and one for the selected instance/time window. SQL and rows are retained in `serenedb-fulltext-results.json`. Do not label a substring `LIKE` query as full-text search.

### Draft storyline per Role

**Engineer question:** “Why did instance a445709b-6ad0-40ec-8860-bec60b6ca0c2 take 51.59 seconds to build when the source file has no ERROR entries?”

- **Tool calls/Evidence expected:** full-text search for build completion in the abnormal file and time range; exact instance-context retrieval; aggregate all build durations in that source; nearby `Creating image`, lifecycle, spawn and build lines with stable entry IDs. Retrieve all 25 explicitly tagged lines for the chosen instance; retain the distinction between explicit instance matches and nearby context.
- **Observed Facts:** labelled anomalous VM execution; 51.59s reported build duration; 25 tagged INFO entries across 3 Components; compare against the same-file distribution.
- **Hypothesis:** delay may lie in image creation/transfer or VM startup. The [DeepLog paper](https://users.cs.utah.edu/~lifeifei/papers/deeplog.pdf), §§5.2/5.5, describes injected network throttling in this experiment family; it is not independent proof of this UUID's cause.
- **Unknowns:** per-UUID injection mechanism, end-user effect and recovery state.
- **Next Step:** inspect the image-creation-to-start interval and the compute/controller image-transfer telemetry; verify against injection records before asserting cause.

**Team Lead question:** “Is slow VM provisioning recurring, and which Components should we investigate together?”

- **Tool calls/Evidence expected:** whole Base Corpus counts; all build durations grouped by source and time; strict-threshold sensitivity; exact labelled-instance membership and Component breakdown; Ownership Table join once supplied.
- **Observed Facts:** four labelled instance executions with 30.32–51.59s builds, versus 193 other same-file completions with median 20.61s; all four span the same three Components. Entry count, build-completion count and distinct instance count are separate quantities.
- **Unknowns:** which Teams own those Components until the organiser's table arrives; whether the four executions stem from two injections, one broader degradation, or four independent operational Incidents.
- **Next Step:** coordinate investigation of the measured image/start/build workflow across those Components, and review the four executions together; do not report “100 incidents” from their 100 tagged lines.

**Manager question:** “Which observed pattern deserves investigation capacity, and what can we justify from the logs?”

- **Tool calls/Evidence expected:** same underlying duration and distinct-instance/Component aggregates, plus the two BGL candidate groups' line-count versus spread comparison.
- **Observed Facts:** four benchmark-labelled slow provisioning executions are an independently verified investigation target. BGL separately shows 152,182 lines on one node versus 20,480 on 20,480 nodes; raw volume cannot itself decide priority.
- **Unknowns:** financial loss, downtime, affected users and operational severity across historical Datasets.
- **Next Step:** allocate a focused check of provisioning performance and its detection coverage; request operational impact and ownership data before choosing a business-severity ranking. No numeric risk score is invented.

### Sources

- [OpenStack dataset description](https://github.com/logpai/loghub/blob/master/OpenStack/README.md), [upstream anomaly labels](https://github.com/aaron-y-chen/deeplog/blob/master/example/data/OpenStack/anomaly_labels.txt), and [DeepLog, CCS 2017](https://users.cs.utah.edu/~lifeifei/papers/deeplog.pdf), §§5.2/5.5.
- [BGL description](https://github.com/logpai/loghub/blob/master/BGL/README.md); [Oliner & Stearley, DSN 2007](https://doi.org/10.1109/DSN.2007.103), Tables 4/5 and §3.3. Its Spirit single-node disk story must not be relabelled as a BGL failure story. Its filtered-alert totals are not a verified Incident ledger.
- [Zhu et al., Loghub, ISSRE 2023](https://arxiv.org/abs/2008.06448), required corpus citation, alongside the dataset-specific papers.
- [SereneDB window functions](https://serenedb.com/docs/sql/functions/window_functions) and [timestamp functions](https://serenedb.com/docs/sql/functions/timestamp), read via docs MCP before SQL experiments.

### Risks / unknowns

- Do not imply a causal connection between BGL 2005 and OpenStack 2017. The BGL segment demonstrates interpretation, while OpenStack supplies the verified anomaly investigation.
- Ground truth consists of four abnormal-instance IDs, not per-line severity, exact failure boundaries, customer impact, or four independent root causes.
- Four labelled executions and an in-sample threshold cannot establish detector generalisation. Keep sensitivity and comparison-group limitations visible.
- BGL grouping counts depend on threshold and timestamp semantics. A 300s total happens to match the 2007 filtered total, but category discrepancies prevent claiming reproduction.
- Supplied loader/schema/Ownership Table may change parsing/ownership. Retain raw identifiers and replay exact evidence after switching to organiser materials.

### Open questions for the humans

- Approve the four OpenStack benchmark anomalies as the centrepiece, with BGL as the concise supporting contrast?
- Is the intended README investigation allowed to include that independent BGL contrast while making its non-causal relationship explicit?
