from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELEASE_GATE = ROOT / "tools" / "release_gate.py"


def test_fail_closed_certification_uses_environment_without_postgres_dsn():
    source = RELEASE_GATE.read_text(encoding="utf-8")

    assert 'cert_env = {**ENV}' in source
    assert 'cert_env.pop("AODSL_POSTGRES_DSN", None)' in source
    assert (
        'run([sys.executable,"-m","aodsl","certify"],'
        'expect=2,env=cert_env)'
        in source.replace(" ", "")
    )
