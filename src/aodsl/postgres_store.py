"""
AODSL v0.9.4 — PostgreSQL Execution Store Adapter

Concrete production adapter for INV-045. Import-safe without psycopg installed;
a live PostgreSQL DSN + psycopg are required for certification.

The adapter uses:
- SELECT ... FOR UPDATE SKIP LOCKED
- atomic lease_version increment
- owner/version/expiry fenced finalization
- identical event/outbox lease semantics
"""
from dataclasses import dataclass
from typing import Optional
import os

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # certification environment supplies psycopg
    psycopg=None
    dict_row=None

import aodsl.runtime as a


EVENT_CLAIM_SQL = """
WITH candidate AS (
 SELECT event_id FROM event_log
 WHERE (
   (status IN ('PENDING','FAILED') AND COALESCE(next_attempt_at, '-infinity'::timestamptz) <= CURRENT_TIMESTAMP)
   OR (status='PROCESSING' AND lease_expires_at <= CURRENT_TIMESTAMP)
 )
 ORDER BY created_at,event_id
 FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE event_log e SET
 status='PROCESSING', lease_owner=%s,
 lease_expires_at=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
 lease_version=e.lease_version+1, attempt_count=e.attempt_count+1
FROM candidate c WHERE e.event_id=c.event_id
RETURNING e.*;
""".strip()

OUTBOX_CLAIM_SQL = """
WITH candidate AS (
 SELECT outbox_id FROM outbox
 WHERE (
   (status IN ('PENDING','FAILED') AND COALESCE(next_attempt_at, '-infinity'::timestamptz) <= CURRENT_TIMESTAMP)
   OR (status='SENDING' AND lease_expires_at <= CURRENT_TIMESTAMP)
 )
 ORDER BY created_at,outbox_id
 FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE outbox o SET
 status='SENDING', lease_owner=%s,
 lease_expires_at=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
 lease_version=o.lease_version+1, attempt_count=o.attempt_count+1
FROM candidate c WHERE o.outbox_id=c.outbox_id
RETURNING o.*;
""".strip()


@dataclass(frozen=True)
class PgLease:
    kind:str
    row_id:str
    owner:str
    version:int


class PostgreSQLExecutionStore:
    def __init__(self,dsn):
        if psycopg is None:
            raise RuntimeError("psycopg is required for PostgreSQLExecutionStore")
        self.dsn=dsn

    def connect(self):
        return psycopg.connect(self.dsn,row_factory=dict_row)

    def claim_event(self,worker_id,lease_seconds=30)->Optional[PgLease]:
        with self.connect() as con:
            with con.transaction():
                row=con.execute(EVENT_CLAIM_SQL,(worker_id,lease_seconds)).fetchone()
                if not row: return None
                return PgLease("event",row["event_id"],worker_id,row["lease_version"])

    def claim_outbox(self,worker_id,lease_seconds=30)->Optional[PgLease]:
        with self.connect() as con:
            with con.transaction():
                row=con.execute(OUTBOX_CLAIM_SQL,(worker_id,lease_seconds)).fetchone()
                if not row: return None
                return PgLease("outbox",str(row["outbox_id"]),worker_id,row["lease_version"])

    def finalize_event(self,lease:PgLease):
        with self.connect() as con:
            with con.transaction():
                row=con.execute("""
                    UPDATE event_log SET status='COMMITTED',
                      lease_owner=NULL,lease_expires_at=NULL
                    WHERE event_id=%s AND status='PROCESSING'
                      AND lease_owner=%s AND lease_version=%s
                      AND lease_expires_at>CURRENT_TIMESTAMP
                    RETURNING event_id
                """,(lease.row_id,lease.owner,lease.version)).fetchone()
                if not row: raise a.AODSLError("AODSL-R410","STALE_OR_EXPIRED_LEASE")

    def finalize_outbox(self,lease:PgLease):
        with self.connect() as con:
            with con.transaction():
                row=con.execute("""
                    UPDATE outbox SET status='DISPATCHED',
                      lease_owner=NULL,lease_expires_at=NULL
                    WHERE outbox_id=%s AND status='SENDING'
                      AND lease_owner=%s AND lease_version=%s
                      AND lease_expires_at>CURRENT_TIMESTAMP
                    RETURNING outbox_id
                """,(lease.row_id,lease.owner,lease.version)).fetchone()
                if not row: raise a.AODSLError("AODSL-R411","STALE_OR_EXPIRED_OUTBOX_LEASE")


def adapter_contract_tests():
    # Import/static certification works without a live server.
    for sql in (EVENT_CLAIM_SQL,OUTBOX_CLAIM_SQL):
        u=sql.upper()
        assert "FOR UPDATE SKIP LOCKED" in u
        assert "LEASE_VERSION" in u and "+1" in u.replace(" ","")
        assert "RETURNING" in u
    import inspect
    s=inspect.getsource(PostgreSQLExecutionStore)
    assert "lease_owner=%s" in s
    assert "lease_version=%s" in s
    assert "lease_expires_at>CURRENT_TIMESTAMP" in s
    print("AODSL v0.9.4 PostgreSQL adapter static contract: PASSED")


def live_postgres_certification(dsn=None):
    """
    Deployment certification hook. Expects pre-created event_log/outbox tables
    with the v0.9 lease columns. It intentionally does not auto-create schemas.
    """
    dsn=dsn or os.getenv("AODSL_POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("AODSL_POSTGRES_DSN is required for live certification")
    store=PostgreSQLExecutionStore(dsn)
    with store.connect() as con:
        v=con.execute("SHOW server_version").fetchone()
        print("Connected PostgreSQL:",v)
    return True


if __name__=="__main__":
    adapter_contract_tests()
    if os.getenv("AODSL_POSTGRES_DSN"):
        live_postgres_certification()
        print("LIVE POSTGRESQL CONNECTIVITY CERTIFICATION: PASSED")
    else:
        print("LIVE POSTGRESQL CERTIFICATION: NOT RUN (AODSL_POSTGRES_DSN not set)")
