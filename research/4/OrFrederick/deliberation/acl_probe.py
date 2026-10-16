#!/usr/bin/env python3
"""SereneDB access-control probe (throwaway container serenedb-d4, port 7894).

Verifies ACL / sandbox claims for the research deliberation. All probes run
against a disposable container; the caller tears it down afterwards.
"""
import sys
import psycopg

HOST = "localhost"
PORT = 7894
SU = f"host={HOST} port={PORT} dbname=postgres user=postgres password=postgres"


def dsn(user, pw):
    return f"host={HOST} port={PORT} dbname=postgres user={user} password={pw}"


results = []  # (probe_id, description, outcome)


def rec(pid, desc, outcome):
    results.append((pid, desc, outcome))


def one(conn, sql):
    """Run a statement, return ('OK', rowsrepr) or ('ERROR', msg)."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                rows = cur.fetchall()
                return ("OK", repr(rows)[:300])
            return ("OK", f"rowcount={cur.rowcount}")
    except Exception as e:  # noqa: BLE001
        return ("ERROR", f"{type(e).__name__}: {str(e).strip().splitlines()[0][:260]}")


def autocommit_conn(dsn_str):
    c = psycopg.connect(dsn_str, connect_timeout=10)
    c.autocommit = True
    return c


# ---------------------------------------------------------------- setup
def setup():
    with autocommit_conn(SU) as su:
        st, ver = one(su, "SELECT version()")
        rec("version", "SELECT version()", f"{st}: {ver}")

        # table + ~1000 rows
        one(su, "DROP TABLE IF EXISTS t CASCADE")
        one(su, "CREATE TABLE t(line_no int, node text, content text)")
        st, ins = one(su, "INSERT INTO t "
                          "SELECT g, 'node-' || (g % 5), 'content-secret-' || g "
                          "FROM generate_series(1, 1000) AS r(g)")
        rec("setup.insert", "INSERT 1000 rows into t", f"{st}: {ins}")
        st, cnt = one(su, "SELECT count(*) FROM t")
        rec("setup.rows", "rows in t", f"{st}: {cnt}")

        # views
        one(su, "DROP VIEW IF EXISTS v_eng")
        one(su, "DROP VIEW IF EXISTS v_mgr")
        st, _ = one(su, "CREATE VIEW v_eng AS SELECT line_no, node, content FROM t")
        rec("setup.v_eng", "CREATE VIEW v_eng (all cols)", st)
        st, _ = one(
            su,
            "CREATE VIEW v_mgr AS "
            "SELECT split_part(node,'-',1) AS node_prefix, count(*) AS n "
            "FROM t GROUP BY 1",
        )
        rec("setup.v_mgr", "CREATE VIEW v_mgr (aggregate, no content)", st)

        # clean leftovers from a previous run
        one(su, "DROP TABLE IF EXISTS t_after CASCADE")
        one(su, "DROP TEXT SEARCH DICTIONARY IF EXISTS d1")
        one(su, "ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM mgr")
        # roles (revoke/drop-owned first so dependent grants don't block DROP ROLE)
        for r in ("eng", "mgr", "mgr2"):
            one(su, f"DROP OWNED BY {r} CASCADE")
            one(su, f"DROP ROLE IF EXISTS {r}")
        st, _ = one(su, "CREATE ROLE eng LOGIN PASSWORD 'eng_pw_throwaway'")
        rec("setup.role_eng", "CREATE ROLE eng LOGIN", st)
        st, _ = one(su, "CREATE ROLE mgr LOGIN PASSWORD 'mgr_pw_throwaway'")
        rec("setup.role_mgr", "CREATE ROLE mgr LOGIN", st)
        st, _ = one(su, "CREATE ROLE mgr2 LOGIN PASSWORD 'mgr2_pw_throwaway'")
        rec("setup.role_mgr2", "CREATE ROLE mgr2 LOGIN", st)

        # grant own view only
        st, _ = one(su, "GRANT SELECT ON v_mgr TO mgr")
        rec("setup.grant_mgr", "GRANT SELECT v_mgr TO mgr", st)
        st, _ = one(su, "GRANT SELECT ON v_eng TO eng")
        rec("setup.grant_eng", "GRANT SELECT v_eng TO eng", st)


# ---------------------------------------------------------------- probes as mgr
def probes_as_mgr():
    mgr = autocommit_conn(dsn("mgr", "mgr_pw_throwaway"))
    try:
        # 1. SELECT from own view / t / v_eng
        rec("1a", "mgr: SELECT from own view v_mgr", "%s: %s" % one(mgr, "SELECT * FROM v_mgr ORDER BY 1"))
        rec("1b", "mgr: SELECT from base table t (no grant)", "%s: %s" % one(mgr, "SELECT count(*) FROM t"))
        rec("1c", "mgr: SELECT from v_eng (not granted)", "%s: %s" % one(mgr, "SELECT count(*) FROM v_eng"))

        # 2. read server files as non-superuser
        rec("2a", "mgr: read_csv('/etc/passwd')", "%s: %s" % one(mgr, "SELECT * FROM read_csv('/etc/passwd')"))
        rec("2b", "mgr: read_text('/etc/hostname')", "%s: %s" % one(mgr, "SELECT content FROM read_text('/etc/hostname')"))
        rec("2c", "mgr: read_text glob '/etc/*'", "%s: %s" % one(mgr, "SELECT count(*) FROM read_text('/etc/*')"))
        rec("2d", "mgr: read_blob('/etc/hostname')", "%s: %s" % one(mgr, "SELECT size FROM read_blob('/etc/hostname')"))

        # 3. write server files
        rec("3", "mgr: COPY (SELECT 1) TO '/tmp/x.csv'", "%s: %s" % one(mgr, "COPY (SELECT 1) TO '/tmp/x.csv'"))

        # 5. view definition exposed via pg_views
        rec("5", "mgr: SELECT definition FROM pg_views WHERE viewname='v_eng'",
            "%s: %s" % one(mgr, "SELECT definition FROM pg_views WHERE viewname='v_eng'"))

        # 6. privilege escalation attempts
        rec("6a", "mgr: SET ROLE postgres", "%s: %s" % one(mgr, "SET ROLE postgres"))
        rec("6b", "mgr: CREATE TABLE evil(x int)", "%s: %s" % one(mgr, "CREATE TABLE evil(x int)"))
        rec("6c", "mgr: CREATE VIEW evilv AS SELECT 1", "%s: %s" % one(mgr, "CREATE VIEW evilv AS SELECT 1"))
        rec("6d", "mgr: ATTACH ':memory:' AS mgr_mem", "%s: %s" % one(mgr, "ATTACH ':memory:' AS mgr_mem"))
    finally:
        mgr.close()


# ---------------------------------------------------------------- probe 4: SET scope
def probe_set_scope():
    # mgr tries to change a security setting; superuser then observes
    mgr = autocommit_conn(dsn("mgr", "mgr_pw_throwaway"))
    try:
        rec("4a", "mgr: SET enable_external_access=false",
            "%s: %s" % one(mgr, "SET enable_external_access=false"))
        rec("4a2", "mgr: SET GLOBAL enable_external_access=false",
            "%s: %s" % one(mgr, "SET GLOBAL enable_external_access=false"))
    finally:
        mgr.close()
    # new superuser connection: is the change visible server-wide?
    su = autocommit_conn(SU)
    try:
        rec("4b", "postgres(new conn): SHOW enable_external_access",
            "%s: %s" % one(su, "SELECT current_setting('enable_external_access')"))
        rec("4c", "postgres(new conn): read_text('/etc/hostname') after mgr's SET",
            "%s: %s" % one(su, "SELECT content FROM read_text('/etc/hostname')"))
        # attempt to restore (expected to fail: one-way latch while running)
        rec("4d", "postgres: SET GLOBAL enable_external_access=true (attempt restore)",
            "%s: %s" % one(su, "SET GLOBAL enable_external_access=true"))
    finally:
        su.close()


# ---------------------------------------------------------------- probe 7: column grant
def probe_column_grant():
    with autocommit_conn(SU) as su:
        rec("7setup", "postgres: GRANT SELECT (line_no,node) ON t TO mgr2",
            "%s: %s" % one(su, "GRANT SELECT (line_no, node) ON t TO mgr2"))
    m2 = autocommit_conn(dsn("mgr2", "mgr2_pw_throwaway"))
    try:
        rec("7a", "mgr2: SELECT node FROM t (granted col)", "%s: %s" % one(m2, "SELECT node FROM t LIMIT 1"))
        rec("7b", "mgr2: SELECT content FROM t (ungranted col)", "%s: %s" % one(m2, "SELECT content FROM t LIMIT 1"))
        rec("7c", "mgr2: SELECT * FROM t", "%s: %s" % one(m2, "SELECT * FROM t LIMIT 1"))
        rec("7d", "mgr2: SELECT count(*) FROM t", "%s: %s" % one(m2, "SELECT count(*) FROM t"))
    finally:
        m2.close()


# ---------------------------------------------------------------- probe 8: default privileges
def probe_default_privileges():
    with autocommit_conn(SU) as su:
        rec("8setup", "postgres: ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO mgr",
            "%s: %s" % one(su, "ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO mgr"))
        rec("8create", "postgres: CREATE TABLE t_after(x int); INSERT",
            "%s: %s" % one(su, "CREATE TABLE t_after(x int)"))
        one(su, "INSERT INTO t_after VALUES (1),(2),(3)")
    mgr = autocommit_conn(dsn("mgr", "mgr_pw_throwaway"))
    try:
        rec("8", "mgr: SELECT from t_after (created after default-priv grant)",
            "%s: %s" % one(mgr, "SELECT count(*) FROM t_after"))
    finally:
        mgr.close()


# ---------------------------------------------------------------- probe 9: wrong password
def probe_wrong_password():
    try:
        c = psycopg.connect(dsn("mgr", "WRONG_password"), connect_timeout=10)
        c.close()
        rec("9", "mgr: connect with WRONG password over TCP", "OK: CONNECTED (auth NOT enforced!)")
    except Exception as e:  # noqa: BLE001
        rec("9", "mgr: connect with WRONG password over TCP",
            f"ERROR: {type(e).__name__}: {str(e).strip().splitlines()[0][:220]}")
    # discover hba/auth method
    with autocommit_conn(SU) as su:
        rec("9hba", "postgres: SHOW hba", "%s: %s" % one(su, "SELECT current_setting('hba')"))


# ---------------------------------------------------------------- probe 10: text search dict syntax
def probe_tsdict():
    with autocommit_conn(SU) as su:
        one(su, "DROP TEXT SEARCH DICTIONARY IF EXISTS d1")
        rec("10a", "postgres: CREATE TEXT SEARCH DICTIONARY d1 (TEMPLATE='segmentation')  [pg-style]",
            "%s: %s" % one(su, "CREATE TEXT SEARCH DICTIONARY d1 (TEMPLATE = 'segmentation')"))
        one(su, "DROP TEXT SEARCH DICTIONARY IF EXISTS d2")
        rec("10b", "postgres: CREATE TEXT SEARCH DICTIONARY d2 AS split_text(case:='lower')  [serenedb form]",
            "%s: %s" % one(su, "CREATE TEXT SEARCH DICTIONARY d2 AS split_text(case := 'lower')"))
        one(su, "DROP TEXT SEARCH DICTIONARY IF EXISTS d3")
        rec("10c", "postgres: CREATE TEXT SEARCH DICTIONARY d3 AS split_text_icu('zh_CN')  [icu segmentation]",
            "%s: %s" % one(su, "CREATE TEXT SEARCH DICTIONARY d3 AS split_text_icu('zh_CN')"))


# ---------------------------------------------------------------- probe 11: drop role with column grant
def probe_drop_role_with_grant():
    with autocommit_conn(SU) as su:
        rec("11", "postgres: DROP ROLE mgr2 (holds column grant on t)",
            "%s: %s" % one(su, "DROP ROLE mgr2"))


def main():
    setup()
    probes_as_mgr()
    probe_column_grant()
    probe_default_privileges()
    probe_wrong_password()
    probe_tsdict()
    probe_drop_role_with_grant()
    # probe 4 LAST: it irreversibly disables external access instance-wide
    # (SET GLOBAL ...=true fails while running), so it must not precede the
    # file-access probes. Clean baseline is restored by recreating the container.
    probe_set_scope()

    out = []
    out.append("SereneDB ACL probe — container serenedb-d4 (port 7894), image serenedb/serenedb:26.09.1.3")
    out.append("=" * 100)
    for pid, desc, outcome in results:
        out.append(f"[{pid}] {desc}\n    -> {outcome}")
    text = "\n".join(out) + "\n"
    sys.stdout.write(text)
    with open(
        "/Users/frederick/Development/LogSearcher-wt/4/research/4/OrFrederick/deliberation/acl_probe_out.txt",
        "w",
    ) as f:
        f.write(text)


if __name__ == "__main__":
    main()
