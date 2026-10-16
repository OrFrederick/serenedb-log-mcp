# Challenge brief (summary)

Our summary of the SereneDB challenge at National AI Hackathon 2026 (SchubKI). The work days are 17 and 18 October 2026, and judging is on 19 and 20 October. The organisers' PDF is the source of truth. This file records what we took from it.

## Challenge: "Making Sense of Logs: The Right Insights for the Right People"

Build a working prototype that turns log data into answers for three Roles (see `GLOSSARY.md`). SereneDB does the searching and the calculating. An AI agent calls our tools and explains the returned Evidence.

- **Engineer**: What failed? In which Component? What happened just before and just after? The answer gives the relevant error lines and nearby events, a possible cause, and the next check to run.
- **Team Lead**: Which problems recur, how often, and which Components or Teams are involved? The answer uses counts and trends over a stated period plus the Ownership Table, and says what to investigate first.
- **Manager**: Which observed problems deserve attention first? The answer is a short, prioritised explanation of risks and actions. It uses only measured frequency and affected Components. It must **not** invent financial loss, downtime or customer impact.
- All three Roles use the same agent and the same Base Corpus. The questions, the Evidence and the actions must differ between Roles. Making only the answer length different does not count.

## What the organisers supply
- A SereneDB instance.
- Corpus links and a schema.
- A **loader** for BGL and OpenStack.
- A **team-ownership table** that maps Components to Teams in one fictional platform org.
- Docs at serenedb.com/docs and a docs MCP server: `claude mcp add --transport http serenedb-docs https://api.serenedb.com/mcp`.

The README must explain how to load the base data with the **supplied loader**.

## Tool requirements
- A small set of parameterised tools, for example search logs, get nearby events and calculate summaries. Each tool description must state its purpose, its inputs and its outputs, so the agent can choose the right tool. Open-ended SQL generation is not required.
- Search and calculations run in SereneDB. Search returns a limited set of entries. Aggregation returns calculated counts and trends.
- Each tool result includes the query and its filters, and flags when the result is truncated. A few sample lines do not show how often something occurs.
- Engineer: full-text search with time filters, plus nearby events.
- Team Lead and Manager: counts grouped by Component or Team, plus a trend over time.
- Optional: phrase matching, fuzzy matching and comparisons.
- Recommended: an MCP server, so Claude is the agent. A CLI or a UI is also acceptable.

## Rules for evidence
- Every measured claim must come from a query that actually ran. Keep the query and its result.
- Keep observed facts separate from hypotheses and from recommended checks. State what remains unknown.
- Count matching Log Entries accurately. Do **not** treat matching entries as separate incidents automatically.

## Minimum requirements
1. The agent answers at least one question for each Role, with relevant evidence, the right level of detail and a next step that fits the Role.
2. The answers use both retrieval and aggregation, and use both BGL and OpenStack. At least one answer uses full-text search. At least one answer uses an aggregation over the whole Base Corpus, for example counts by Dataset or by Team. State the scope and the filters.
3. The recommendations are practical and backed by the findings.
4. The tools are exposed with clear descriptions.
5. The answers show their work: the queries and results behind every claim, with hypotheses and limitations labelled.
6. The project runs from a clean checkout and includes setup instructions.

## Deliverables
- A public repository with a README that covers the workflow, setup, how to run it and its limitations.
- A working prototype.
- Documentation for each tool: description, inputs and outputs.
- **One worked investigation** of a real issue in the base data. For each Role, show the questions, the tool calls, the queries, the results, and the answer with its action. Include both search Evidence and an aggregation. State what the data does and does not establish.
- A demo of 5 minutes or less, plus slides.

## Scoring
| Criterion | Weight |
|---|---|
| Role-appropriate insight | 30% |
| Search + analytics together | 25% |
| Recommendation quality | 25% |
| Execution, novelty, presentation | 20% |

Special awards, judged separately:
- **Best MCP Server**: clear tool descriptions, correct tool choice by the agent, sensible result limits, and Claude can connect using our instructions.
- **Working with Huge Datasets**: OTel Parquet data or larger Loghub sets. Show how much data was queried, the query times and the costs.

Informal bonuses:
- Correlating logs with traces or metrics.
- Row-level security per Role.
- An agent-managed index that is created and later removed, with a timing comparison that shows the trade-off between creating the index and querying with it.

## Data
- **BGL**: 4,747,963 lines over 214.7 days, 709 MB, at `https://zenodo.org/records/8196385/files/BGL.zip?download=1`. Use it for search and trends.
- **OpenStack**: 207,820 lines, 58.6 MB, at `https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1`. Use it for aggregation: request latency, request and tenant IDs, labelled anomalies.
- Loghub (github.com/logpai/loghub) has samples of 2,000 lines for each dataset.
- Loghub requires a citation: Zhu et al., ISSRE 2023. BGL also requires: Oliner & Stearley, DSN 2007.
- Remote OTel Parquet data, which we query in place: `https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet` … `part_049.parquet`.
- Multimodal options: RCAEval and Nezha.
