--x2 P0 baseline: fetch 1M Body values, count distinct (no masking)
select count(*), count(distinct Body) from (select Body from read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') limit 1000000);;
--x2 P1 mask 1M rows per row with generic g10
select count(*), count(distinct r6d_mask_g10(Body)) from (select Body from read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') limit 1000000);;
--x2 P2 mask 1M rows distinct-first
select count(distinct r6d_mask_g10(b)) from (select distinct Body b from (select Body from read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') limit 1000000));;
