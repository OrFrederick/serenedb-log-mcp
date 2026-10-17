"""Execute/record research SQL on the same raw-line corpus loaded by parent.

Usage: DATABASE_URL=... python verify_serenedb.py OUTPUT [openstack|bgl|all]
Docs read via serenedb-docs MCP: /sql/functions/window_functions,
/sql/functions/timestamp. All timestamps are source display times, no UTC claim.
"""
import json,os,re,sys,time
from pathlib import Path
import psycopg

IDS=['544fd51c-4edc-4780-baae-ba1d80a0acfc','ae651dff-c7ad-43d6-ac96-bbcd820ccca8','a445709b-6ad0-40ec-8860-bec60b6ca0c2','1643649d-2f42-4303-bfcd-7798baec19f9']

def queries(mode):
    if mode=='fulltext':
        yield 'fulltext_build_count', """SELECT count(*) AS matching_entries FROM research_logs_idx
            WHERE dataset=%s AND message @@ ts_phrase(%s)""", ['OpenStack','build instance']
        yield 'fulltext_abnormal_build_count', """SELECT count(*) AS matching_entries FROM research_logs_idx
            WHERE dataset=%s AND source_file=%s AND ts>=TIMESTAMP '2017-05-14 00:00:00'
            AND ts<TIMESTAMP '2017-05-15 00:00:00' AND message @@ ts_phrase(%s)""", ['OpenStack','openstack_abnormal.log','build instance']
        yield 'fulltext_selected_build_count', """SELECT count(*) AS matching_entries FROM research_logs_idx
            WHERE dataset=%s AND source_file=%s AND instance_id=%s
            AND ts>=TIMESTAMP '2017-05-14 21:42:00' AND ts<TIMESTAMP '2017-05-14 21:44:00'
            AND message @@ ts_phrase(%s)""", ['OpenStack','openstack_abnormal.log','a445709b-6ad0-40ec-8860-bec60b6ca0c2','build instance']
        yield 'fulltext_selected_build_sample', """SELECT source_file,entry_id,ts,instance_id,component,level,message
            FROM research_logs_idx WHERE dataset=%s AND source_file=%s AND instance_id=%s
            AND ts>=TIMESTAMP '2017-05-14 21:42:00' AND ts<TIMESTAMP '2017-05-14 21:44:00'
            AND message @@ ts_phrase(%s) ORDER BY ts,entry_id LIMIT 10""", ['OpenStack','openstack_abnormal.log','a445709b-6ad0-40ec-8860-bec60b6ca0c2','build instance']
        yield 'fulltext_bgl_tlb_count', """SELECT count(*) AS matching_entries FROM research_logs_idx
            WHERE dataset=%s AND message @@ ts_phrase(%s)""", ['BGL','data TLB error interrupt']
    if mode in ['all','openstack']:
        yield 'openstack_file_profile', """SELECT source_file, count(*) AS entries, count(ts) AS parsed_timestamps,
            min(ts) AS first_display_ts, max(ts) AS last_display_ts,
            count(DISTINCT instance_id) AS explicit_instances,
            count(DISTINCT request_id) AS request_ids
            FROM research_log_entries WHERE dataset='OpenStack' GROUP BY source_file ORDER BY source_file""", []
        yield 'openstack_levels', """SELECT source_file,level,count(*) AS entries FROM research_log_entries
            WHERE dataset='OpenStack' GROUP BY source_file,level ORDER BY source_file,level""", []
        yield 'labelled_instance_sessions', """SELECT instance_id,count(*) AS explicit_tag_entries,
            min(ts) AS first_display_ts,max(ts) AS last_display_ts,count(DISTINCT component) AS components,
            min(level) AS min_level,max(level) AS max_level
            FROM research_log_entries WHERE dataset='OpenStack' AND instance_id IN (%s,%s,%s,%s)
            GROUP BY instance_id ORDER BY instance_id""", IDS
        yield 'labelled_instance_context', """SELECT source_file,entry_id,ts,instance_id,component,level,message,request_id
            FROM research_log_entries WHERE dataset='OpenStack' AND instance_id IN (%s,%s,%s,%s)
            ORDER BY instance_id,ts,entry_id""", IDS
        yield 'build_rows', """SELECT source_file,entry_id,ts,instance_id,create_seconds,level
            FROM research_log_entries WHERE dataset='OpenStack' AND create_operation=%s
            ORDER BY create_seconds DESC,source_file,entry_id""", ['build instance.']
        yield 'http_endpoint_profile', """SELECT source_file,endpoint,http_status,count(*) AS entries,
            min(latency_s) AS min_s,max(latency_s) AS max_s,avg(latency_s) AS mean_s
            FROM research_log_entries WHERE dataset='OpenStack' AND latency_s IS NOT NULL
            GROUP BY source_file,endpoint,http_status ORDER BY source_file,endpoint,http_status""", []
        for threshold in [22,23,24,25,27.5,30,35,40,50,60]:
            yield f'build_threshold_{threshold}', """SELECT
                count(CASE WHEN instance_id IN (%s,%s,%s,%s) THEN 1 ELSE NULL END) AS labelled,
                count(CASE WHEN instance_id IN (%s,%s,%s,%s) THEN NULL ELSE 1 END) AS unlabelled
                FROM research_log_entries WHERE dataset='OpenStack'
                AND create_operation=%s AND create_seconds>%s""", [*IDS,*IDS,'build instance.',threshold]
    if mode in ['all','bgl']:
        yield 'whole_base_corpus', """SELECT dataset,count(*) AS entries,count(ts) AS parsed_timestamps,
            min(ts) AS first_display_ts,max(ts) AS last_display_ts FROM research_log_entries GROUP BY dataset ORDER BY dataset""", []
        yield 'bgl_labels', """SELECT label,count(*) AS entries,count(DISTINCT node) AS nodes,
            count(DISTINCT rack) AS racks,count(DISTINCT midplane) AS midplanes
            FROM research_log_entries WHERE dataset='BGL' GROUP BY label ORDER BY entries DESC""", []
        yield 'bgl_levels_components', """SELECT level,component,count(*) AS entries FROM research_log_entries
            WHERE dataset='BGL' GROUP BY level,component ORDER BY entries DESC""", []
        yield 'bgl_weekly', """SELECT time_bucket(INTERVAL '7 days',ts) AS week,label,count(*) AS entries,
            count(DISTINCT node) AS nodes FROM research_log_entries WHERE dataset='BGL'
            GROUP BY week,label ORDER BY week,label""", []
        yield 'bgl_adjacent_category_transitions', """WITH previous AS (
            SELECT node,label,epoch_s,lag(label) OVER(PARTITION BY node ORDER BY epoch_s,entry_id) AS previous_label,
            lag(epoch_s) OVER(PARTITION BY node ORDER BY epoch_s,entry_id) AS previous_epoch
            FROM research_log_entries WHERE dataset='BGL' AND label<>'-')
            SELECT previous_label,label,count(*) AS adjacent_pairs,count(DISTINCT node) AS nodes
            FROM previous WHERE label<>previous_label AND epoch_s-previous_epoch<=%s
            GROUP BY previous_label,label ORDER BY adjacent_pairs DESC LIMIT 20""", [300]
        yield 'bgl_recent_vs_baseline', """SELECT label,min(ts) AS first_observed,
            sum(CASE WHEN ts>=TIMESTAMP '2005-12-28 08:00:05.233639' THEN 1 ELSE 0 END) AS recent_7d,
            sum(CASE WHEN ts>=TIMESTAMP '2005-11-30 08:00:05.233639' AND ts<TIMESTAMP '2005-12-28 08:00:05.233639' THEN 1 ELSE 0 END) AS prior_28d
            FROM research_log_entries WHERE dataset='BGL' AND label<>'-'
            GROUP BY label ORDER BY recent_7d DESC""", []
        yield 'bgl_candidate_minute_intensity', """SELECT label,time_bucket(INTERVAL '1 minute',ts) AS minute,
            count(*) AS entries,count(DISTINCT node) AS nodes
            FROM research_log_entries WHERE dataset='BGL' AND
            ((label='KERNDTLB' AND epoch_s BETWEEN %s AND %s) OR
             (label='KERNSTOR' AND epoch_s BETWEEN %s AND %s))
            GROUP BY label,minute ORDER BY label,minute""", [1118535464,1118558372,1118767675,1118768209]
        yield 'bgl_paper_boundary_strict_5', """WITH previous AS (
            SELECT label,epoch_s,lag(epoch_s) OVER(PARTITION BY label ORDER BY epoch_s,entry_id) AS prev
            FROM research_log_entries WHERE dataset='BGL' AND label<>'-')
            SELECT label,sum(CASE WHEN prev IS NULL OR epoch_s-prev>=%s THEN 1 ELSE 0 END) AS temporal_groups
            FROM previous GROUP BY label ORDER BY label""", [5]
        for gap in [1,5,30,60,300,900,3600]:
            for spatial in ['category','node_category']:
                partition='label' if spatial=='category' else 'label,node'
                yield f'bgl_gap_{gap}_{spatial}', f"""WITH previous AS (
                    SELECT label,epoch_s,lag(epoch_s) OVER(PARTITION BY {partition} ORDER BY epoch_s,entry_id) AS prev
                    FROM research_log_entries WHERE dataset='BGL' AND label<>'-')
                    SELECT label,count(*) AS entries,
                    sum(CASE WHEN prev IS NULL OR epoch_s-prev>%s THEN 1 ELSE 0 END) AS temporal_groups
                    FROM previous GROUP BY label ORDER BY entries DESC""", [gap]
        for gap in [5,300]:
            yield f'bgl_top_groups_{gap}', """WITH previous AS (
                SELECT *,lag(epoch_s) OVER(PARTITION BY label ORDER BY epoch_s,entry_id) AS prev
                FROM research_log_entries WHERE dataset='BGL' AND label<>'-'),
                marked AS (SELECT *,CASE WHEN prev IS NULL OR epoch_s-prev>%s THEN 1 ELSE 0 END AS new_group FROM previous),
                numbered AS (SELECT *,sum(new_group) OVER(PARTITION BY label ORDER BY epoch_s,entry_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS group_id FROM marked)
                SELECT label,group_id,min(epoch_s) AS first_epoch_s,max(epoch_s) AS last_epoch_s,
                min(ts) AS first_display_ts,max(ts) AS last_display_ts,count(*) AS entries,
                count(DISTINCT node) AS nodes,count(DISTINCT rack) AS racks,count(DISTINCT midplane) AS midplanes
                FROM numbered GROUP BY label,group_id ORDER BY entries DESC LIMIT 30""", [gap]

if __name__=='__main__':
    out=Path(sys.argv[1]); mode=sys.argv[2] if len(sys.argv)>2 else 'all'
    results=[]
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as conn:
        for name,sql,params in queries(mode):
            started=time.perf_counter()
            try:
                cur=conn.execute(sql,params)
                rows=cur.fetchall(); columns=[c.name for c in cur.description]
                limit=re.search(r'LIMIT (\d+)',sql)
                results.append(dict(name=name,sql=sql,params=params,seconds=time.perf_counter()-started,
                                    columns=columns,rows=rows,returned_count=len(rows),
                                    total_result_rows=None if limit else len(rows),
                                    row_limit=int(limit[1]) if limit else None,
                                    truncated=bool(limit and len(rows)==int(limit[1]))))
                print(name,len(rows),round(results[-1]['seconds'],3),flush=True)
            except Exception as exc:
                results.append(dict(name=name,sql=sql,params=params,error=str(exc),seconds=time.perf_counter()-started))
                print(name,'ERROR',str(exc),flush=True)
            out.write_text(json.dumps(results,indent=2,default=str)+'\n')
