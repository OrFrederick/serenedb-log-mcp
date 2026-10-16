## Decision proposal

**Chosen option:** The Roles differ in substance through what the server returns.

**1. Tools**
- Every tool takes a required `role` enum (`engineer | team_lead | manager`).
- The `role` selects:
  - that Role's SereneDB LOGIN-role connection;
  - its defaults;
  - its output shape.
- Each result carries a Role `answer_frame` and, for the Manager, a `not_measured` list. Both sit inside `structuredContent`, with the same text mirrored in `content`.

**2. Guidance layers**
- Server `instructions`.
- MCP prompts `engineer`, `team_lead` and `manager`, plus a `get_role_brief(role)` tool that mirrors them.
- The settled `verify_answer` validator, extended with Role checks:
  - every Evidence id exists;
  - every number appears in its cited Evidence;
  - the Manager cites no raw-line Evidence;
  - no forbidden cost, downtime or customer terms appear.

**3. Defence in depth for the access-control bonus**
- Per-Role views with LOGIN roles and GRANT on the views only.
- Optional `--role` pinning of one server registration to one Role.

**4. Manager ranking**
- The equal-weight mean of percentile ranks over five factors:
  - Incidents (gap 300 s, merged across sources, per #1);
  - spread in midplanes (instances or hosts for OpenStack);
  - active weeks;
  - the 28-day Incident rate against the rate over the whole period;
  - recency.
- Present it as a top-5 band, next to the raw-line Pareto share and `top_node_share`.

**5. Worked thread across Roles**
- KERNMC → KERNRTSP → APPSEV, on R27-M0-NA-C:J11-U11 on 2006-01-03.

**Rationale:** Both Claudes converged after independent measurement. Role-shaped results make the answers differ in what each Role receives: a Manager answer cannot cite raw lines it never got. `verify_answer` then checks the final text. The Incident-based ranking was re-derived by both sides on the full BGL Dataset:
- it gives the same top 5 as the earlier node-card-hour version (τ = 0.857);
- it is more robust under 2,000 random weightings: all five stay in the top 5 in at least 90.8% of them;
- it demotes single-burst categories such as APPOUT (1 Incident, rank 6 → 13) and the volume leaders KERNDTLB and KERNSTOR (rank 1–2 by raw lines → 10 and 15).

The database grants hold for tables and views. Any login role can still read and write server files and change a server-wide setting, so the grants are presented only as defence in depth.

**Rejected options:**
- **Prompts only:** they are user-controlled, and the Claude Code desktop app does not list them (#75234).
- **Three registrations as the core:** Claude sees duplicate tools, and switching Role means switching servers. Keep `--role` as an option only.
- **Dynamic `list_changed` tool lists:** Claude.ai desktop caches `tools/list` (#38324).
- **NoNaim95's weighted composite (b) with effective sources as spread:** it ranks KERNSTOR, 8 Incidents and silent for 42 days, at #2.
- **Node-card-hours as the frequency unit:** it inflates single bursts (APPOUT #6) and needs a new glossary term.
- **Borda, or FMEA-style multiplication:** both promote tiny groups. The AIAG-VDA 2019 handbook replaced RPN with Action Priority.
- **Database grants as the main guarantee:** a Manager role can still run `read_csv('/etc/passwd')`, `COPY … TO` and a server-wide `SET enable_external_access`.

**Decisions the humans must make:**
1. Do we present the access-control bonus as "defence in depth" with the limits stated in the README, or skip it?
2. Should the weights be fixed equal weights, or a tool parameter? We recommend fixed, with the sensitivity result documented.
3. Who checks Claude Desktop by hand (prompt menu, use of `instructions`, `structuredContent`) before the demo?
4. Should RLS be re-checked on the demo SereneDB version? It is announced for October 2026 but is not in the docs yet.
