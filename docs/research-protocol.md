# Research protocol

Research questions live in GitHub issues with the `research` label. Each teammate's Claude works through the phases below. Several Claudes work on the same issue in parallel. That is intended: independent answers followed by deliberation beat one answer.

## Phase 1: Independent research
1. Read `CLAUDE.md`, `docs/challenge-brief.md`, `docs/decisions.md`, `GLOSSARY.md` and the issue body.
2. **Don't read other teammates' `Research` comments yet.** Anchoring on another answer defeats the point of independent research.
3. Split the issue into sub-questions. Spawn parallel subagents: web and docs research, the SereneDB docs MCP, and hands-on experiments.
4. Run experiments wherever a claim can be tested, such as a benchmark, a query on real data or a prototype. Put experiment code on a branch `research/<issue>-<github-user>`, never on `main`. Use your own scratch directory under `research/<issue>/<github-user>/`, so parallel work never touches the same files.
5. Post one comment on the issue in the format below.

```md
## Research: <github-user>'s Claude

**Recommendation:** <one paragraph>
**Confidence:** high / medium / low, and why

### Options compared
| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|

### Experiments run
<what ran, the numbers, a link to the branch or commit>

### Sources
<URLs, doc pages, papers>

### Risks / unknowns
### Open questions for the humans
```

## Phase 2: Deliberation
Start this phase when the issue has at least 2 `Research` comments, or all 3 teammates have posted.
1. Read every `Research` and `Deliberation` comment.
2. Verify the claims that matter. Re-run a disputed benchmark or re-check a source. Don't trust another Claude's claim without checking it.
3. Post a comment headed `## Deliberation: <github-user>'s Claude`. Cover where the comments agree, where they disagree and who is right with evidence, and give your revised recommendation.
4. Repeat until the comments converge or a disagreement stands that only the humans can settle.

## Phase 3: Decision proposal
The issue assignee, or the first Claude to see the comments converge, posts `## Decision proposal`. It contains the chosen option, a one-paragraph rationale, the rejected options with one line each, and the decisions the humans must make. The humans confirm in a comment. The confirmed decision then goes into `docs/decisions.md`.

## Rules
- Use the canonical terms from `GLOSSARY.md`. A Log Entry is not an incident.
- Facts need a source or an experiment. Label guesses as guesses.
- Don't push to `main`, and don't close or edit other people's comments.
