-- Run by r7 literature agent 2026-10-16 against SereneDB 26.10.1 (docker, port 7890), Ollama all-minilm on host.
-- 1) localhost from inside the container FAILS: "request to 'http://localhost:11434/v1/embeddings' failed: Could not connect to server"
CREATE SECRET r7lit_local (TYPE openai, base_url 'http://localhost:11434', embeddings_path '/v1/embeddings');
-- 2) host.docker.internal is refused until the session setting is on:
--    "secret 'r7lit_hdi' uses the insecure endpoint 'http://host.docker.internal:11434'"
CREATE SECRET r7lit_hdi (TYPE openai, base_url 'http://host.docker.internal:11434', embeddings_path '/v1/embeddings');
SET sdb_ai_allow_insecure_endpoint = true;   -- per session; secrets are instance-wide (visible from other connections)
SELECT array_length(ai_embed('memory error', 'all-minilm', 'r7lit_hdi'),1) AS dims;  -- 384, 1.1 s first call
-- 3) embed all 320 BGL templates (Loghub-2.0): 4.35 / 3.86 / 3.80 s (median 3.86 s), host load avg ~27 on 8 cores
CREATE TABLE r7lit_tpl AS SELECT event_id, any_value(event_template) AS tpl FROM lh2_bgl GROUP BY event_id;
CREATE TABLE r7lit_tplv AS SELECT event_id, tpl, ai_embed(tpl, 'all-minilm', 'r7lit_hdi')::FLOAT[384] AS emb FROM r7lit_tpl;
CREATE INDEX r7lit_tplv_idx ON r7lit_tplv USING inverted (event_id, tpl, emb ivf (metric = 'cosine'));
VACUUM (REFRESH_TABLE) r7lit_tplv;
-- 4) inline query embedding; EXPLAIN shows IRESEARCH_SCAN, Score: cosine_distance(emb, FLOAT[384]), Top: 5 (ai_embed constant-folded)
SELECT event_id, tpl FROM r7lit_tplv_idx
ORDER BY emb <=> ai_embed('RAM failure','all-minilm','r7lit_hdi')::FLOAT[384] LIMIT 6;   -- 0.07-0.15 s
-- cleanup
DROP INDEX r7lit_tplv_idx; DROP TABLE r7lit_tplv; DROP TABLE r7lit_tpl; DROP SECRET r7lit_local; DROP SECRET r7lit_hdi;
