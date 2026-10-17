# Independent incident-model and corpus experiments

Research identity: **ugnmura's Codex**. Issue #5, branch `research/5-ugnmura`.
No teammate Research comments were read during this independent phase.

`comment.md` is the protocol-format research response. `corpus.py` is a
throwaway streaming parser, not the organiser's supplied loader. It retains
every physical raw line, including empty message bodies and malformed lines.
Only explicit OpenStack `[instance: UUID]` tags become `instance_id`.

## Reproduce independent profiles

Download the two raw archives linked in `docs/challenge-brief.md` into an
external data directory. Do not use the 2k samples or substitute Loghub-2.0
annotated CSVs: their row populations differ. No extraction is needed.

```sh
python3 research/5/ugnmura/profile.py bgl /path/to/BGL.zip research/5/ugnmura/bgl-profile.json
python3 research/5/ugnmura/profile.py openstack /path/to/OpenStack.tar.gz research/5/ugnmura/openstack-profile.json
```

Profiles use Python's standard library. Quantiles linearly interpolate the
sorted values at rank `(n-1)*p`. BGL category grouping uses the raw integer
epoch field, sorts by `(epoch_s, entry_id)`, and begins a new group for a gap
**strictly greater** than the named threshold. Separate experiments check the
paper's strict-less-than suppression rule and naive detailed display times.
Naive display timestamps are not converted to the host machine's timezone.

## Reproduce SereneDB confirmation

Start SereneDB and import both raw corpora using the schema and loader on
branch `research/2-ugnmura`, under `research/2/ugnmura/`. That research loader
records archive and parser SHA-256 hashes, version and physical-line totals.
It uses raw COPY escaping to preserve embedded NUL bytes. The final organiser
loader must replace it when supplied.

Set `DATABASE_URL` to your own local research instance. Then:

```sh
uv run --with 'psycopg[binary]' python research/5/ugnmura/verify_serenedb.py research/5/ugnmura/serenedb-openstack-results.json openstack
uv run --with 'psycopg[binary]' python research/5/ugnmura/verify_serenedb.py research/5/ugnmura/serenedb-bgl-results.json bgl
```

Each JSON Evidence record retains the query, parameters, columns, returned
rows, wall-clock seconds and any error. Group-list queries have an explicit
LIMIT: a full page is flagged truncated and total output-group count remains
unknown there; the separate gap-count query gives the full group totals.
The SQL was checked against the SereneDB docs MCP, then actually executed.

`validate_results.py` cross-checks the two independent computations. It does
not compare a purported Incident count to ground truth: no full Incident
ground truth exists in these raw archives.

## Interpretation constraints

- Labelled BGL Log Entries are not distinct Incidents. Gap groups preserve
  members and parameters and remain candidates.
- Published filtered-alert totals are not a verified Incident ledger. The
  stated 5-second paper calculation did not reproduce on this archive.
- Four OpenStack UUIDs are verified *benchmark-anomalous instance executions*.
  All four explicitly tagged sessions contain INFO lines, but the annotations
  do not identify four independent causes or outages.
- OpenStack HTTP response durations and VM build durations have separate
  fields, populations and denominators.
- No Team is assigned until the organiser Ownership Table is supplied.

The ranked candidates and three Role storylines are in `comment.md`. `serenedb-fulltext-results.json` retains actual inverted-index phrase queries, counts and selected raw lines. These copied shared experiments were developed jointly with issue #1 and are included here for reproducibility.
