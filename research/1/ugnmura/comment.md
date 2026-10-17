## Research: ugnmura's Codex

**Recommendation:** Expose three separate quantities: matching **Log Entries**, deterministic **candidate temporal groups**, and externally verified **Incidents**. Use an explicit same-category/node inactivity-gap rule for local groups and a second same-category, cross-node view that retains distinct nodes/racks/midplanes and the top-node share. Keep membership reversible. Merge different patterns only through an explicit correlation identifier or a separately documented, reviewed rule; chronological proximity alone is a cascade hypothesis. In OpenStack, retain instance-level ground-truth annotations separately from request HTTP status and build duration. The verified demo opportunity is four injected-anomaly VM executions whose tagged lines are all INFO, alongside a BGL contrast between one-node repetition and broad spatial spread. This does not establish a complete list of real operational Incidents.

**Confidence:** High for raw counts, labels, reproducible SQL and descriptive grouping; medium for a useful grouping policy; low for identifying distinct underlying BGL failures without operational context. A published filtering result did not reproduce at its stated threshold, and is explicitly not a ground-truth Incident count.

### Options compared

| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| Count ERROR/FATAL Log Entries as Incidents | Reject | Simple | Repetition dominates; misses labelled OpenStack performance anomalies | Full-corpus profiles and SQL below; [Oliner & Stearley](https://doi.org/10.1109/DSN.2007.103), Table 5 |
| Same category + node + inactivity gap | Local candidate groups | Deterministic, queryable, preserves repeated-node behavior | Counts grow with spatial spread; gap chaining permits long groups | `bgl_gap_*_node_category` in recorded SQL |
| Same category + inactivity gap across nodes | Cross-node candidate groups | Makes one-node repetition versus many-node reporting visible | Can merge independent simultaneous problems; category is not necessarily a template | `bgl_gap_*_category`; [2007 paper](https://scispace.com/pdf/what-supercomputers-say-a-study-of-five-system-logs-1rwf6iuf3m.pdf), §3.3 |
| Instance/request identity + timing/status | Recommended for OpenStack | Identity is stronger than temporal similarity; labels independently available | An instance can host multiple operations; HTTP latency and VM build duration differ | `labelled_instance_sessions`, `build_rows`, `http_endpoint_profile`; [upstream labels](https://github.com/aaron-y-chen/deeplog/blob/master/example/data/OpenStack/anomaly_labels.txt) |
| Learned anomaly detection | Later experiment | Captures sequence/count/timing changes | Requires training/evaluation; anomaly score does not define an Incident | [DeepLog](https://users.cs.utah.edu/~lifeifei/papers/deeplog.pdf), [LogAnomaly](https://www.ijcai.org/proceedings/2019/658), [LogRobust](https://hongyujohn.github.io/FSE19b.pdf) |

### Definitions proposed for review

These are proposed vocabulary, not edits to `GLOSSARY.md`.

1. A **local candidate temporal group** has a dataset, category/pattern key, node, first/last observed timestamp and member Log Entry identifiers. In timestamp order, begin a group when there is no predecessor or `current_epoch_s - previous_epoch_s > gap_seconds`. Equal-to-threshold entries remain together. BGL's supplied alert category is the grouping key in this experiment; a category is not a mined template.
2. A **cross-node candidate temporal group** uses the same rule partitioned by dataset and category, without node. It retains the union of members and the local-group references. Do not discard the raw count after deduplication.
3. **Spread** is the vector `(distinct_nodes, distinct_racks, distinct_midplanes, distinct_components)`, with missing identifiers reported separately. BGL rack/midplane extraction uses anchored `Rxx-My` prefixes, rather than splitting arbitrary identifiers. The top-node share is `largest per-node member count / all members`. One-node repetition and broad spread remain visibly different even when both have one candidate temporal group.
4. **Burst versus trickle** is a descriptive comparison: report group duration, per-minute counts, peak-minute count/share, and active-minute counts alongside raw count. A gap group alone is not a statistically detected burst. A normalized recent-rate versus preceding-baseline rate requires equal stated exposure and zero-filled buckets; a nonzero recent count with zero previous count is “not seen in the stated baseline,” not proof of a new failure. `bgl_recent_vs_baseline` records 7-day versus preceding 28-day counts; divide by 7 and 28 respectively before comparing rates.
5. A **cascade candidate** is an observed ordered pair of distinct categories on the same node within a configurable interval. The experiment uses adjacent *labelled-alert* entries, not every raw line. It does not establish causal propagation. Unknown/missing node identifiers must be excluded in a production implementation.
6. An **Incident** requires external verification or a reviewed operational definition with its provenance and uncertainty. Store verification status and rationale separately from candidate-group membership. Four labelled injected-anomaly instance executions are verified benchmark annotations; they do not imply four independent causal failures or outages.

Gap chaining can produce a multi-hour group. If a maximum duration or fixed-window boundary is added, it becomes another explicit policy parameter, changes counts, and needs separate sensitivity reporting. Cross-Component graph connected-components merging is deferred: transitive temporal joins can collapse unrelated problems.

### Experiments run

Code is on branch `research/1-ugnmura`, under `research/1/ugnmura/`. `corpus.py` streams both complete raw archives without extracting them; `profile.py` independently computes profiles; `verify_serenedb.py` records executed SQL, bound parameters, returned rows and timings. Parent research loader and input hashes are on `research/2-ugnmura`, `research/2/ugnmura/load_results.json`. It is a **throwaway research loader**, not the organiser-supplied loader, which remains unavailable.

Raw physical-line Data Scope: BGL **4,747,963** and OpenStack **207,820**, hence Base Corpus **4,955,783** Log Entries. OpenStack comprises 18,434 abnormal-file, 52,312 normal1-file and 137,074 normal2-file lines. Eight OpenStack lines have no structured timestamp; they remain in total counts. BGL includes empty messages, corrupted duplicated location fields and embedded NUL bytes; these are preserved. The repaired parser reproduces all six severity counts in the 2007 paper: INFO 3,735,823; FATAL 855,501; ERROR 112,355; WARNING 23,357; SEVERE 19,213; FAILURE 1,714. Original BGL display timestamps are naive and differ from its separate epoch field; neither is silently relabelled as UTC.

#### Gap sensitivity on all 348,460 labelled BGL Log Entries

| Gap seconds; new group when difference is greater | Category groups across nodes | Category-and-node groups |
|---:|---:|---:|
| 1 | 2,355 | 195,900 |
| 5 | 1,621 | 195,892 |
| 30 | 1,351 | 195,861 |
| 60 | 1,295 | 194,486 |
| 300 | 1,202 | 190,287 |
| 900 | 1,118 | 184,815 |
| 3,600 | 914 | 143,004 |

These are candidate temporal groups, not verified Incidents. The difference between node-local and cross-node counts itself shows why a single line count is inadequate.

The 2007 paper's Table 4 reports 1,202 filtered BGL alerts, while §3.3.2 states a **5-second** threshold and Algorithm 3.1 suppresses differences **strictly less** than T. Our explicitly specified `>5` gap calculation yields 1,621, not 1,202. `>300` happens to total 1,202 but the category counts differ (APPSEV 136 here versus 138 in Table 4), so this is **not a successful reproduction**. Boundary/time-basis comparisons are retained in `published_reproduction_checks`. Do not tune to a published total and call the result ground truth.

#### Real candidate groups for #5

| Candidate | Observed membership and spread | Verification status |
|---|---|---|
| BGL KERNDTLB, 2005-06-11 17:17:44–23:39:32 display time | 152,182 entries; **one node**, R30-M0-N9-C:J16-U01; 22,908 epoch seconds; top-node share 100% | Category-labelled alerts; one candidate group stable at 5s and 300s; underlying Incident unverified |
| BGL KERNSTOR, 2005-06-14 09:47:55–09:56:49 display time | 20,480 entries, **20,480 nodes**, 20 racks, 40 midplanes; 534 epoch seconds; one entry per node | Category-labelled alerts; one candidate group stable at 5s and 300s; shared cause unverified |
| OpenStack four labelled instance executions | 25 explicitly instance-tagged entries each, all INFO, spanning 3 Components; build times 30.32, 32.12, 42.81, 51.59s | Four injected-anomaly instance IDs verified in archive and upstream label file; no outage/financial-impact annotation |

The four UUIDs are `544fd51c-4edc-4780-baae-ba1d80a0acfc`, `ae651dff-c7ad-43d6-ac96-bbcd820ccca8`, `a445709b-6ad0-40ec-8860-bec60b6ca0c2`, `1643649d-2f42-4303-bfcd-7798baec19f9`. Upstream label file blob SHA is `446cbb36e68cdd960170927cefad0299ec0d5c68`.

OpenStack build comparison: 193 unlabelled builds in the same abnormal file have median 20.61s and maximum 27.61s; 1,869 builds in normal files have median 20.54s, p99 22.3156s and maximum 23.34s (`build_rows`, plus independent `openstack-profile.json`; linear interpolation quantiles). A retrospective threshold **>30s** selects exactly the four labelled builds. Thresholds >22, >23, >24, >35, >50, >60 select respectively `(labelled, unlabelled)` = `(4,52), (4,2), (4,1), (2,0), (1,0), (0,0)`. This sensitivity is descriptive and in-sample; it is not a validated production detector. The abnormal file contains no ERROR or CRITICAL entries at all. HTTP response durations are measured separately and must not be substituted for VM build durations.

### Sources

- [Oliner & Stearley, DSN 2007](https://doi.org/10.1109/DSN.2007.103), [full paper mirror](https://scispace.com/pdf/what-supercomputers-say-a-study-of-five-system-logs-1rwf6iuf3m.pdf): same failure can produce many alerts; filtering is approximate; operational context is missing; Table 4 is filtered alerts, not a verified Incident ledger. An example single-node disk story in this paper concerns **Spirit**, not BGL, so it must not be presented as a BGL story.
- [DeepLog, CCS 2017](https://users.cs.utah.edu/~lifeifei/papers/deeplog.pdf), §§5.2,5.5: value and elapsed-time anomalies, VM build workflow and injected network throttling; our four UUID labels do not independently identify each fault mechanism.
- [LogAnomaly, IJCAI 2019](https://www.ijcai.org/proceedings/2019/658): sequential and quantitative anomaly detection using semantic templates. [LogRobust, FSE 2019](https://hongyujohn.github.io/FSE19b.pdf): semantic sequence classification under log instability. Neither defines ground-truth Incident counts for this Base Corpus.
- [Loghub BGL dataset description](https://github.com/logpai/loghub/blob/master/BGL/README.md) and [OpenStack description](https://github.com/logpai/loghub/blob/master/OpenStack/README.md); [Loghub paper](https://arxiv.org/abs/2008.06448). Cite Zhu et al., ISSRE 2023, plus dataset-specific papers when reusing the corpora.
- [PagerDuty](https://support.pagerduty.com/main/docs/intelligent-alert-grouping): rolling time window, textual similarity and response history. [Opsgenie](https://support.atlassian.com/opsgenie/docs/what-is-alert-de-duplication/): alias-key de-duplication while an alert remains open. [Splunk ITSI](https://help.splunk.com/en/splunk-it-service-intelligence/splunk-it-service-intelligence/detect-and-act-on-notable-events/4.18/event-aggregation/overview-of-aggregation-policies-in-itsi): filtering, split fields and breaking criteria produce operational episodes. These are useful policy precedents, not independent causal verification.
- [Datadog Log Anomaly Detection](https://www.datadoghq.com/blog/accelerate-incident-investigations-with-log-anomaly-detection/): anomaly investigation and pattern changes are a separate concern from Incident lifecycle.
- [Kleinberg, KDD 2002](https://www.cs.cornell.edu/home/kleinber/bhs.pdf): burst detection as changes in stream intensity. [Adams & MacKay, 2007](https://arxiv.org/abs/0710.3742): online changepoint posterior over run length. Both are alternatives for future baseline-aware detection; neither model was run or claimed as implemented here.
- SereneDB docs read through its docs MCP: [window functions](https://serenedb.com/docs/sql/functions/window_functions) and [timestamp functions](https://serenedb.com/docs/sql/functions/timestamp). Queries execute on local Docker 26.10.1 via psycopg. Binding a Python list to `ANY(%s)` failed as VARCHAR/UNNEST; fixed-size bound `IN` parameters succeeded.

### Risks / unknowns

- BGL alert categories verify tagged lines, not distinct failures, cause, downtime, or affected users. Temporal/spatial overlap is not causal identity.
- Exact Organiser schema, loader and Ownership Table remain unavailable; Team counts cannot be claimed. Current Component/rack parsing is a research adaptation.
- Baseline novelty can mean changed logging or observation coverage. A zero denominator must be reported, not divided into an invented infinite risk score.
- A group that reaches either corpus boundary is censored; absence of a nearby entry does not prove recovery. Source clocks and missing node identifiers need explicit handling.
- LogRobust's paper evaluates Hadoop and a Microsoft service, not a new validation of our BGL/OpenStack Incident model. Do not transfer published F1 numbers to this prototype.

### Open questions for the humans

- Adopt “candidate temporal group” as the default tool output, with verified Incident status requiring provenance?
- For the demo, use the four OpenStack labelled performance anomalies as the verified case and BGL repetition/spread as the supporting contrast?
- Keep both 5s and 300s group views visible rather than presenting one threshold as uniquely correct?
