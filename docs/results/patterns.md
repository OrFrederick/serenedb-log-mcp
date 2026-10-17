# Pattern dictionary: agreement, coverage and derive time (T9, #6)

Every Log Entry gets a Pattern inside SereneDB, in one derive step over distinct messages
(`sql/derive/20_patterns.sql`):

- **Tier 1** (`template_source = 'loghub2'`): a `CASE` chain generated at derive time from the
  Loghub-2.0 template files (`scripts/gen_pattern_sql.py`). Every template becomes an anchored RE2
  pattern; all strict patterns (`<*>` as `\S+`) come before all loose ones (`<*>` as `.*`), each half
  most specific first.
- **Tier 2** (`template_source = 'sqlmask'`): the generic g10 mask macro of #6 for every message
  Tier 1 does not match.
- NULL and blank messages get `template_source = 'empty'`.

`template_id` is the first 16 hex characters of `md5('<dataset>:<template text>')`; `family_id` is
the md5 of the same key without a trailing ` over <*> seconds`.

Measured on 2026-10-17 on the shared SereneDB 26.10.1 instance (amd64), on the Base Corpus as
loaded by the dev loader (`public.bgl`, `public.openstack`) and derived into schema `slm`.

## How to reproduce

```
make fetch-data                        # the Loghub-2.0 files, into SLM_DATA_DIR
make load-dev                          # raw tables, including the labels lh2_bgl and lh2_openstack
make derive                            # all derive steps; Pattern Tier 1 on by default
make score-patterns                    # the merge gate; exit 0 when it holds
SLM_REQUIRE_DB=1 make test-corpus      # golden numbers below (tests/corpus/test_patterns_corpus.py)
```

`uv run python scripts/derive.py --only 20_patterns` re-runs the Pattern step on its own.

## Agreement with the Loghub-2.0 labels (merge gate)

`scripts/score_patterns.py` runs the chain that the derive installed in `slm` over every labelled
Loghub-2.0 line, in SQL, and compares the chosen EventId with the gold one. A labelled line that no
template matches counts as a disagreement.

| Dataset | Labelled lines | Agreeing | Agreement | Tier 1 coverage | Agreement on Tier 1 lines |
|---|---:|---:|---:|---:|---:|
| bgl | 4,631,261 | 4,631,257 | 99.99991% | 99.99991% | 100% |
| openstack | 207,632 | 207,632 | 100% | 100% | 100% |

