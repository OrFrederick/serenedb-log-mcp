## Research: OrFrederick's Claude

**Recommendation:** Make the Roles differ where Claude cannot blur them: in what the server returns. The prompt wording only reinforces that.

Use four layers:
1. **Per-Role Evidence surface, enforced in SereneDB.** Each Role's tool calls run on its own LOGIN role. That role may SELECT only its own view:
   - Engineer: raw Log Entries plus a full-text index over that view.
   - Team Lead: counts per hour × Component × alert category × midplane, with no `content` column.
   - Manager: counts per day × category, plus spread, with no node ids and no `content`.

   I verified this works, and that each role is refused on the base table, on the other Roles' views and on the base-table index.
2. **Role-shaped tool results.** Every tool takes a required `role` enum. The server picks the Role's connection, its defaults and its output shape, and returns a fixed `answer_frame` with the required sections. The Manager frame also lists `not_measured: [cost, downtime, customer impact, SLA, root cause]`.
3. **Three MCP prompts** (`engineer`, `team_lead`, `manager`). Each one sets the questions to ask, the Evidence allowed and the shape of the Next Step.
4. **A `--role` startup flag** that pins one server registration to a single Role. This is for the Desktop demo and the access-control bonus.

For the Manager, rank problems by the **equal-weight mean of percentile ranks** over five measured features:
- frequency, as node-card-hours
- spread, as midplanes
- persistence, as active weeks
- trend, as the last-28-day rate against the rate over the whole period
- recency

There is no severity or impact term, and no product of scores. Always show it next to a Pareto column, so the Manager sees why volume is not the same as priority.

**Confidence:**
- **High** for the access-control mechanism and the ranking results, which I ran on real data.
- **Medium** for the client-side prompt UX. Claude Code is documented. The Claude Desktop prompt UI and its use of server `instructions` are not documented officially.

### Options compared
| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| MCP prompts per Role only | Necessary, not sufficient | Native slash commands in Claude Code (`/mcp__<server>__<prompt>`). Desktop: "+" menu, then "Add from <server>". | Prompt output is injected as a user message, not as a system prompt, so it is a hint and not a guarantee. Claude Code splits prompt arguments on whitespace, so a question cannot be an argument. | code.claude.com/docs/en/mcp; MCP spec 2025-11-25 prompts |
| Role-scoped tool subsets (3 registrations via `--role`) | Good for the demo and the bonus | Smaller tool list, so better tool choice. Works without `list_changed`. Clean namespaces. | With all three enabled, Claude sees duplicate tools and may pick the wrong Role. Switching Role means switching server. | Subagent review of the SDK and Claude Code docs; Desktop reportedly caches `tools/list` |
| Dynamic tool list (`list_changed`) | Poor | One server | Claude Code re-fetches the list, but Desktop reportedly does not. | github.com/anthropics/claude-code/issues/38324 (unconfirmed) |
| `role` argument on every tool plus server-side shaping | **Best core** | One connection. Role switching works mid-demo. Results differ in substance by construction. Easy to test with pytest. | Claude could pass the wrong role, so we echo `role` in every Evidence item and check it in the eval. | Design; see experiments |
| outputSchema / structuredContent | Use, carefully | SDK 2.x builds `outputSchema` from return types | Claude Code versions disagree on whether they show `structuredContent` or `content`, so the two must be equivalent. `mcp` 2.x renamed FastMCP to `MCPServer`, so pin the version. | claudeissues 15412 / 64316 / 4427; local test, mcp 2.3.0 |
| Per-Role views + LOGIN roles + GRANT | **Do it**: substance plus the bonus | Physically separates the Roles: the Manager cannot read raw lines. Full-text search over a view works per Role. | No row-level security, so filtering is done by views only. A full-text index on a view is a snapshot that needs `REINDEX`. | Experiment B |
| Role-specific defaults (window, limit, grouping) | Use within shaping | Cheap; changes the Evidence itself | On its own, it only changes length | — |

### Experiments run
Branch: https://github.com/OrFrederick/serenedb-log-mcp/tree/research/4-OrFrederick/research/4/OrFrederick. All numbers come from the full `bgl` table (4,747,963 rows) on SereneDB 26.09.1.3.

**A. Manager ranking** (`features.sql`, 131 ms; `score.py`). The query computes per-category features over 348,460 alert lines and 41 alert categories. A node-card-hour is one node card (for example `R27-M0-NA`) active in one clock hour. It is a measured way to de-duplicate bursts, and it is **not** an incident count.

