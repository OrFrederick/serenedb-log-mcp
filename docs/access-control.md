# Access control per Role

This page covers T22 (issue #34, award track 4: "row-level security per Role"). It describes what the
database enforces for each Role, how to switch it on, and where it stops. The short version is that
the Role-scoped tools are the guarantee and the database grants are **defence in depth**. SereneDB
has no row-level security, and any login can still read server files.

## What each Role's login may read

`make access` runs [`sql/access/roles.sql`](../sql/access/roles.sql). It gives each Role a schema
of its own, `<SLM_SCHEMA>_<role>`, which holds one view of every derived table under the canonical
name. It also creates a LOGIN role per Role, which may read that schema and nothing else.
SereneDB evaluates a view with its owner's privileges, so no login needs a grant on the derived
schema itself.

| Role | Its views (in `slm_<role>`) | Full-text index | Cannot read |
|---|---|---|---|
| Engineer | every column of `bgl_entry`, `os_entry`, `ownership`, `pattern`, `pattern_dict`, `os_instance`, `incident` | its own, over its entry views | `slm.*`, the raw loader tables, the Team Lead's and the Manager's views |
| Team Lead | the same columns as the Engineer; the cap of 3 sample Log Entries stays in the Role shaping | its own, over its entry views | `slm.*`, the raw loader tables, the Engineer's and the Manager's views |
| Manager | every column **except `message`** (and `pattern_dict` without `message`) | none | Log Entry text in any form, `slm.*`, the raw loader tables, the other Roles' views |

The Manager decides from measured frequency and affected Components (#4), so its login cannot read
Log Entry text at all. Pattern texts stay visible because they name the problems the Manager ranks.
Note that for a message with no variable parts, the Pattern text is the message itself.

## Switching it on

1. Build the derived tables: `make derive`.
2. Set one connection string per Role. Its `user=` names the LOGIN role that `make access` creates,
   and its `password=` sets that role's password. The user must be a dedicated role named `slm_...`,
   because the script drops and re-creates it.

   ```sh
   export DATABASE_URL="host=localhost port=7890 dbname=postgres user=postgres"   # creates the roles
   export DATABASE_URL_ENGINEER="host=localhost port=7890 dbname=postgres user=slm_engineer password=..."
   export DATABASE_URL_TEAM_LEAD="host=localhost port=7890 dbname=postgres user=slm_team_lead password=..."
   export DATABASE_URL_MANAGER="host=localhost port=7890 dbname=postgres user=slm_manager password=..."
   ```

3. Run `make access`. It drops the views and roles of an earlier run, then runs `roles.sql`. The
   two indexes over each text Role's entry views take about 4 s on the full Base Corpus. It then
   connects as each login and prints what it may and may not read (`make access-check` repeats
   only that check). `make access-drop` removes everything again.
4. Start the server with the same environment. A Role whose `DATABASE_URL_<ROLE>` is set connects
   as its own login (`db.get_database`), and `corpus.datasets.relation` gives it that Role's views.
   No tool changes. With `--role manager`, a registration answers only as the Manager, through the
   Manager's login (T3's pin).

`make derive` drops the per-Role views before it rebuilds the tables, so run `make access` again
after every derive. A view blocks `CREATE OR REPLACE TABLE` on its source
(`DependentObjectsStillExist`). Also, an index over a view is a snapshot taken when it is built, so
it would be stale after a derive.

## What changes for the tools

The tools behave the same for the Engineer and the Team Lead. For the Manager, everything that
counts still works and gives the same numbers as the Engineer: `describe_corpus`, `count_by`,
`trend`, `latency_stats`, `get_evidence` and `get_role_brief`. The calls that need Log Entry text
are refused by the resolver before any statement would read it, with a message that names the
alternative:

- `search_logs`;
- `get_context`;
- `count_by` or `trend` with `query`.

Without per-Role logins, the T7 shaping returns these calls to the Manager with totals and facets
but no rows.

`tests/db/access/test_access_db.py` shows all of this on a real SereneDB. It runs every registered
tool as each Role through that Role's login, and it fails when a new tool has no call there.

## Limits

- **No row-level security.** The SereneDB docs list it as not supported. Views separate columns and
  relations, not rows: every Role sees every Log Entry of both Datasets, as count-only rows for the
  Manager.
- **Any login can read server files.** This includes the server's own storage under
  `/var/lib/serenedb`, which bypasses every grant. A login can also fetch remote data. For example,
  `read_csv('/etc/passwd')`, `read_text('/etc/hostname')` and `glob('/var/lib/serenedb/**')` all
  work for the Manager login (measured on 26.10.1, see below).
- **Any login can change some server-wide settings.** A Manager login's `SET
  enable_external_access=false` turned off file access for the whole server until restart (measured
  on 26.09.1.3 in #4). This was not repeated on the shared 26.10.1 instance, because it cannot be
  undone while the server runs.
- **View definitions are public.** `pg_views` shows each view's SQL to every login.
- **Passwords need password authentication.** The dev container runs with
  `SERENEDB_HOST_AUTH_METHOD=trust`, so it accepts any password for any role, and anyone who can
  reach the port can log in as any Role. `make access` reports this. In #4, a container started with
  a password checked them over TCP on 26.09.1.3.
- **Node names stay visible to the Manager.** `count_by` and `trend` count distinct sources on the
  node column, and Spread needs it too. The Manager playbook and the Role shaping keep node names
  out of the Manager's answer, not the grants.
- **Agent indexes are not per Role.** `create_index` (T19) builds its partial index in the
  canonical schema with `DATABASE_URL`, and a Role's login cannot read it, so a Role connected
  through its own login searches with its own full index (same counts, without the speed-up). The
  Incident table and the Pattern catalogue index are covered by `roles.sql`. A table or index that
  a later change adds needs its per-Role view there too; the test above fails for a new tool until
  its call is added.
- **The tools are the guarantee.** No tool accepts SQL, every statement is a fixed template, and
  the Role shaping (T7) withholds raw Log Entries from the Manager whether or not per-Role logins
  are configured.

## Measured on SereneDB 26.10.1 (2026-10-17, shared dev instance)

As a LOGIN role with `SELECT` on one view and `USAGE` on its schema:

| Attempt | Result |
|---|---|
| its own view | allowed |
| `slm.bgl_entry`, `slm.bgl_entry_idx`, the raw `public.bgl` | `permission denied for table …` |
| another Role's view, or the index over it | `permission denied for view …` |
| `message` in the Manager's view | `Referenced column "message" not found` |
| `CREATE TABLE` (public, own schema), `CREATE TEMP TABLE` | `permission denied for schema …` |
| `SET ROLE postgres` | `permission denied to set role "postgres"` |
| `ATTACH ':memory:'` | `permission denied to create database` |
| `COPY (SELECT 1) TO '/tmp/…'` | `permission denied to COPY to a file` |
| `pg_authid` | `permission denied` |
| `read_csv('/etc/passwd')`, `read_text('/etc/hostname')`, `glob('/var/lib/serenedb/**')` | **allowed** |
| `pg_views.definition` of its view | **allowed** |
| connecting with a wrong password (trust auth) | **allowed** |

In #4, `COPY … TO` and `ATTACH` succeeded on 26.09.1.3, so check them again on any other version.

On the full Base Corpus in `slm`, with the logins `slm_engineer`, `slm_team_lead` and
`slm_manager`:

- `make access` took 14.1 s, and all 18 of its checks passed.
- Through its own login, each Role's `count_by(group_by='alert_category', dataset='bgl')` returned
  the same rows, for example 4,399,503 non-alert Log Entries.
- `search_logs(query='ciod error')` found 180,223 matches through the Engineer's index and through
  the Team Lead's. That is the canonical index's total.
- The Manager's `describe_corpus`, `trend` (KERNDTLB per week) and `latency_stats(metric='spawn')`
  worked.
- The Manager's `search_logs`, `get_context` and `count_by(query=...)` were refused with the message
  above.

The roles and views were then dropped again (`make access-drop`), so that the views would not block
`make derive` on the shared instance before this change merged.

Other things measured while building this:

- A view over the canonical index relation keeps `@@`, phrases, fuzzy matching and `ts_highlight`,
  but it loses `BM25(e.tableoid)` ("does not have a column named tableoid"). It also loses the
  `::score(NULL)` TSQUERY filters on other columns ("TSQUERY expression evaluated outside an `@@`
  match"). That is why each text Role gets an inverted index over its own entry view: 3.1 s for the
  4,747,963 BGL Log Entries and 0.5 s for OpenStack, with the same totals as the canonical index.
- `GRANT SELECT ON a, b TO r` is a syntax error, so the SQL grants one object per statement.
- A view blocks `CREATE OR REPLACE TABLE` on its source, but not `UPDATE`, `VACUUM` or
  `DROP INDEX`.
