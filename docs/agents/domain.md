# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This repo is **single-context**, but it does not use the default file names. Where a skill says `CONTEXT.md` or "ADRs", read the files below instead.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root: the canonical domain language. It plays the role of `CONTEXT.md`.
- **`docs/decisions.md`**: settled decisions, open questions and verified facts. It plays the role of `docs/adr/`.
- **`docs/overnight-decisions.md`**: decisions made during the overnight run that humans have not confirmed yet. Treat them as tentative.
- **`docs/adr/`**: numbered ADRs, if any exist later.

If any of these files don't exist, **proceed silently**. Don't flag their absence and don't suggest creating them upfront. The producer skill (`/grill-with-docs`) adds terms and decisions lazily as they get resolved. New terms go in `GLOSSARY.md` and new decisions go in `docs/decisions.md`.

## File structure

```
/
├── GLOSSARY.md                  ← domain language (instead of CONTEXT.md)
├── docs/
│   ├── decisions.md             ← settled decisions (instead of ADRs)
│   ├── overnight-decisions.md   ← unconfirmed decisions
│   └── adr/                     ← optional, future numbered ADRs
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis or a test name), use the term as `GLOSSARY.md` defines it. Don't drift to synonyms the glossary explicitly avoids. In particular, never call matching Log Entries "incidents".

If the concept you need isn't in the glossary yet, that's a signal. Either you're inventing language the project doesn't use (reconsider), or there's a real gap (note it for `/grill-with-docs`).

## Flag decision conflicts

If your output contradicts an entry in `docs/decisions.md`, surface it explicitly rather than silently overriding it:

> _Contradicts the settled "Evidence Ledger" decision in docs/decisions.md, but worth reopening because…_
