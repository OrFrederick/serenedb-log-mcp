"""Throwaway research loader. NOT the organiser-supplied production loader.

Usage: python load_corpus.py --parser /path/to/corpus.py --data /tmp/serenedb-data
Set DATABASE_URL for your research instance. Input archives stream without extraction.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
import psycopg

COLS = ['dataset','entry_id','ts','node','component','level','label','message',
        'source_file','request_id','instance_id','latency_s','rack','midplane',
        'epoch_s','endpoint','http_status','create_seconds','create_operation']
DDL = '''CREATE TABLE research_log_entries (
dataset VARCHAR, entry_id BIGINT, ts TIMESTAMP, node VARCHAR, component VARCHAR,
level VARCHAR, label VARCHAR, message VARCHAR, source_file VARCHAR,
request_id VARCHAR, instance_id VARCHAR, latency_s DOUBLE, rack VARCHAR,
midplane VARCHAR, epoch_s BIGINT, endpoint VARCHAR, http_status INTEGER,
create_seconds DOUBLE, create_operation VARCHAR)'''

def copy_field(value):
    """PostgreSQL COPY text, including NUL that psycopg.write_row rejects.

    SereneDB 26.10.1 accepts escaped NUL and returns it byte-exactly. Do not
    delete corrupted input bytes or turn them into invented message text.
    """
    if value is None:return r'\N'
    return str(value).replace('\\','\\\\').replace('\x00',r'\000').replace('\t',r'\t').replace('\n',r'\n').replace('\r',r'\r')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--parser',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=Path(__file__).with_name('load_results.json'))
    ap.add_argument('--resume-bgl',action='store_true',help='Preserve already committed OpenStack import after failed BGL transaction')
    args=ap.parse_args()
    spec=importlib.util.spec_from_file_location('corpus',args.parser)
    parser=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    result={'parser_sha256':hashlib.sha256(args.parser.read_bytes()).hexdigest(),
            'schema':DDL,'time_basis':'BGL printed naive timestamp; separate epoch_s retained; OpenStack printed naive timestamp',
            'loader':'throwaway research loader; official loader not supplied','inputs':{}}
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as c:
        result['version']=c.execute('SELECT version()').fetchone()[0]
        c.execute('SET threads=4')
        if args.resume_bgl:
            previous=json.loads(args.output.read_text())
            result['inputs']=previous['inputs']
            result['prior_failures']=['BGL line 1579029 has empty message: initial strict parser rejected it; failed COPY rolled back.',
              'psycopg.write_row rejects embedded NUL; SereneDB raw COPY accepts it. Use escaped raw COPY preserving original bytes.']
        else:
            c.execute(DDL)
        for filename,reader in [('OpenStack.tar.gz',parser.openstack_rows),('BGL.zip',parser.bgl_rows)]:
            if args.resume_bgl and filename=='OpenStack.tar.gz':continue
            p=args.data/filename
            started=time.perf_counter()
            n=0
            buffer=bytearray()
            nul_rows=0
            with c.cursor().copy('COPY research_log_entries ('+','.join(COLS)+') FROM STDIN') as cp:
                for row in reader(p):
                    values=[row[k] for k in COLS]
                    nul_rows+=any(isinstance(v,str) and '\x00' in v for v in values)
                    buffer.extend(('\t'.join(copy_field(v) for v in values)+'\n').encode('utf-8'))
                    if len(buffer)>=131072:
                        cp.write(buffer)
                        buffer.clear()
                    n+=1
                    if n%500000==0: print(f'{filename}: {n}',flush=True)
                if buffer:cp.write(buffer)
            elapsed=time.perf_counter()-started
            result['inputs'][filename]={'sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest(),'rows':n,'load_s':elapsed,'rows_with_embedded_nul':nul_rows}
            args.output.write_text(json.dumps(result,indent=2))
            print(filename,n,elapsed,flush=True)
        result['verification_sql']='SELECT dataset,count(*) FROM research_log_entries GROUP BY dataset ORDER BY dataset'
        result['verification_rows']=c.execute(result['verification_sql']).fetchall()
    args.output.write_text(json.dumps(result,indent=2))

if __name__=='__main__':main()
