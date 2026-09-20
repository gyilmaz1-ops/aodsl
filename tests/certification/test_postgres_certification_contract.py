import inspect
from aodsl.certification import postgres as p
def test_cert_pg_001_static_contract():
    s=inspect.getsource(p)
    assert "FOR UPDATE SKIP LOCKED" in s
    assert "psycopg.connect(dsn)" in s
    assert "threading.Thread" in s
    assert "lease_version=t.lease_version+1" in s
    assert "lease_until>now()" in s
    assert "stale generation finalized" in s
    assert "event/outbox lease semantic parity" in s
    assert "DROP TABLE IF EXISTS" in s
def test_cert_pg_001_fail_closed(monkeypatch):
    monkeypatch.delenv("AODSL_POSTGRES_DSN",raising=False)
    r=p.postgres_certification()
    assert r["status"]=="NOT_CERTIFIED"
    assert "AODSL_POSTGRES_DSN" in r["reason"]
