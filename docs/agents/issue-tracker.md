# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues in `OrFrederick/serenedb-log-mcp`. Use the `gh` CLI for all operations.

This clone has two remotes (`origin` = `OrFrederick/serenedb-log-mcp`, `fork` = `NoNaim95/serenedb-log-mcp`). Always pass `-R OrFrederick/serenedb-log-mcp` so `gh` never picks the fork.

## Conventions

- **Create an issue**: `gh issue create -R OrFrederick/serenedb-log-mcp --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> -R OrFrederick/serenedb-log-mcp --comments`, filtering comments by `jq` and also fetching labels and assignees.
- **List issues**: `gh issue list -R OrFrederick/serenedb-log-mcp --state open --json number,title,body,labels,assignees,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], assignees: [.assignees[].login], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> -R OrFrederick/serenedb-log-mcp --body "..."`
- **Apply / remove labels**: `gh issue edit <number> -R OrFrederick/serenedb-log-mcp --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> -R OrFrederick/serenedb-log-mcp --comment "..."`

Before starting work on an issue, check its comments and assignee, so you don't duplicate parallel work (see `CLAUDE.md`).

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> -R OrFrederick/serenedb-log-mcp --comments`.
