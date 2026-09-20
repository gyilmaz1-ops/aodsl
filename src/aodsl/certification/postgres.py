"""CERT-PG-001: live PostgreSQL multi-node lease/fencing certification.

Fail-closed: certification is possible only with AODSL_POSTGRES_DSN and psycopg.
Uses isolated temporary-named tables and independent DB connections.
"""
import os,uuid,threading

def _claim(psycopg,dsn,table,idcol,status,active,worker,lease_seconds=30,only_id=None):
    where=f"""(({status} IN ('PENDING','FAILED') AND (lease_until IS NULL OR lease_until < now()))
               OR ({status}='{active}' AND lease_until < now()))"""
    extra=f" AND {idcol}=%s" if only_id else ""
    params=([only_id] if only_id else [])+[worker,lease_seconds]
    sql=f"""WITH candidate AS (
      SELECT {idcol} FROM {table} WHERE {where}{extra}
      ORDER BY {idcol} FOR UPDATE SKIP LOCKED LIMIT 1)
    UPDATE {table} t SET {status}=%s,
      lease_owner=%s,lease_until=now()+(%s * interval '1 second'),
      lease_version=t.lease_version+1,attempt_count=t.attempt_count+1
    FROM candidate c WHERE t.{idcol}=c.{idcol}
    RETURNING t.{idcol},t.lease_version"""
    # status value is prepended after any optional id predicate.
    params=([only_id] if only_id else [])+[active,worker,lease_seconds]
    with psycopg.connect(dsn) as c:
        with c.transaction():
            return c.execute(sql,params).fetchone()

def _finalize(psycopg,dsn,table,idcol,status,active,done,row_id,owner,version):
    with psycopg.connect(dsn,autocommit=True) as c:
        return c.execute(f"""UPDATE {table} SET {status}=%s
          WHERE {idcol}=%s AND {status}=%s AND lease_owner=%s
            AND lease_version=%s AND lease_until>now()
          RETURNING {idcol}""",(done,row_id,active,owner,version)).fetchone()

def postgres_certification():
    dsn=os.getenv("AODSL_POSTGRES_DSN")
    if not dsn:
        return {"id":"CERT-PG-001","status":"NOT_CERTIFIED","reason":"AODSL_POSTGRES_DSN is not set"}
    try: import psycopg
    except Exception as e:
        return {"id":"CERT-PG-001","status":"NOT_CERTIFIED","reason":f"psycopg unavailable: {e}"}
    suffix=uuid.uuid4().hex[:12]
    et=f"aodsl_cert_event_{suffix}"; ot=f"aodsl_cert_outbox_{suffix}"
    covered=[]
    try:
        with psycopg.connect(dsn,autocommit=True) as c:
            c.execute(f"""CREATE TABLE {et}(
              event_id text primary key,status text not null default 'PENDING',
              lease_owner text,lease_until timestamptz,lease_version bigint not null default 0,
              attempt_count bigint not null default 0)""")
            c.execute(f"""CREATE TABLE {ot}(
              outbox_id text primary key,status text not null default 'PENDING',
              lease_owner text,lease_until timestamptz,lease_version bigint not null default 0,
              attempt_count bigint not null default 0)""")
            c.execute(f"INSERT INTO {et}(event_id) VALUES ('e1'),('e2'),('stale')")
            c.execute(f"INSERT INTO {ot}(outbox_id) VALUES ('o1'),('o2'),('stale')")
        for table,idcol,status,active,done in (
            (et,"event_id","status","PROCESSING","COMMITTED"),
            (ot,"outbox_id","status","SENDING","DISPATCHED")):
            claimed={}; errors=[]
            def worker(w):
                try: claimed[w]=_claim(psycopg,dsn,table,idcol,status,active,w)
                except Exception as e: errors.append(repr(e))
            a=threading.Thread(target=worker,args=("worker-a",)); b=threading.Thread(target=worker,args=("worker-b",))
            a.start(); b.start(); a.join(10); b.join(10)
            if a.is_alive() or b.is_alive(): raise RuntimeError(f"{table}: concurrent claim timed out")
            if errors: raise RuntimeError(f"{table}: claim errors {errors}")
            rows=[r for r in claimed.values() if r]
            if len(rows)!=2 or len({r[0] for r in rows})!=2:
                raise AssertionError(f"{table}: SKIP LOCKED exclusivity failed: {claimed}")
            with psycopg.connect(dsn,autocommit=True) as c:
                c.execute(f"UPDATE {table} SET {status}=%s,lease_owner='old',lease_until=now()-interval '1 second',lease_version=7 WHERE {idcol}='stale'",(active,))
            row=_claim(psycopg,dsn,table,idcol,status,active,"new",only_id="stale")
            if not row or row[1]!=8: raise AssertionError(f"{table}: lease recovery/version failed: {row}")
            stale=_finalize(psycopg,dsn,table,idcol,status,active,done,"stale","old",7)
            fresh=_finalize(psycopg,dsn,table,idcol,status,active,done,"stale","new",8)
            if stale is not None: raise AssertionError(f"{table}: stale generation finalized")
            if fresh is None: raise AssertionError(f"{table}: current generation could not finalize")
            covered.append("event" if table==et else "outbox")
        return {"id":"CERT-PG-001","status":"CERTIFIED",
          "covered":["independent-connection SKIP LOCKED exclusivity",
                     "event/outbox lease semantic parity","expired lease recovery",
                     "lease_version 7->8","stale finalizer rejection","current finalizer acceptance"]}
    except Exception as e:
        return {"id":"CERT-PG-001","status":"NOT_CERTIFIED","reason":f"{type(e).__name__}: {e}"}
    finally:
        try:
            with psycopg.connect(dsn,autocommit=True) as c:
                c.execute(f"DROP TABLE IF EXISTS {ot}"); c.execute(f"DROP TABLE IF EXISTS {et}")
        except Exception: pass
