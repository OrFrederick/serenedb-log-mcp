# serenedb-log-mcp

Hackathon entry for the SereneDB challenge "Making Sense of Logs". The project is an MCP server that exposes log search and aggregation over SereneDB, with answers tailored to three Roles.

Read these before working:
- `docs/challenge-brief.md`: what the judges want and how they score it.
- `docs/decisions.md`: settled decisions, open questions and verified facts.
- `GLOSSARY.md`: canonical terms. Use them exactly. Never call matching Log Entries "incidents".

Work is ticket based. GitHub issues are the source of truth for tasks and research. Before you start an issue, check its comments and assignee, so you don't duplicate parallel work.

Use the SereneDB docs MCP (`serenedb-docs`) for SQL syntax. Don't guess SereneDB syntax, because it differs from Postgres in places.

## Agent skills

### Issue tracker

Skill-generated PRDs and issues live as local markdown under `.scratch/<feature>/`. Team tickets stay in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: domain language in `GLOSSARY.md`, decisions in `docs/decisions.md`. See `docs/agents/domain.md`.
