--x2 P3 baseline 10M: fetch Body, count distinct
select count(*), count(distinct Body) from (select Body from read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') limit 10000000);;
--x2 P4 mask 10M per row (g10)
select count(*), count(distinct r6d_mask_g10(Body)) from (select Body from read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') limit 10000000);;
--x2 P5 mask 10M distinct-first (g10)
select count(distinct r6d_mask_g10(b)) from (select distinct Body b from (select Body from read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') limit 10000000));;
--x1 P6 mask 100M distinct-first (g10)
select count(distinct r6d_mask_g10(b)) from (select distinct Body b from (select Body from read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') limit 100000000));;