Gate: at least 99.99% on both Datasets: **pass**. The target of 99.9999% (#6, Python RE2 matcher) is
met too. The script prints shares rounded down to four decimals (99.9999% for BGL).

Every disagreeing template:

| Gold EventId | Assigned | Lines | Distinct messages | Why |
|---|---|---:|---:|---|
| BGL E7 | none (Tier 2) | 4 | 1 | The Loghub-2.0 Content of these 4 lines is cut off after 200 characters (`... Assembly Revision: `), while the template continues with `Code Revision: <*>, ... is usable but not a supported version`, so no regex for the template can match it. The raw messages of the same lines are complete (345 characters) and get E7 in `slm`. |

OpenStack has no disagreeing template.

Every `pattern_dict` row carries the EventId that the chain gives its message (358,354 of 358,354
BGL rows, 139,782 of 139,782 OpenStack rows), so the score applies to the derived dictionary.

The labels are matched on the Loghub-2.0 `Content` field, not joined to the raw messages: 312
distinct BGL contents (320 lines) differ from the dev loader's message text. They are the 4
truncated E7 lines and the lines of E278, E5, E6 and E9: the 306 `Kill job <n> timed out. Block
freed.` lines, where Loghub-2.0 parsed `Kill` as part of the header (E278), and 10 concatenated
lines (E5, E6, E9). The raw versions of the E278, E5, E6 and E9 lines get Tier 2 Patterns.

## Coverage per template_source

| Dataset | template_source | Patterns | Distinct messages | Log Entries | Share of Log Entries |
|---|---|---:|---:|---:|---:|
| bgl | loghub2 | 316 | 338,424 | 4,630,955 | 97.54% |
| bgl | sqlmask | 148 | 19,929 | 82,538 | 1.74% |
| bgl | empty | 1 | 1 | 34,470 | 0.73% |
| openstack | loghub2 | 48 | 139,734 | 207,632 | 99.91% |
| openstack | sqlmask | 24 | 47 | 180 | 0.09% |
| openstack | empty | 1 | 1 | 8 | 0.004% |

BGL: 465 Patterns over 4,747,963 Log Entries. OpenStack: 73 Patterns over 207,820 Log Entries.
4 of the 320 BGL templates match no raw message: E278, E5, E6 and E9 (see above).

With `SLM_PATTERN_TIER1=0` (CI, or if the Loghub-2.0 files may not be used), the same corpus gets
647 BGL and 72 OpenStack g10 Patterns, plus one `empty` Pattern each.

## Pattern Families

The suffix rule merges 8 pairs of BGL Patterns into one Pattern Family each (465 Patterns, 457
families; OpenStack has none):

- the six pairs from #6: E218/E112, E63/E34, E70/E40, E78/E43, E119/E67, E191/E92;
- E99/E59: `<*> tree receiver <*> in re-synch state event(s) (dcr <*>) detected` and the same with
  ` over <*> seconds`;
- E56 and a Tier 2 Pattern: `<*> torus processor sram reception error(s) (dcr <*>) detected and
  corrected` and its ` over <*> seconds` variant (13 Log Entries that Loghub-2.0 dropped).

Both extra pairs are the same wording drift. The rule is still untested against a reviewed family
list (#6 open question 3).

## Derive time

| Run | Pattern step | Whole derive |
|---|---:|---:|
| `make derive`, Tier 1 on (adapter, entries, Patterns, indexes) | 5.4 s | 14.3 s |
| `make derive`, `SLM_PATTERN_TIER1=0` | 4.5 s | 13.5 s |
| `derive.py --only 20_patterns` on derived, indexed tables, nothing changed | 3.6 s | |
| `derive.py --only 20_patterns`, first fill of indexed tables (`slm` after T4) | 235 s | |

Inside the Pattern step, the Tier 1 chain over the 358,354 distinct BGL messages and building
`pattern_dict` take 1.5 s; filling the Log Entry columns takes 2.7 s.

The last row is the cost of an UPDATE through an existing inverted index: 204 s for the 4.7M BGL
rows instead of 1.7 s, and other sessions on the shared instance stalled meanwhile. The step
therefore writes only Log Entries whose Pattern changes, so a re-run is cheap. To fill Patterns
into a schema that is indexed but has none yet, run the full `make derive` (14 s) rather than
`--only 20_patterns`.

## The catalogue and its search index (T14)

The `pattern` table is the catalogue behind `list_patterns`. The Pattern step also builds
`pattern_idx`, an inverted index over `pattern.template_text` (dictionary `pattern_text`: lower-case
words with English Snowball stemming, `frequency` and `norm` for BM25) and `dataset`, which
`search_patterns` reads. It lives in this step rather than in `90_indexes`, because
`CREATE OR REPLACE TABLE pattern` drops it, and `--only 20_patterns` must leave a searchable
catalogue. The step's legacy dictionary form for servers before 26.10.1 (`stemming = true`) is
untested, like the one of `log_text`.

Size of the catalogue as `list_patterns` returns it (Evidence JSON per page, counted on 2026-10-17
with `claude -p` and a fixed system prompt; Opus, Sonnet and Haiku 5.5 gave the same counts):

| Dataset | Patterns | Pages | Tokens per page | Tokens in total |
|---|---:|---:|---|---:|
| bgl | 465 | 8 | 3,968 to 8,453 (KERNEL alone fills pages 4 to 7) | 52,872 |
| openstack | 73 | 1 | 8,075 | 8,075 |

The #7 estimate of about 6.6k tokens for BGL counted 320 Loghub EventIds with their text only; a
`list_patterns` row adds the 16-hex `template_id`, the Log Entry count and first and last seen, at
about 2 characters per token. Reading BGL therefore goes by Component (`component='KERNEL'`, four
pages) or starts from `search_patterns`.

## SereneDB versions

Measured on 26.10.1 only. The step avoids temporary views (a temporary table carries the Tier 1
template texts), and uses `CREATE OR REPLACE MACRO` in the schema, `regexp_full_match`,
`regexp_replace`, `md5`, `UPDATE ... FROM` and window functions. It has not been run on 26.09.1.3
(arm64); `SLM_REQUIRE_DB=1 make test-db` there would confirm it.
