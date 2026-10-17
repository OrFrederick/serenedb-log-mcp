"""Reproduce real BGL NUL import boundary without altering source text."""
import json,os
from pathlib import Path
import psycopg
from load_corpus import copy_field

results={}
with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as c:
    c.execute('CREATE TEMP TABLE research_nul_roundtrip(x VARCHAR)')
    try:
        with c.cursor().copy('COPY research_nul_roundtrip FROM STDIN') as cp:
            cp.write_row(('a\x00b',))
    except psycopg.DataError as exc:
        results['write_row']={'status':'rejected','error':str(exc)}
    else:
        raise AssertionError('Expected driver NUL rejection for pinned psycopg')
    values=['a\x00b',r'backslash\value','a\tb\nc\rd',None,'']
    with c.cursor().copy('COPY research_nul_roundtrip FROM STDIN') as cp:
        cp.write(('\n'.join(copy_field(x) for x in values)+'\n').encode())
    actual=[x[0] for x in c.execute('SELECT x FROM research_nul_roundtrip').fetchall()]
    assert actual==values,(actual,values)
    results['raw_copy']={'status':'exact roundtrip','inputs':values,'outputs':actual}
Path(__file__).with_name('nul-results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results))
