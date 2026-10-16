import sys, psycopg
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
for s in sys.argv[1:]:
    try:
        cur = c.execute(s)
        print("OK", s[:80]); 
        if cur.description: 
            for r in cur.fetchall()[:20]: print("  ", r)
    except Exception as e: print("ERR", s[:80], "->", str(e).splitlines()[0][:200])
