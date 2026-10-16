"""Claim 4b: default_transaction_read_only inside explicit BEGIN; SET TRANSACTION READ ONLY; ALTER ROLE."""
import psycopg
from c4_readonly import run, DSN
print("SET default_transaction_read_only=on + explicit transaction (non-autocommit):")
with psycopg.connect(DSN) as a:
    run(a, "SET default_transaction_read_only = on"); a.commit()
    run(a, "INSERT INTO scratch_ro VALUES (10)"); a.commit()
print("BEGIN; SET TRANSACTION READ ONLY; INSERT:")
with psycopg.connect(DSN, autocommit=True) as a:
    run(a, "BEGIN"); run(a, "SET TRANSACTION READ ONLY"); run(a, "INSERT INTO scratch_ro VALUES (11)"); run(a, "ROLLBACK")
    run(a, "BEGIN READ ONLY"); run(a, "INSERT INTO scratch_ro VALUES (12)"); run(a, "ROLLBACK")
with psycopg.connect(DSN, autocommit=True) as a:
    run(a, "SELECT * FROM scratch_ro ORDER BY x")
