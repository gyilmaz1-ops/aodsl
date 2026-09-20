import json,shutil,subprocess,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def verify(r):
 e={**os.environ,"PYTHONPATH":str(r/"src"),"PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"}
 return subprocess.run([sys.executable,str(r/"tools/verify_production_attestation.py")],cwd=r,env=e,capture_output=True,text=True)
def test_current_attestation():
 assert verify(ROOT).returncode==0
def test_source_tamper(tmp_path):
 w=tmp_path/"r"; shutil.copytree(ROOT,w,ignore=shutil.ignore_patterns("dist","__pycache__",".pytest_cache"))
 p=w/"src/aodsl/__init__.py"; p.write_text(p.read_text()+"\n#tamper\n")
 assert verify(w).returncode==2
def test_evidence_tamper(tmp_path):
 w=tmp_path/"r"; shutil.copytree(ROOT,w,ignore=shutil.ignore_patterns("dist","__pycache__",".pytest_cache"))
 p=w/"certification/evidence/live-certification-status.json"; d=json.loads(p.read_text()); d["cert_pg_001"]="NOT_CERTIFIED"; p.write_text(json.dumps(d))
 assert verify(w).returncode==2