| Rank | Category | Score | Rank by lines | Lines (share) | Node-card-hours | Midplanes /128 | Active weeks /31 | Last-28d rate ratio | Days since last |
|---|---|---|---|---|---|---|---|---|---|
| 1 | APPSEV | .96 | 3 | 49,651 (14.2%) | 24,528 | 128 | 20 | 1.84 | 0 |
| 2 | KERNMNTF | .94 | 4 | 31,531 (9.0%) | 13,864 | 128 | 15 | 0.48 | 0 |
| 3 | KERNMC (machine check) | .86 | 16 | 342 (0.1%) | 295 | 109 | **31** | 0.47 | 1 |
| 3 | KERNRTSP (rts panic) | .86 | 8 | 3,983 (1.1%) | 544 | 103 | **31** | 0.30 | 1 |
| 5 | KERNSOCK | .83 | 18 | 209 (0.1%) | 205 | 91 | 15 | 0.34 | 0 |
| 10 | KERNDTLB | .63 | **1** | 152,734 (43.8%) | 366 | 44 | 7 | 0 | 36 |
| 11 | KERNSTOR | .62 | **2** | 63,491 (18.2%) | 1,347 | 45 | 4 | 0 | 42 |

What the ranking shows:
- **Pareto alone misleads.** The two largest categories hold 62% of alert lines. Both are short bursts on at most 45 midplanes and were silent in the final 5 to 6 weeks.
- **A tiny category ranks high.** KERNMC is 0.1% of the lines but rank 3. It appeared in every one of the 31 weeks, on 109 of 128 midplanes.
- **The score is not just volume.** Kendall τ between the score order and the line-count order is 0.60.

Sensitivity of the weights:
- **Drop one feature.** Removing any single feature keeps the same top-5 set, except that removing persistence swaps KERNSOCK for APPOUT. Kendall τ against the default order stays between 0.83 and 0.94.
- **Random weights.** Over 2,000 random weightings (Dirichlet(1,…,1)):

  | Category | In the top 5 |
  |---|---|
  | APPSEV | 100% |
  | KERNMNTF | 100% |
  | KERNMC | 91% |
  | KERNRTSP | 89.5% |
  | KERNSOCK | 64.6% |

  Median τ against the default order is 0.81, and the 5th percentile is 0.66.
- **Conclusion.** The ranking is robust to the weights, so equal weights are defensible. Present the score as a band (top 5) and not as a precise number.

**B. Access control per Role** (`rbac.py`, `rbac_idx.py`, `rbac_vidx.py`). I worked on `r4_bgl`, a copy of the last 8 days (17,547 rows), and did not GRANT or REVOKE anything on the shared tables.

Setup:
- `CREATE VIEW r4_v_engineer | r4_v_teamlead | r4_v_manager`
- `CREATE ROLE r4_<role> LOGIN PASSWORD '…'`
- `GRANT SELECT ON r4_v_<role> TO r4_<role>`
- Password login over TCP works.

Results:
- **Own view works.** Each role reads its own view.
- **Everything else is denied** (`permission denied for table r4_bgl`, `… for table bgl`, `… for view r4_v_teamlead`), on:
  - `r4_bgl`
  - `bgl`
  - the other Roles' views
  - `CREATE VIEW` (`permission denied for schema public`)
- **A full-text index on the base table follows the table's permissions.** An index relation cannot be granted (`relation "r4_bgl_idx" does not exist`).
- **A full-text index on the view works per Role.** `CREATE INDEX r4_veng_idx ON r4_v_engineer USING inverted (line_no, content r4_dict)` took 99–2,813 ms for 17.5k rows. `r4_engineer` can run `@@ 'panic'`. `r4_manager` gets `permission denied for view r4_v_engineer`.
- **Design consequence.** Use one connection per LOGIN role and never `SET ROLE`. The coordinator notes that SET is server-wide in 26.09.1.3.
- **Docs mismatch.** On 26.09.1.3 the documented `CREATE TEXT SEARCH DICTIONARY … AS split_text(...)` is a syntax error. The `(TEMPLATE = 'segmentation')` form works.

**C. One real BGL issue: machine checks followed by rts panics.** Files: `precede.sql`, `mc_follow.sql`, `example.sql`, `share_by_month.sql`. Each query ran in 57–974 ms over the whole of BGL.

