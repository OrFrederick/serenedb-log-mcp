-- Pattern vectors for find_patterns (T26, #7 Decision proposal): one row per Pattern and embedding
-- model, the template text embedded locally with fastembed by scripts/embed_patterns.py
-- (`make embed-patterns`, optional). SQL cannot compute the vectors, so this step only makes sure
-- the table exists: empty on a fresh schema, kept as it is on a re-run. template_id is a content
-- hash of the template text, so vectors stay valid across derives; embed_patterns.py replaces the
-- rows of a model wholesale, and find_patterns joins them to the catalogue, so a vector whose
-- Pattern is gone is never shown. The per-Role views (sql/access/roles.sql) need the table to exist.
-- No vector index: the catalogue holds a few hundred Patterns, and #7 measured an exact scan at
-- 13.8 ms against 3.1 ms with IVF, both negligible next to the agent.
CREATE TABLE IF NOT EXISTS ${slm}.pattern_vector (
    dataset     VARCHAR,
    template_id VARCHAR,
    model       VARCHAR,
    emb         FLOAT[384]
);
