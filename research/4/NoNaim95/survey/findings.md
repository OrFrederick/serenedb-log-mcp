# #4 survey findings (NoNaim95's Claude, subagent report, saved by parent)

## Recommendation
Layered Role mechanism, soft to hard:
1. Server `instructions`, max 4 KB, rules first. Claude Code loads them at session start; the cutoff is 4,096 chars (code.claude.com/docs/en/mcp#tool-search-for-mcp-server-authors).
2. One MCP prompt per Role, invoked by the user. Prompts are user-controlled under the MCP spec 2025-11-25. Claude Code exposes them as `/mcp__srv__prompt`, with arguments split on whitespace. The Claude Code desktop app does not surface them (issue #75234, closed not planned).
3. A `get_role_brief(role)` tool that mirrors the prompt text, plus `role://<role>` resources. These are the fallback when no prompt was invoked.
4. Role-shaped tools. The rank tool returns factor tables only; search returns capped lines plus a truncated flag.
5. A `submit_answer(role, ...)` answer-contract tool. Its inputSchema is the Role template. The server validates: evidence ids exist, numbers appear in cited results, the Manager cites no search Evidence, and no forbidden terms appear.
6. Optional hard isolation: one server entry per Role, each with its own `LOG_MCP_ROLE` and SereneDB LOGIN role, granted on views only.

## SereneDB ACL experiment (26.10.1)
Script: acl_setup.sql / acl_test.py / acl_test_fts.py.
- Engineer, Lead and Manager views; three LOGIN roles, each granted SELECT on its own view only.
- Each role read only its own view (2–28 ms). Everything else was denied, including `CREATE TABLE`.
- Querying an inverted index needs SELECT on its base table. Wrapping the index in a view works, and the `@@` predicate is pushed through: 24,619 rows in 4 ms, the same count as querying the index directly.
- Column-level `GRANT SELECT (cols)` works. `SELECT message`, `SELECT *` and `count(*)` via an index on `message` are all denied.
- `ALTER DEFAULT PRIVILEGES` is accepted but not applied.
- The dev container runs with trust auth, so a real demo needs SCRAM.
- The SereneDB blog announces RLS for October 2026 (serenedb.com/blog/state-of-serene-2026-09); it is not in 26.10.1.

## Manager formula
score = mean percent_rank of:
- frequency (entries);
- spread (distinct nodes or instances);
- persistence (active days);
- trend: ln((last30+1)/(prev30+1)), anchored at the end of the Data Scope;
- recency (days since last seen, reversed);
- Team count, once the Ownership Table exists.

Scores within 0.05 count as one tier. The query (priority.sql) runs in a median 0.059 s.

Top ranks on BGL (41 alert categories):

| Rank | Problem | Entries | Nodes | Active days | Score |
|---|---|---|---|---|---|
| 1 | KERNMNTF | 31,531 | 1,024 | 25 | 0.895 |
| 2 | APPSEV | 49,651 | 1,024 | 67 | 0.835 |
| 3 | KERNRTSP | 3,983 | 3,394 | 124 | 0.825 |
| 4 | KERNMC | 342 | 265 | 142 | 0.735 |

- Raw count alone ranks KERNDTLB (152,734) first. The composite pushes it to a tie at ranks 5–7 because it covers only 541 nodes and 11 days and has been absent for 37 days.
- Leaving out any one factor keeps 4 of the top 5 (3 of 5 without persistence). The top 3 stays in the top 5 in every variant.
- Rejected: FMEA RPN (multiplies ordinal scores; Bowles), risk matrices (Cox 2008), RICE and WSJF (Impact and Effort would be invented).

## Example: KERNMNTF ("Lustre mount FAILED")
- On 2006-01-03, 333 entries came from 333 nodes between 13:09:26 and 13:11:16 (110 s). Nothing else was logged from 13:00 to 13:20.
- 31,531 entries on 1,024 nodes over 25 active days, from 2005-08-03 to 2006-01-03. The peak month was Sep 2005 with 23,641 entries.
- Two message variants:
  - "point /p/gb1": 26,426 entries;
  - "block_id : location": 5,105 entries.

## Sources
- sre.google: sre-book/example-postmortem, managing-incidents; workbook/incident-response, postmortem-culture ("The exact revenue impact is unknown at this time"), on-call.
- response.pagerduty.com.
- atlassian.com/incident-management.
- GitLab handbook.
- dora.dev.
- Etsy Debriefing Guide.
- Jeli Howie.
- greatcircle.com sitrep.
- Google Security blog, Apr 2024 (security.googleblog.com/2024/04/accelerating-incident-response-using.html).
- RCACopilot arXiv 2305.15778.
- Ahmed ICSE'23 arXiv 2301.03797.
- Oasis arXiv 2305.18084.
- Meta engineering blog, 2024-06-24.
- arXiv 2602.09937.
- ALCE arXiv 2305.14627.
- Mullick arXiv 2406.03986.
- PagerDuty Advance docs.
- Datadog incident_ai docs.
