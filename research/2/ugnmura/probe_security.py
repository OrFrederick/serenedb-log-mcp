"""Negative privilege checks and an honest documented-timeout reproduction.

Run after probe_sql.py, whose intended timeout assertion exposes the mismatch.
This follow-up does not turn a failed timeout test into a passing claim.
"""
import json,os,time
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

results=[]
base=psycopg.conninfo.conninfo_to_dict(os.environ['DATABASE_URL'])
for user,password in [('research_reader','research-only-reader'),('research_manager','research-only-manager')]:
    with psycopg.connect(**dict(base,user=user,password=password),autocommit=True,row_factory=dict_row) as c:
        tests=[('set_timeout',"SET statement_timeout='100ms'"),('show_timeout','SHOW statement_timeout'),
          ('sleep_timeout_probe','SELECT pg_sleep(0.3)'),
          ('read_view','SELECT * FROM research_manager_counts ORDER BY log_entry_count DESC LIMIT 3')
             if user=='research_manager' else ('read_base','SELECT count(*) AS log_entries FROM research_log_entries'),
          ('base_denied','SELECT message FROM research_log_entries LIMIT 1')
             if user=='research_manager' else ('write_denied',"INSERT INTO research_log_entries(dataset) VALUES ('SHOULD_NOT_WRITE')")]
        for name,sql in tests:
            t=time.perf_counter();r={'user':user,'name':name,'sql':sql,'parameters':[]}
            try:
                cur=c.execute(sql);r.update(status='ok',rows=cur.fetchall() if cur.description else [])
            except psycopg.Error as exc:r.update(status='error',error=str(exc),sqlstate=exc.sqlstate)
            r['query_ms']=(time.perf_counter()-t)*1000;results.append(r)
            if name in ['base_denied','write_denied']:assert r.get('sqlstate')=='42501'
            elif name!='sleep_timeout_probe':assert r['status']=='ok'
Path(__file__).with_name('security-results.json').write_text(json.dumps(results,indent=2,default=str))
print(json.dumps([x for x in results if x['name'] in ['sleep_timeout_probe','base_denied','write_denied']],indent=2))