- **One case, node `R27-M0-NA-C:J11-U11` on 2006-01-03:**
  - 03:24:46.84: KERNMC `machine check interrupt (bit=0x1d): L2 dcache unit write data parity error`.
  - Then 134 FATAL register-dump lines (`d-cache flush parity error...1`, `imprecise machine check...1`).
  - 03:25:05.50: KERNRTSP `rts panic! - stopping execution`, 18.7 s after the machine check.
  - 03:25:10–03:25:25: 8 APPSEV `ciod: Error reading message prefix on CioStream socket` lines from I/O nodes on midplane R27-M0.
- **Machine check usually means a panic follows.** 216 of 342 KERNMC lines (63%) are followed by an rts panic on the same node within 120 s. By sub-type:
  - `DDR machine check register`: 47 of 47
  - `L2 dcache unit write data parity`: 15 of 15
  - `L3 major internal error`: 24 of 57
- **The cause of panics changed over time.** rts panic lines fell from 2,579 in June to 44–47 a month in Oct–Dec. The share preceded by a machine check rose from 0.7% in June to 60.9%, 68.1% and 93.2% in Oct, Nov and Dec, which is 101 of 137 lines in Q4.
- **June looks like a different cause.** June panics are preceded by `rts internal error`. That template appears 2,190 times in that position, and those lines carry no alert label. June also has mass panics, for example on all 32 cores of node card R27-M0-NA on 2005-06-14 at 11:16:50–51.
- **The machine checks are spread out.** KERNMC appears on 265 nodes across all 64 racks, and only 8 nodes repeat on more than one day. The busiest rack is R12, with 16 lines.

### Answer templates
The answers always contain Observed Facts with `[E<n>]` citations, Hypotheses, Unknowns and Next Step. Every number cites its Evidence id, Data Scope and whether the result is Truncated.

| | Engineer | Team Lead | Manager |
|---|---|---|---|
| Questions | What failed, where, and what happened just before and after? | What recurs, is it rising, which Components and Teams, and what to look at first? | Which observed problems first, and what to decide? |
| Required sections | Failure (Component, node, timestamp); Timeline ±N s with raw lines; Precursors and after-effects; Ruled out | Recurring patterns with counts over a stated window; Trend per bucket; Components → Teams (Ownership Table); Co-occurrence; Investigation order | Bottom line first (one sentence); Ranked list (top 5) with the measured features and the Pareto share; What is not measured; One decision |
| Allowed Evidence | Raw Log Entries, nearby events, full-text hits, small local counts | Aggregates, trends, co-occurrence rates, up to 3 example lines per pattern | Aggregates and the ranking only. No raw lines and no node ids. |
| Next Step | A concrete check (query or command) | An investigation priority with an owner Team | A prioritised action and decision, explicitly not a fix |

Precedents:
- **Engineer:** the SRE Workbook working document (theories, causes ruled out, logs) and PagerDuty's subject-matter-expert report in Condition / Actions / Needs (CAN) form.
- **Team Lead:** ITIL problem management (trend analysis and known errors).
- **Manager:**
  - The SRE book's VP status email without technical detail.
  - SBAR and bottom-line-up-front (BLUF) briefing formats.
  - FMEA risk priority numbers (RPN), but without the multiplication. The AIAG-VDA 2019 FMEA handbook replaced RPN because multiplying ordinal scales hides very different profiles behind the same number.
- **Unknowns:** ICD 203 standards 2 to 4 (express uncertainty, keep information apart from judgment, consider alternatives).

Guard rails:
- The Manager frame carries `not_measured`.
- The Manager prompt says: if asked about cost, downtime or customers, answer "not measurable from these logs", then say which data would be needed.
- A pytest eval checks Manager answers for currency, hour and customer terms outside the Unknowns section.
- The standard Unknowns are:
  - Log Entries are not incidents.
  - BGL alert labels are a property of the Dataset.
  - Logs are not complete for causality.

### Example answers (same issue, abbreviated)
**Engineer: "What failed on R27-M0-NA-C:J11-U11 at 03:25 on 2006-01-03?"**
- **Observed Facts:**
  - KERNMC L2 dcache write-data parity error at 03:24:46.84 [E1].
  - 134 FATAL register-dump lines, including `d-cache flush parity error 1` [E1].
  - rts panic at 03:25:05.50, 18.7 s later [E1].
  - 8 APPSEV ciod socket-read errors from I/O nodes on R27-M0 within 20 s [E2].
- **Hypothesis:** a cache parity hardware fault on this compute chip stopped the job. The ciod errors are a consequence, not the cause.
- **Unknown:** whether the parity fault repeats on this chip. This is its only KERNMC in 214 days [E3].
- **Next Step:** run `nearby_events(node='R27-M0-NA-C:J11-U11', ±24 h)` and pull this chip's hardware or diagnostic records.

