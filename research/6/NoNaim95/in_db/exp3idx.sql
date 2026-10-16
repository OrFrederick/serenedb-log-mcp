create text search dictionary r6d_tx as split_text(case := 'lower') | normalize_tokens('en_US.UTF-8', accent := false) with (frequency, position);;
-- build: keyword template_id + tokenized message, on full-width table with template_id
create index r6d_bgl_idx on r6d_bgl_full2 using inverted (line_no, ts, component, template_id, message r6d_tx);;
vacuum (refresh_table) r6d_bgl_full2;;
select count(*) from r6d_bgl_idx;;
--x5 I1 facet: count per template from the index (term-dictionary rewrite?)
select template_id, count(*) n from r6d_bgl_idx group by template_id order by n desc limit 3;;
explain select template_id, count(*) n from r6d_bgl_idx group by template_id;;
--x5 I2 search + group by template (index): message @@ 'failed'
select template_id, count(*) n from r6d_bgl_idx where message @@ 'failed' group by template_id order by n desc limit 3;;
--x5 I2b same on base table with ILIKE
select template_id, count(*) n from r6d_bgl_full2 where message ilike '%failed%' group by template_id order by n desc limit 3;;
--x5 I3 ts_dict facet over search hits
select unnest(ts_dict_agg(template_id)) t, unnest(ts_dict_count(template_id)) c from r6d_bgl_idx where message @@ 'failed';;
--x5 I4 count lines of one template via index keyword match
select count(*) from r6d_bgl_idx where template_id = '6265c7394fe834d8';;
--x5 I4b same on base table
select count(*) from r6d_bgl_full2 where template_id = '6265c7394fe834d8';;
