# Role differentiation — independent research

Researcher: **ugnmura's Codex**, 2026-10-17. Independent Research phase; no other Research comments read. Recommendations below remain proposals.

## Recommendation and confidence

Keep **three MCP Role prompts over the same small parameterised tool vocabulary**, with Role-specific evidence requirements and defaults. Enforce schema and query contracts server-side; evaluate the resulting tool trajectories and answers. Prompts express the decision the Role needs to make: Engineer chooses a diagnostic check, Team Lead chooses an investigation order and coordination, Manager chooses an attention/measurement decision. This changes questions, Evidence selection and action, not just verbosity.

Treat prompts as advisory. MCP defines them as user-controlled templates; invoking Manager does not authenticate a Manager or hide raw tools. The installed official SDK experiment discovers/retrieves all three prompts under both legacy and modern protocol eras, including subprocess/stdio; it does not prove Claude Desktop/Code exposes the correct UI or obeys them. Optional access control needs separate authenticated database identities and views/grants, not a client-supplied `role` argument. Confidence **high** in this separation and the evidence-first templates; **medium** in reliability until live repeated Claude and actual host tests run.

## Primary practice: what transfers, what does not

Google SRE's [troubleshooting guidance](https://sre.google/sre-book/effective-troubleshooting/) motivates iterative hypothesis/test thinking for Engineer; its [monitoring chapter](https://sre.google/sre-book/monitoring-distributed-systems/) distinguishes symptoms from causes and considers latency as well as errors. Therefore a severity-only error search is insufficient for this corpus's slow-build example. This is an application of the guidance, not a claim Google mandates our exact Role templates.