**Team Lead: "Which kernel problems recur and what should we investigate first?"**
- **Observed Facts:**
  - rts panic lines fell from 2,579 in June to 44–47 a month in Oct–Dec [E4].
  - In Q4, 101 of 137 rts panics (74%) followed a machine check on the same node within 120 s, against 0.7% in June [E5].
  - Two machine-check sub-types preceded an rts panic every time: DDR at 47/47 and L2 write parity at 15/15 [E6].
  - 265 nodes across 64 racks, and only 8 nodes repeat [E7].
- **Hypothesis:** the residual panics come from distributed memory-hardware errors, not from one bad rack. The June wave had a different cause (`rts internal error`).
- **Unknown:** the owning Team. The Ownership Table is not loaded yet, and both categories are Component `KERNEL`.
- **Next Step:** first, the KERNEL owner investigates DDR and L2-parity machine checks as the precursor. Second, review why the June `rts internal error` wave stopped.

**Manager: "Which observed problems deserve attention first?"**
- **Bottom line:** five patterns stand out [E8]:
  - APPSEV and KERNMNTF: high volume, everywhere, still active.
  - Machine checks and rts panics: small but present in every week on most of the machine.
  - KERNSOCK.
- **The two biggest by volume rank 10th and 11th.** They are 62% of alert lines, but they were bursts that have been silent for more than 5 weeks [E8].
- **The ranking holds.** It stays the same under 2,000 alternative weightings: about 90% for the first four [E9].
- **Not measured:** cost, downtime, job loss, customer impact.
- **Decision:** fund an investigation of the machine-check → panic link before the high-volume burst categories.

### Sources
- https://sre.google/sre-book/managing-incidents/
- https://sre.google/sre-book/example-postmortem/
- https://sre.google/workbook/incident-response/
- https://response.pagerduty.com/before/different_roles/
- https://response.pagerduty.com/during/external_communication_guidelines/
- https://www.atlassian.com/incident-management/incident-communication/templates
- https://wa.aws.amazon.com/saas.concept.coe.en.html
- https://dora.dev/guides/dora-metrics-four-keys (contrast only: needs deploy data we lack)
- https://wiki.en.it-processmaps.com/index.php/Problem_Management_-_ITIL_V2
- https://accendoreliability.com/better-way-rpn/ (RPN critique, secondary source)
- https://www.ihi.org/resources/Pages/Tools/sbartoolkit.aspx
- https://www.dni.gov/files/documents/ICD/ICD-203.pdf
- Jin et al., Oasis, FSE 2023, https://arxiv.org/abs/2305.18084. It writes one outage summary, not one per audience. I found no paper that does per-audience summaries.
- RCACopilot: https://arxiv.org/abs/2305.15778
- Log3C: https://www.microsoft.com/en-us/research/publication/identifying-impactful-service-system-problems-via-log-analysis/
- https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- https://code.claude.com/docs/en/mcp
- https://claude.com/docs/connectors/building
- SereneDB docs:
  - https://serenedb.com/docs/security/privileges
  - https://serenedb.com/docs/sql/indexes/inverted (index on views)

### Risks / unknowns
- **Desktop behaviour is unverified.** I could not verify by hand that the Desktop prompt UI exists or that Desktop reads server `instructions` (reportedly ignored). Test before the demo.
- **Claude can pass the wrong `role`.** Mitigate by echoing `role` in Evidence, adding an eval and offering `--role` pinning.
- **A view index is a snapshot.** It needs `REINDEX` after a reload. Our corpus is static, so the risk is low.
- **The trend feature is crude.** It compares the last-28-day rate with the rate over the whole period. I rejected OLS slope because single-day bursts made it noisy (APPTO had +0.11 per week from one day). The score still uses BGL alert categories. With the Ownership Table, add the Team count as a sixth feature and group by template or Component.
- **The ranking depends on the grouping.** It is not tested on OpenStack yet.

### Open questions for the humans
1. Should the core be one server with a `role` argument, or three registrations? I recommend one server, with `--role` as an option.
2. Should the Team Lead be allowed up to 3 example lines? I recommend yes, but through the Engineer search tool, cited separately.
3. Can we accept "node-card-hours" as the measured de-duplication unit until the incident-model issue settles? Proposed glossary addition: **Active Unit-Hour**, meaning one emitting unit active in one hour, which is not an Incident.
4. Should the KERNMC → KERNRTSP story be the worked investigation? It gives distinct Evidence per Role and uses both search and aggregation.
