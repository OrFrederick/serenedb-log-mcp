## Research: ugnmura's Codex

**Recommendation:** Three advisory MCP Role prompts over the same core tools, with different decision/Evidence/action contracts: Engineer → correlated raw context and diagnostic check; Team Lead → recurrence, comparable-window aggregates, ownership coverage and investigation order; Manager → transparent measured-frequency attention list and a decision, with impact explicitly unknown. Validate Evidence schemas and limits server-side. Use actual identity + views/grants only for the optional access-control track; a prompt or `role=manager` argument cannot enforce it. Prefer a disclosed frequency rank to an arbitrary weighted “risk” score.

**Confidence:** high in the mechanism boundaries and evidence-first templates; medium in run-to-run Role adherence until repeated live Claude/host tests execute.

### Options compared

| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| **MCP prompts + validated defaults + Evidence schemas** | Recommended core | Same agent/Base Corpus; substantive Evidence/action differences | Prompts can be ignored; host final Markdown is not constrained by MCP outputSchema | [Prompts spec](https://modelcontextprotocol.io/specification/2025-06-18/server/prompts), [our templates](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/4-ugnmura/research/4/ugnmura/role-prompts.md) |
| Role-scoped tool subsets | Possible later | Reduce irrelevant choices | Duplicated vocabularies and maintenance; listing is not authorisation | [Claude SDK permissions](https://platform.claude.com/docs/en/agent-sdk/python) |
| Typed final-answer schema | Strong eval/rendering aid | Verifiable metrics and citation paths | SDK enforcement does not automatically apply to Desktop/Code free text | [SDK structured output](https://platform.claude.com/docs/en/agent-sdk/python) |
| Separate views/LOGIN/grants | Optional access-control extension | Actual server-side boundary | Needs verified grant semantics/identity isolation, no raw-table escape hatch; not RLS | Existing decisions; actual grant experiments still required |
| Separate product agents | Poor core fit | Strong workflow separation possible | Conflicts with settled same-agent MCP interface | Challenge brief and decisions |

### Experiments run

Independent raw OpenStack scan: **207,820** physical Log Entries. Strict-header prototype parses **207,812** and discloses **8** remaining parser gaps rather than dropping them. WARNING/ERROR/CRITICAL frequency ranks imagecache first (**3,059**); ERROR/CRITICAL-only ranks periodic_task first (**184**). A hypothetical frequency/emission-fraction weighted score changes the top Component as weights change. This demonstrates sensitivity, not a justified risk weighting. [Code and numeric results](https://github.com/OrFrederick/serenedb-log-mcp/tree/research/4-ugnmura/research/4/ugnmura).

Independent verification of the corpus agent's slow-build story directly from raw archive + anomaly_labels.txt: **four** labelled instances, **25** explicitly tagged INFO entries each, across **three** Components. Logged build durations **30.32, 32.12, 42.81, 51.59 seconds**; same-file unlabelled baseline **193** build entries, median **20.61**, max **27.61** seconds. Threshold >22 selects four labelled +52 unlabelled builds; >30 selects four labelled +0 unlabelled; >35 selects two labelled. Post hoc threshold selection is **not** held-out detection accuracy; unlabelled need not mean normal. Severity-only investigation misses this story.

The [report](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/4-ugnmura/research/4/ugnmura/report.md) gives three same-issue authored answers with distinct raw-context diagnosis, grouped coordination, and sponsorship decision. All are labelled offline research prototypes; their references point to executed scan artifacts, **not fabricated MCP Evidence ids**. Rerun via SereneDB/MCP before using them in the worked investigation. Issue #3's official SDK experiments retrieve all three Role prompts in legacy and modern protocol eras; no live Claude answer was generated.

### Sources

[Google SRE troubleshooting](https://sre.google/sre-book/effective-troubleshooting/), [monitoring](https://sre.google/sre-book/monitoring-distributed-systems/), [incident coordination](https://sre.google/workbook/incident-response/), [postmortem practice](https://sre.google/workbook/postmortem-culture/); [DORA metric definitions](https://dora.dev/guides/dora-metrics/). The Base Corpus alone does not establish DORA deployment/recovery metrics, business loss or downtime. [Raw archive](https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1), SHA256 in artifact.

### Risks / unknowns

Manager priority proposal: within a fixed explicit Data Scope/filter, rank matching Log Entry count descending, affected Component count as tie-break; equal pairs share rank. Report severity/threshold sensitivity. Team count is coordination context; no invented weights, Team names, business risk or incompatible pooled rates. Unequal file coverage cannot establish temporal growth. Ownership Table remains unavailable. Role prompt adherence is measurable, never guaranteed. MCP output schema is tool output, not host answer schema. Separate views require both row filtering and column projection/aggregation when raw text must be unavailable.

### Open questions for the humans

Confirm the measured-frequency attention policy and slow-build example. Choose supported Desktop/Code versions for prompt-selection smoke tests. Optional access control needs authenticated Role identities and verified negative grants tests before making any claim of enforced separation.