Google's [incident-response workbook](https://sre.google/workbook/incident-response/) separates operations from coordination and stakeholder communication. That supports different actions for technical responders and decision-makers, though our three Roles are challenge-defined and are not literal incident-command job titles. [Google's postmortem practice](https://sre.google/workbook/postmortem-culture/) supports structured, blameless accounts and follow-up action, rather than assigning fault to Teams based on log volume. We should borrow Evidence/timeline/action separation without calling this observational investigation a completed postmortem.

[DORA metrics](https://dora.dev/guides/dora-metrics/) depend on change/deployment and recovery information. The Base Corpus alone does not establish them. Do not relabel matching-entry counts as change failure rate, recovery time or Team performance. Industry reports with verified customer-impact sections are stylistic references, not permission to invent such sections for historical logs.

## Mechanisms compared

| Mechanism | Role substance | Enforcement / compatibility | Recommendation |
|---|---|---|---|
| Three MCP prompts | Strong if each specifies distinct decision, Evidence and action | User-selected instructions; can be ignored, not identity or access control | Core; templates in role-prompts.md; actual host smoke test needed |
| Role-specific defaults | Bounded context for Engineer; aggregation window/grouping for Lead/Manager | Server validates limits/scope; defaults do not stop clients changing allowed inputs | Core; return effective filters/defaults in Evidence so comparisons stay honest |
| Output schemas | Stable typed metrics/claims/coverage; makes numeric gates straightforward | MCP outputSchema constrains tool results, not the final host response; SDK answer schema is eval-specific | Core for Evidence; structured final-answer lane plus native Markdown lane in eval |
| Different tool subsets | Can reduce irrelevant raw-line retrieval for Manager | Listing/filtering is not authorisation; host SDK `allowed_tools` auto-approves rather than restricting visibility | Avoid three duplicated tool sets in core. Consider authenticated server profiles only if measured confusion persists |
| Separate views + LOGIN identities + grants | Enforce actual data scope and raw-line access | Requires server-bound identity, no base-table grants, no generic SQL escape hatch, negative access tests | Optional award extension after core. Verify SereneDB SQL via docs MCP; no RLS claim |
| Three separate agents/apps | Strong workflow isolation is possible | Changes settled product interface and introduces more state/maintenance | Reject for this challenge; same Claude agent and Base Corpus is the requirement |

[MCP prompts specification](https://modelcontextprotocol.io/specification/2025-06-18/server/prompts) is the source for user control. [MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) separates tool schemas/structured output. [Claude Agent SDK reference](https://platform.claude.com/docs/en/agent-sdk/python) explicitly distinguishes auto-approval (`allowed_tools`) from tools actually available/denied. Do not market prompt adherence as privacy enforcement.

For actual access separation, role must come from authenticated server config or trusted claims, never natural-language selection. Each MCP connection binds its own DB credentials. Manager identity cannot call raw retrieval successfully even by guessing the tool name or switching prompts; Engineer cannot use admin/index-award operations via core credentials. Filtered views need predicates for rows plus projection/aggregation for sensitive columns; a view with only a row filter can still reveal raw text. Views/grants do not prove true RLS, and their definer/invoker behavior must be verified in SereneDB. Existing known-facts doc reports no RLS; this research did not independently execute grants.

## Templates and allowed Evidence

The complete prompts are in `role-prompts.md`. All answers retain Observed Facts, Hypotheses, Unknowns and Next Step.

| Role | Required Observed Facts | Permitted supporting Evidence | Required action | Fails despite correct numbers |
|---|---|---|---|---|
| Engineer | Focus error/anomaly, raw lines, ordered nearby context by a real correlation key | Exact/phrase/full-text search, bounded context, counts for scope or baseline | Discriminating diagnostic check with expected observation | Generic “monitor this”, frequency table without context, causation from proximity |
| Team Lead | Recurrence over stated windows, Component/Team grouping, ownership coverage, trend/denominator caveats | Aggregation and trends plus representative retrieved lines | Investigation order and mapped coordination objective | Raw-line dump, invented Team, no comparison period, entry count called incidents |
| Manager | Ranked observed attention list, named Components, frequency policy/sensitivity and uncertainty | Aggregates, measured spread and comparison; raw Evidence available through citations | Decision to sponsor/coordinate/measure with completion criterion | Same Engineer diagnosis shortened, invented loss/downtime, frequency called proven harm |

Tool defaults can favor the Role without suppressing Evidence needed to explain it. Engineer may aggregate to assess scope; Manager may inspect a line through a citation when allowed. Do not insist on disjoint tool names. Disjoint **decision requirements and Evidence shapes** are the evaluation target.

## A defensible prioritisation formula

Prefer an explicit **frequency attention rank**, not an arbitrary weighted “risk score”. For a fixed Dataset/window and mutually specified candidate filters, compute `F_i = COUNT(matching Log Entries)` and `C_i = COUNT(DISTINCT Component)`. Define `rank_i = 1 + number of candidates j with (F_j > F_i) OR (F_j = F_i AND C_j > C_i)`. Equal `(F,C)` pairs share rank; alphabetic ordering is display only. Every count uses executed aggregation in production. These are Log Entries, not incidents. If candidates overlap, disclose overlap and do not sum them as unique affected entries. Keep Datasets separate when durations/systems differ; no cross-corpus common production rate.

Frequency and affected Components follow the challenge's permitted Manager basis. Show trend as a comparison of frequency over equal, adequately observed windows: `delta_count`, `F_current/exposure_current` and `F_previous/exposure_previous`; percentage change is undefined at zero baseline. Do not divide by “last timestamp minus first” and call it an availability exposure period. Emit “insufficient comparable coverage” when appropriate. Team count is coordination context from the official Ownership Table, not an automatic severity multiplier. Missing Team values remain Unknown rather than guessed. Do not invent weights for financial or customer impact.

This formula is a transparent queue policy; it does not prove the first item is most harmful. The Manager can explicitly request a different filter (ERROR/CRITICAL only, or known slow operations) and see how priority changes. For the worked slow-build story, use a candidate predicate for emitted build duration above a disclosed threshold independently of severity; “attention ranked by error level” would miss all labelled anomaly entries. Thresholds found after looking at labels remain exploratory, not a validated detector or SLO.

Why avoid an opaque scalar? The real-data sensitivity experiment ranks five Components under WARNING/ERROR/CRITICAL and compares ERROR/CRITICAL only. All-severity first is `nova.virt.libvirt.imagecache` with 3,059 matching entries; severe-only first is `oslo_service.periodic_task` with 184. A hypothetical score blending log count with fraction of Component emissions flips its top among auth_token, periodic_task and imagecache as the weight changes. This does not identify the best weighting; it demonstrates that hidden value judgements materially change the answer. Emission fraction is not user failure rate. Current groups each contain one Component, so spread is measured but adds no distinction. No Ownership Table or valid equal observation windows exist here to test Team/trend weighting honestly.

## Experiments and verified observations

```sh
python3 research/4/ugnmura/role_experiment.py /tmp/serenedb-data/OpenStack.tar.gz
python3 research/4/ugnmura/verify_build_story.py /tmp/serenedb-data/OpenStack.tar.gz
```

`role_experiment.py` independently scans the exact raw archive (SHA256 in openstack-fixture.json), preserving physical Log Entry count **207,820**. Its strict timestamp-header regex associates **207,812** lines with Component/level; **8** remaining lines are parser coverage gaps, not evidence that the source lacks structure. They are included in physical totals and excluded from Component ranking, explicitly disclosed. The more complete corpus parser in issue #1 may recover them; implementation must settle that without losing physical entries. WARNING/ERROR/CRITICAL grouped totals and raw auth-token context are stored; timestamps have no explicit timezone, so do not silently assert UTC.

`verify_build_story.py` independently cross-checks the corpus agent's anomaly discovery directly from `anomaly_labels.txt` and raw lines. Four labelled instance ids each have 25 explicitly tagged Log Entries; all 100 are INFO. Build-duration lines are **30.32, 32.12, 42.81 and 51.59 seconds**. The same file has 193 unlabelled build entries, median **20.61**, maximum **27.61** seconds. Other files have 1,869 build entries, median **20.54**, maximum **23.34**. All exact lines are stored in build-story-verified.json. Comparing logged duration to duration is legitimate; calling it downtime or customer latency is not.

Threshold `>22` seconds selects all four labels and 52 unlabelled builds; `>30` selects four labels and no unlabelled builds; `>35` selects two labels. This is a post hoc sensitivity analysis on this archive. Unlabelled is not guaranteed normal, and no holdout/generalisation performance is claimed. The literal label file says injected anomalies; it does not specify the physical cause.

**Scope limitation:** these are executed offline scans, not SereneDB queries, MCP Evidence Ledger entries or Claude-generated answers. No live model was run. All worked-investigation numbers must be rerun by reviewed golden SQL and the actual MCP tools before README/demo claims.

## Same issue, three substantive example answers

The following are authored answer prototypes based on real executed scans. **[R1]** resolves to build-story-verified.json `labelled_entries`/`labelled_builds`; **[R2]** resolves to its baseline and threshold fields; **[R3]** resolves to openstack-fixture.json file coverage. These are research provenance references, deliberately not fake `[E<n>]` MCP ids. Production versions must substitute actual successful Evidence Ledger ids and SQL/results.

### Engineer — “Why did this labelled OpenStack build take longer?”

**Observed Facts.** For instance `1643649d-2f42-4303-bfcd-7798baec19f9`, raw INFO lines show successful resource claim, image creation, paused/resumed lifecycle messages and a logged build duration of **32.12 seconds**; the reported spawn duration is **31.27 seconds** [R1]. The same-file unlabelled median build duration is **20.61 seconds**, over **193** emitted build-duration entries [R2]. Lifecycle messages include a different request id, so instance identity is the available correlation link [R1].

**Hypotheses.** The extra time may be in the spawn/image path, consistent with the reported durations; the logs do not establish its underlying cause. Pausing/resuming alone does not prove the injection mechanism.

**Unknowns.** No hypervisor metrics, image fetch timings, configuration changes, or established customer impact. No causal conclusion follows from the injected-anomaly label.

**Next Step.** Inspect image-fetch and hypervisor state timing for this instance's spawn interval, then compare the same stages for a nearby unlabelled build. Check whether the delay occurs before guest start or during pause/resume before choosing a fix.

### Team Lead — “Is this a recurring investigation candidate and who should coordinate?”

**Observed Facts.** The supplied label file identifies **four** instances; each has **25** explicitly tagged INFO Log Entries across **three** Components: `nova.compute.manager`, `nova.compute.claims`, `nova.virt.libvirt.driver` [R1]. Their logged build durations range **30.32–51.59 seconds**, compared with same-file unlabelled maximum **27.61 seconds** [R1,R2]. The later files have a different coverage period, so their counts do not establish an increase or decrease [R3]. The official Ownership Table is unavailable.

**Hypotheses.** A shared stage in the build path is a reasonable joint investigation candidate; the Components' involvement does not mean each caused the delay.

**Unknowns.** Team mapping, recurrence outside this archive, and a stable detector threshold. A severity-only workflow would omit these INFO-level examples.

**Next Step.** Coordinate the mapped Teams for those Components once the Ownership Table arrives. Investigate the spawn-stage timing first, then validate the candidate rule on unlabelled/held-out builds before adopting alerting.

### Manager — “What decision should I make about these observed slow builds?”

**Observed Facts.** The archive contains **four** labelled builds with recorded duration **30.32–51.59 seconds** and linked entries across **three** Components [R1]. An exploratory **30-second** cutoff isolates those **four** within this archive, while **35 seconds** misses **two** [R2]. These are measured build-duration observations, not established business loss or downtime. Their attention rank depends on whether the review covers slow builds or severity-labelled entries.

**Hypotheses.** A targeted performance investigation may improve the build-path understanding; business urgency remains unestablished.

**Unknowns.** Customer expectations/SLOs, financial loss, downtime, causality and current recurrence are not established by the Base Corpus.

**Next Step.** Sponsor a bounded build-path investigation and request an appropriate latency objective plus validation on additional builds. Completion means a stage-level explanation and an independently evaluated decision rule, before approving wider remediation.

These answers share verified observations but differ in diagnostic detail, grouped recurrence/coordination and a sponsorship decision. Merely truncating Engineer text would not satisfy the latter two.

## Repeated-run enforcement and remaining work

Issue #3 proposes 10 questions per Role plus adversarial cases, repeated independent sessions, deterministic numeric/citation gates and a calibrated Role rubric. Add this slow-build triplet to the held-out set; compare all three Role outputs for the same source issue. Reject Engineer with no context, Lead with no aggregate/ownership caveat, Manager with only debugging instructions, and any Role claiming cost/downtime. Required sections are not sufficient: inspect selected Evidence and action type. Report both trial pass rate and all-repeats consistency; prompt adherence is measured, never guaranteed.

Before core delivery: rerun all examples through SereneDB and MCP; obtain Ownership Table and supplied loader; validate prompt selection on actual Desktop and Code versions; execute live Claude repeats. Before access-control bonus: docs-confirm and execute views/grants, verify default/public privileges, direct base-table/guessed-tool/raw-column denial, and identity isolation across sessions. No additional SQL syntax is proposed here because it was not verified through SereneDB docs MCP.
