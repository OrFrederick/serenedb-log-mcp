"""Classify distinct real messages, then verify raw-entry GROUP BY joins in SereneDB.

The map is a research acceleration, not the final per-entry production sidecar.
All input messages and multiplicities come from executed SereneDB GROUP BY.
Use strict catalogue assignment: ambiguous/empty/unmatched are diagnostics.
"""
import argparse,hashlib,json,os,time
from pathlib import Path
import psycopg
from catalogue_reader import Catalogue
from load_corpus import copy_field

HERE=Path(__file__).parent
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True);args=ap.parse_args()
    dsn=os.environ['DATABASE_URL'];started=time.perf_counter()
    query='''SELECT dataset,coalesce(component,'') AS component,message,count(*) AS entries
      FROM research_log_entries GROUP BY dataset,component,message'''
    models={d:Catalogue(args.data/(d+'-annotated.zip'),d) for d in ['BGL','OpenStack']}
    result={'input_sql':query,'catalogues':{d:m.version for d,m in models.items()},'ambiguity_policy':'strict; no tie breaking','mapping_rows':0,'queries':[]}
    with psycopg.connect(dsn,autocommit=True) as writer,psycopg.connect(dsn,autocommit=True) as reader:
        reader.execute("SET memory_limit='1GB'");reader.execute('SET threads=2')
        writer.execute('''CREATE TABLE research_template_map(dataset VARCHAR,component VARCHAR,message VARCHAR,
          template_id VARCHAR,parse_status VARCHAR,parser_version VARCHAR,template_text VARCHAR,source_entries BIGINT)''')
        buffer=bytearray()
        with writer.cursor().copy('COPY research_template_map FROM STDIN') as cp:
            for dataset,component,message,n in reader.cursor().stream(query):
                a=models[dataset].classify(message or '',component)
                row=[dataset,component,message,a.template_id,a.status,a.parser_version,a.template_text,n]
                buffer.extend(('\t'.join(copy_field(x) for x in row)+'\n').encode())
                if len(buffer)>=131072:cp.write(buffer);buffer.clear()
                result['mapping_rows']+=1
            if buffer:cp.write(buffer)
        tests={
          'assignment_coverage':'''SELECT dataset,parse_status,sum(source_entries) AS log_entry_count
            FROM research_template_map GROUP BY dataset,parse_status ORDER BY dataset,parse_status''',
          'duplicate_map_keys':'''SELECT count(*) AS duplicate_keys FROM (
            SELECT dataset,component,message,count(*) AS n FROM research_template_map
            GROUP BY dataset,component,message HAVING count(*)>1) t''',
          'top_patterns':'''SELECT l.dataset,l.component,p.template_id,p.parser_version,
            min(p.template_text) AS template_text,count(*) AS log_entry_count
            FROM research_log_entries l JOIN research_template_map p
            ON l.dataset=p.dataset AND coalesce(l.component,'')=p.component AND l.message=p.message
            WHERE p.parse_status='catalogue'
            GROUP BY l.dataset,l.component,p.template_id,p.parser_version
            ORDER BY log_entry_count DESC,l.dataset,l.component,p.template_id LIMIT 20''',
          'pattern_trend':'''SELECT l.dataset,time_bucket(INTERVAL '1 day',l.ts) AS bucket,
            p.parse_status,count(*) AS log_entry_count FROM research_log_entries l
            LEFT JOIN research_template_map p ON l.dataset=p.dataset
            AND coalesce(l.component,'')=p.component AND l.message=p.message
            GROUP BY l.dataset,bucket,p.parse_status ORDER BY l.dataset,bucket,p.parse_status''',
          'all_entry_join_count':'''SELECT count(*) AS joined_entries FROM research_log_entries l
            LEFT JOIN research_template_map p ON l.dataset=p.dataset
            AND coalesce(l.component,'')=p.component AND l.message=p.message'''}
        for name,sql in tests.items():
            t=time.perf_counter();cur=reader.execute(sql);rows=cur.fetchall()
            result['queries'].append({'name':name,'sql':sql,'parameters':[],'columns':[x.name for x in cur.description],'rows':rows,'query_ms':(time.perf_counter()-t)*1000})
            print(name,str(rows[:5]),flush=True)
        assert result['queries'][1]['rows'][0][0]==0
        assert result['queries'][-1]['rows'][0][0]==4955783
    result['wall_s']=time.perf_counter()-started
    (HERE/'pattern-sql-results.json').write_text(json.dumps(result,indent=2,default=str))

if __name__=='__main__':main()
