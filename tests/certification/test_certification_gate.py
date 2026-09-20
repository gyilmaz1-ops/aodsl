from aodsl.certification import certification_status
def test_fail_closed_without_live_postgres(monkeypatch):
    monkeypatch.delenv("AODSL_POSTGRES_DSN",raising=False)
    s=certification_status()
    assert s["architecture"]["status"]=="PASSED"
    assert s["operations"]["status"]=="CERTIFIED"
    assert s["postgres"]["status"]=="NOT_CERTIFIED"
    assert s["production_status"]=="NOT_CERTIFIED"
