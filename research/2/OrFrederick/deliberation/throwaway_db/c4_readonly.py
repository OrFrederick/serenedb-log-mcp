"""Claim 4: read-only enforcement."""
import psycopg
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"


def run(c, s, label=""):
    try:
        cur = c.execute(s)
        r = cur.fetchall() if cur.description else cur.statusmessage
        print(f"  OK  {label}{s[:100]} -> {str(r)[:200]}", flush=True)
    except Exception as e:
        print(f"  ERR {label}{s[:100]} -> {type(e).__name__}: {str(e)[:250]}", flush=True)
        if not c.autocommit:
            c.rollback()


if __name__ == "__main__":
  with psycopg.connect(DSN, autocommit=True) as c:
      run(c, "DROP TABLE IF EXISTS scratch_ro")
      run(c, "CREATE TABLE scratch_ro (x INT)")

  print("(a) SET default_transaction_read_only = on, then INSERT (autocommit session)")
  with psycopg.connect(DSN, autocommit=True) as a:
      run(a, "SHOW default_transaction_read_only")
      run(a, "SET default_transaction_read_only = on")
      run(a, "SHOW default_transaction_read_only")
      run(a, "INSERT INTO scratch_ro VALUES (1)")
      run(a, "CREATE TABLE scratch_ro2 (x INT)")
      run(a, "SHOW transaction_read_only")
      print("(c) new session while session A still open:")
      with psycopg.connect(DSN, autocommit=True) as b:
          run(b, "SHOW default_transaction_read_only", "[B] ")
          run(b, "INSERT INTO scratch_ro VALUES (2)", "[B] ")
      run(a, "RESET default_transaction_read_only")
  print("(c) new session after A closed + RESET:")
  with psycopg.connect(DSN, autocommit=True) as b:
      run(b, "SHOW default_transaction_read_only", "[B2] ")

  print("(b) conn.read_only = True (BEGIN READ ONLY)")
  with psycopg.connect(DSN) as r:
      r.read_only = True
      run(r, "SHOW transaction_read_only")
      run(r, "INSERT INTO scratch_ro VALUES (3)")
      run(r, "CREATE TABLE scratch_ro3 (x INT)")
      run(r, "UPDATE scratch_ro SET x = 9")
      run(r, "DELETE FROM scratch_ro")
      run(r, "SELECT count(*) FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed')")
      run(r, "SELECT line_no, BM25(bgl_idx.tableoid) s FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed') ORDER BY s DESC, line_no LIMIT 3")
      run(r, "SELECT level, count(*) FROM bgl GROUP BY level ORDER BY 2 DESC")
      run(r, "SELECT time_bucket(INTERVAL '1 month', ts) b, count(*) FROM bgl GROUP BY b ORDER BY b LIMIT 3")
      r.commit()

  if __name__ == "__main__":
    with psycopg.connect(DSN, autocommit=True) as c:
      run(c, "SELECT * FROM scratch_ro ORDER BY x")
      run(c, "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'scratch_ro%'")
