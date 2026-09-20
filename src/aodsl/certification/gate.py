from .freeze import verify_architecture
from .ops import run_ops001
from .postgres import postgres_certification

def certification_status():
    arch=verify_architecture()
    ops=run_ops001()
    pg=postgres_certification()
    production=(arch["status"]=="PASSED" and ops["status"]=="CERTIFIED" and pg["status"]=="CERTIFIED")
    return {"architecture":arch,"operations":ops,"postgres":pg,
            "production_status":"CERTIFIED" if production else "NOT_CERTIFIED"}

def run_certification():
    s=certification_status()
    print("AODSL v1.0 PACKAGE-NATIVE PRODUCTION CERTIFICATION")
    print(f'ARCH-001: {s["architecture"]["status"]}')
    print(f'OPS-001: {s["operations"]["status"]}')
    print(f'CERT-PG-001: {s["postgres"]["status"]}')
    if s["postgres"].get("reason"): print("  "+s["postgres"]["reason"])
    print("PRODUCTION DEPLOYMENT:",s["production_status"])
    return 0 if s["production_status"]=="CERTIFIED" else 2
