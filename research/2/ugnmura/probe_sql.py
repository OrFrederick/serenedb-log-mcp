"""Execute documented SQL against real Base Corpus; retain every query/result.

Research probe, not the product MCP implementation. Requires load_corpus.py first.
All search values are bound; dynamic identifiers in the proposed product must come
from a fixed server-side allowlist. Use DATABASE_URL; never log credentials.
"""
import json
import os
from pathlib import Path
import time
import psycopg
from psycopg.rows import dict_row

HERE=Path(__file__).parent
E=[]

def run(c,name,sql,params=()):
    start=time.perf_counter()
    item={'evidence_id':f'E{len(E)+1}','name':name,'sql':sql,'parameters':params}
    try:
        cur=c.execute(sql,params)
        item.update(rows=cur.fetchall() if cur.description else [],status='ok')
    except psycopg.Error as exc:
        item.update(status='error',sqlstate=exc.sqlstate,error=str(exc))
    item['query_ms']=(time.perf_counter()-start)*1000
    E.append(item)
    (HERE/'sql-results.json').write_text(json.dumps(E,indent=2,default=str))
    print(name,item['status'],str(item.get('rows',item.get('error')))[:230],flush=True)
    return item

def main():
    dsn=os.environ['DATABASE_URL']
    with psycopg.connect(dsn,autocommit=True,row_factory=dict_row) as c:
        c.execute('SET threads=2')
        c.execute("SET memory_limit='1GB'")
        run(c,'server','SELECT version() AS version')
        run(c,'describe_corpus', '''SELECT dataset,count(*) AS log_entries,
          count(ts) AS timestamped_entries,min(ts) AS first_printed_time,max(ts) AS last_printed_time,
          count(DISTINCT component) AS components,count(DISTINCT node) AS named_nodes
          FROM research_log_entries GROUP BY dataset ORDER BY dataset''')
        run(c,'dictionary', '''CREATE TEXT SEARCH DICTIONARY research_words AS
          split_text(case := 'lower') | normalize_tokens('en_US.UTF-8', accent := false)
          WITH (frequency, position, offset, norm)''')
        run(c,'create_index', '''CREATE INDEX research_logs_idx ON research_log_entries
          USING inverted (dataset,ts,component,node,label,level,message research_words)
          INCLUDE(entry_id,source_file,request_id,instance_id,message)''')
        run(c,'refresh','VACUUM (REFRESH_TABLE) research_log_entries')
        run(c,'search_build_count', 'SELECT count(*) AS total_matches FROM research_logs_idx WHERE dataset=%s AND message @@ ts_phrase(%s)',('OpenStack','build instance'))
        run(c,'search_build_page', '''SELECT dataset,source_file,entry_id,ts,component,instance_id,
          ts_highlight(message,'MaxWords=24') AS snippet FROM research_logs_idx
          WHERE dataset=%s AND message @@ ts_phrase(%s) ORDER BY source_file,entry_id LIMIT 5''',('OpenStack','build instance'))
        for mode,pred,value in [
          ('terms','message @@ %s','error'),
          ('phrase','message @@ ts_phrase(%s)','machine check'),
          ('fuzzy','message @@ ts_levenshtein(%s,1)','erorr')]:
            params=('BGL',value)
            scope='dataset=%s AND '+pred
            count=run(c,'search_'+mode+'_count','SELECT count(*) AS total_matches FROM research_logs_idx WHERE '+scope,params)
            page=run(c,'search_'+mode+'_page', '''SELECT dataset,source_file,entry_id,ts,component,node,
              ts_highlight(message,'MaxWords=24') AS snippet,BM25(research_logs_idx.tableoid) AS score
              FROM research_logs_idx WHERE '''+scope+'''
              ORDER BY BM25(research_logs_idx.tableoid) DESC,source_file,entry_id LIMIT 5''',params)
            if count['status']=='ok' and page['status']=='ok':
                assert count['rows'][0]['total_matches']>=len(page['rows'])
        run(c,'search_empty_count','SELECT count(*) AS total_matches FROM research_logs_idx WHERE message @@ %s',('qzzzzxnomatchingterm',))
        run(c,'search_empty_page','SELECT entry_id FROM research_logs_idx WHERE message @@ %s LIMIT 5',('qzzzzxnomatchingterm',))
        run(c,'injection_bound_literal','SELECT count(*) AS total_matches FROM research_log_entries WHERE component=%s',("x'; DROP TABLE research_log_entries; --",))
        run(c,'get_context', '''SELECT dataset,source_file,entry_id,ts,component,instance_id,message
          FROM research_log_entries WHERE dataset=%s AND source_file=%s AND instance_id=%s
          ORDER BY ts,entry_id LIMIT 30''',('OpenStack','openstack_abnormal.log','544fd51c-4edc-4780-baae-ba1d80a0acfc'))
        run(c,'count_by', '''SELECT dataset,component,count(*) AS log_entry_count
          FROM research_log_entries GROUP BY dataset,component
          ORDER BY log_entry_count DESC,dataset,component LIMIT 20''')
        run(c,'trend', '''SELECT time_bucket(INTERVAL '1 day',ts) AS bucket,count(*) AS log_entry_count
          FROM research_log_entries WHERE dataset=%s AND label<>%s
          GROUP BY bucket ORDER BY bucket''',('BGL','-'))
        run(c,'latency_stats_builds', '''SELECT source_file,count(*) AS timed_operations,
          quantile_cont(create_seconds,0.5) AS p50_s,quantile_cont(create_seconds,0.95) AS p95_s,
          quantile_cont(create_seconds,0.99) AS p99_s,max(create_seconds) AS max_s
          FROM research_log_entries WHERE dataset=%s AND create_operation=%s
          GROUP BY source_file ORDER BY source_file''',('OpenStack','build instance.'))
        run(c,'latency_stats_http', '''SELECT endpoint,count(*) AS timed_requests,
          quantile_cont(latency_s,0.5) AS p50_s,quantile_cont(latency_s,0.95) AS p95_s,
          quantile_cont(latency_s,0.99) AS p99_s,max(latency_s) AS max_s
          FROM research_log_entries WHERE dataset=%s AND latency_s IS NOT NULL
          GROUP BY endpoint ORDER BY timed_requests DESC LIMIT 20''',('OpenStack',))
        run(c,'compare_periods', '''SELECT
          count(*) FILTER(WHERE ts>=%s AND ts<%s) AS before_count,
          count(*) FILTER(WHERE ts>=%s AND ts<%s) AS after_count
          FROM research_log_entries WHERE dataset=%s AND label<>%s''',
          ('2005-06-03','2005-06-10','2005-06-10','2005-06-17','BGL','-'))
        run(c,'group_entries', '''WITH prior AS (
          SELECT label,node,epoch_s,entry_id,
            lag(epoch_s) OVER(PARTITION BY label,node ORDER BY epoch_s,entry_id) AS previous_s
          FROM research_log_entries WHERE dataset=%s AND label<>%s), marked AS (
          SELECT *, CASE WHEN previous_s IS NULL OR epoch_s-previous_s>%s THEN 1 ELSE 0 END AS new_group
          FROM prior), numbered AS (
          SELECT *,sum(new_group) OVER(PARTITION BY label,node ORDER BY epoch_s,entry_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS temporal_group FROM marked)
          SELECT label,node,temporal_group,count(*) AS log_entry_count,
          min(epoch_s) AS first_epoch_s,max(epoch_s) AS last_epoch_s
          FROM numbered GROUP BY label,node,temporal_group
          ORDER BY log_entry_count DESC,label,node,temporal_group LIMIT 10''',('BGL','-',5))
        run(c,'create_reader',"CREATE ROLE research_reader LOGIN PASSWORD 'research-only-reader'")
        run(c,'reader_select','GRANT SELECT ON research_log_entries TO research_reader')
        run(c,'create_manager',"CREATE ROLE research_manager LOGIN PASSWORD 'research-only-manager'")
        run(c,'create_manager_view', '''CREATE VIEW research_manager_counts AS SELECT dataset,component,
          count(*) AS log_entry_count FROM research_log_entries GROUP BY dataset,component''')
        run(c,'manager_select','GRANT SELECT ON research_manager_counts TO research_manager')
    # These disposable identities belong only to the loopback-bound research instance.
    base=psycopg.conninfo.conninfo_to_dict(dsn)
    for name,password in [('research_reader','research-only-reader'),('research_manager','research-only-manager')]:
        params=dict(base,user=name,password=password)
        with psycopg.connect(**params,autocommit=True,row_factory=dict_row) as c:
            run(c,name+'_read_only_setting',"SET default_transaction_read_only=on")
            if name=='research_reader':
                run(c,'reader_base_count','SELECT count(*) AS log_entries FROM research_log_entries')
                run(c,'reader_search','SELECT count(*) AS log_entries FROM research_logs_idx WHERE message @@ %s',('error',))
                result=run(c,'reader_write_denied',"INSERT INTO research_log_entries(dataset) VALUES ('SHOULD_NOT_WRITE')")
                assert result['status']=='error'
            else:
                run(c,'manager_view','SELECT * FROM research_manager_counts ORDER BY log_entry_count DESC LIMIT 3')
                result=run(c,'manager_base_denied','SELECT message FROM research_log_entries LIMIT 1')
                assert result['status']=='error'
            run(c,name+'_timeout_setting',"SET statement_timeout='100ms'")
            result=run(c,name+'_timeout','SELECT pg_sleep(5)')
            assert result.get('sqlstate')=='57014'
            run(c,name+'_after_timeout','SELECT 1 AS recovered')
    assert all(x['status']=='ok' for x in E if x['name'] not in
      ('reader_write_denied','manager_base_denied','research_reader_timeout','research_manager_timeout'))

if __name__=='__main__':main()
