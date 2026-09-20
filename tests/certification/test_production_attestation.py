import json,shutil
from pathlib import Path
from aodsl.certification.attestation import (canonical_json_bytes,create_attestation,sha256_bytes,source_tree_entries,verify_attestation)
ROOT=Path(__file__).resolve().parents[2]
def setup(tmp):
 w=tmp/"repo"; shutil.copytree(ROOT,w,ignore=shutil.ignore_patterns(".git","dist","__pycache__",".pytest_cache"))
 e=w/"certification/evidence/live-certification-status.json"
 d=json.loads(e.read_text())
 d["tested_source_tree_sha256"]=sha256_bytes(canonical_json_bytes(source_tree_entries(w)))
 e.write_text(json.dumps(d))
 m,a=create_attestation(w,e); mp=w/"certification/production-certification-manifest.json"; ap=w/"certification/production-certification-attestation.json"; mp.write_text(json.dumps(m)); ap.write_text(json.dumps(a)); return w,e,mp,ap
def test_valid(tmp_path):
 w,e,m,a=setup(tmp_path); assert verify_attestation(w,e,m,a)==[]
def test_source_tamper(tmp_path):
 w,e,m,a=setup(tmp_path); p=w/"src/aodsl/__init__.py"; p.write_text(p.read_text()+"\n#tamper\n"); assert verify_attestation(w,e,m,a)
def test_evidence_tamper(tmp_path):
 w,e,m,a=setup(tmp_path); d=json.loads(e.read_text()); d["cert_pg_001"]="NOT_CERTIFIED"; e.write_text(json.dumps(d)); assert verify_attestation(w,e,m,a)
def test_manifest_tamper(tmp_path):
 w,e,m,a=setup(tmp_path); d=json.loads(m.read_text()); d["product"]["version"]="9.9.9"; m.write_text(json.dumps(d)); assert verify_attestation(w,e,m,a)
