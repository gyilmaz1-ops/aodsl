from pathlib import Path
import subprocess,sys,os,re,hashlib,json,zipfile,tempfile
ROOT=Path(__file__).resolve().parents[1]
ENV={**os.environ,"PYTHONPATH":str(ROOT/"src"),"PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"}

def run(cmd, expect=0, timeout=180, env=None):
    r=subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=ENV if env is None else env,
    )
    print("$"," ".join(map(str,cmd)))
    print(r.stdout,end="")
    if r.returncode!=expect:
        print(r.stderr,end="")
        raise SystemExit(f"release stage failed: expected {expect}, got {r.returncode}")
    return r

# 1) Version consistency.
from aodsl.certification.version_policy import (
    read_project_version,
    validate_git_release_identity,
    validate_release_version,
)
from aodsl import __version__ as runtime_version
from investment_domain import __version__ as investment_version

production = "--production" in sys.argv[1:]
pv = read_project_version(ROOT / "pyproject.toml")

if production:
    ref_type = os.environ.get("GITHUB_REF_TYPE", "").strip()
    git_tag = os.environ.get("GITHUB_REF_NAME", "").strip()
    if ref_type != "tag" or not git_tag:
        raise SystemExit(
            "VERSION-011: production release requires a Git tag"
        )

    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    if not github_sha:
        raise SystemExit(
            "VERSION-012: production release requires GITHUB_SHA"
        )

    try:
        validate_git_release_identity(
            repository_root=ROOT,
            git_tag=git_tag,
            github_ref_type=ref_type,
            github_sha=github_sha,
        )
    except ValueError as exc:
        raise SystemExit(f"VERSION-012: {exc}") from exc
else:
    git_tag = f"v{pv}"

try:
    validate_release_version(
        package_version=pv,
        runtime_version=runtime_version,
        investment_version=investment_version,
        git_tag=git_tag,
    )
except ValueError as exc:
    raise SystemExit(f"VERSION-011: {exc}") from exc

print("VERSION CONSISTENCY: PASSED", pv)

run([sys.executable,str(ROOT/"tools/architecture_freeze_gate.py")])

# 2) Package-native architecture contracts.
run([sys.executable,str(ROOT/"tests/run_contract_suite.py")],timeout=240)

# 3) Package boundary.
run([sys.executable,str(ROOT/"tests/contract/test_package_boundary.py")])

# 4) Certification must fail closed without a live PostgreSQL DSN.
cert_env = {**ENV}
cert_env.pop("AODSL_POSTGRES_DSN", None)
run([sys.executable,"-m","aodsl","certify"],expect=2,env=cert_env)

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
# Bind archive membership and content to the committed Git tree.
tree_data = subprocess.check_output(
    ["git", "ls-tree", "-r", "-z", "HEAD"],
    cwd=ROOT,
)
files = []

for record in tree_data.split(b"\x00"):
    if not record:
        continue

    metadata, separator, raw_path = record.partition(b"\x09")
    if not separator:
        raise RuntimeError("INVALID_GIT_TREE_RECORD")

    fields = metadata.decode("ascii").split()
    if len(fields) != 3:
        raise RuntimeError("INVALID_GIT_TREE_METADATA")

    mode, kind, object_id = fields

    if kind != "blob" or mode not in ("100644", "100755"):
        raise RuntimeError("UNSUPPORTED_GIT_TREE_ENTRY")

    name = raw_path.decode("utf-8")

    if (
        not name
        or name.startswith("/")
        or chr(92) in name
        or chr(0) in name
        or any(
            part in ("", ".", "..")
            for part in name.split("/")
        )
    ):
        raise RuntimeError("UNSAFE_GIT_TREE_PATH")

    files.append((name, object_id, mode))

files.sort(key=lambda item: item[0])

with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as z:
    for name, object_id, mode in files:
        data = subprocess.check_output(
            ["git", "cat-file", "blob", object_id],
            cwd=ROOT,
        )
        zi = zipfile.ZipInfo(name)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.date_time = (1980, 1, 1, 0, 0, 0)
        zi.create_system = 3
        permissions = 0o755 if mode == "100755" else 0o644
        zi.external_attr = (0o100000 | permissions) << 16
        z.writestr(zi, data)
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
