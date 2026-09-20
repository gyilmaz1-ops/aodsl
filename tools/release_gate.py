from pathlib import Path
import subprocess,sys,os,re,hashlib,json,zipfile,tempfile
ROOT=Path(__file__).resolve().parents[1]
ENV={**os.environ,"PYTHONPATH":str(ROOT/"src"),"PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"}

def run(cmd, expect=0, timeout=180):
    r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=timeout,env=ENV)
    print("$"," ".join(map(str,cmd)))
    print(r.stdout,end="")
    if r.returncode!=expect:
        print(r.stderr,end="")
        raise SystemExit(f"release stage failed: expected {expect}, got {r.returncode}")
    return r

# 1) Version consistency.
pyproject=(ROOT/"pyproject.toml").read_text()
init=(ROOT/"src/aodsl/__init__.py").read_text()
pv=re.search(r'(?m)^version = "([^"]+)"',pyproject).group(1)
iv=re.search(r'__version__="([^"]+)"',init).group(1)
assert pv==iv=="1.0.0",(pv,iv)
print("VERSION CONSISTENCY: PASSED",pv)

run([sys.executable,str(ROOT/"tools/architecture_freeze_gate.py")])

# 2) Package-native architecture contracts.
run([sys.executable,str(ROOT/"tests/run_contract_suite.py")],timeout=240)

# 3) Package boundary.
run([sys.executable,str(ROOT/"tests/contract/test_package_boundary.py")])

# 4) Certification must fail closed without a live PostgreSQL DSN.
env_saved=os.environ.pop("AODSL_POSTGRES_DSN",None)
try:
    run([sys.executable,"-m","aodsl","certify"],expect=2)
finally:
    if env_saved is not None: os.environ["AODSL_POSTGRES_DSN"]=env_saved

# 5) Optional production attestation gate.
production = "--production" in sys.argv[1:]
prod_manifest_sha = None
prod_source_sha = None
if production:
    verifier=ROOT/"tools/verify_production_attestation.py"
    required=[
        verifier,
        ROOT/"certification/production-certification-manifest.json",
        ROOT/"certification/production-certification-attestation.json",
        ROOT/"certification/evidence/live-certification-status.json",
    ]
    missing=[str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    if missing:
        raise SystemExit("PRODUCTION RELEASE GATE: NOT_CERTIFIED — missing: "+", ".join(missing))
    run([sys.executable,str(verifier)])
    pm=json.loads((ROOT/"certification/production-certification-manifest.json").read_text())
    pa=json.loads((ROOT/"certification/production-certification-attestation.json").read_text())
    if pm.get("evidence",{}).get("cert_pg_001")!="CERTIFIED":
        raise SystemExit("PRODUCTION RELEASE GATE: NOT_CERTIFIED — CERT-PG-001")
    if pm.get("evidence",{}).get("production_deployment")!="CERTIFIED":
        raise SystemExit("PRODUCTION RELEASE GATE: NOT_CERTIFIED — production deployment")
    prod_manifest_sha=pa["manifest_sha256"]
    prod_source_sha=pm["source"]["canonical_tree_sha256"]
    print("PRODUCTION RELEASE GATE: PASSED")

# 6) Build deterministic source release artifact + manifest.
dist=ROOT/"dist"; dist.mkdir(exist_ok=True)
artifact=dist/f"aodsl-{pv}{'-production' if production else ''}-source.zip"
files=sorted(p for p in ROOT.rglob("*") if p.is_file()
             and "dist" not in p.parts and "__pycache__" not in p.parts
             and ".pytest_cache" not in p.parts and ".git" not in p.parts)
with zipfile.ZipFile(artifact,"w",zipfile.ZIP_DEFLATED) as z:
    for p in files:
        zi=zipfile.ZipInfo(str(p.relative_to(ROOT)))
        zi.date_time=(1980,1,1,0,0,0)
        zi.external_attr=0o644<<16
        z.writestr(zi,p.read_bytes())
digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
manifest={"version":pv,"mode":"production" if production else "source",
          "artifact":artifact.name,"sha256":digest,"files":len(files)}
if production:
    manifest["production_certification_manifest_sha256"]=prod_manifest_sha
    manifest["certified_source_tree_sha256"]=prod_source_sha
(ROOT/"dist/release-manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
print("RELEASE ARTIFACT:",artifact.name)
print("SHA256:",digest)
print("AODSL v1.0 PRODUCTION RELEASE GATE: PASSED" if production else "AODSL v1.0 SOURCE RELEASE GATE: PASSED")
