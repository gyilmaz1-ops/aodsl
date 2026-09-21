from __future__ import annotations
import hashlib,json
from pathlib import Path
SCHEMA="aodsl.production-certification.v1"; HASH_ALGORITHM="sha256"
EXCLUDED_DIRS={".git",".pytest_cache","__pycache__",".mypy_cache",".ruff_cache",".venv","venv","dist","build"}
EXCLUDED_FILES={"certification/production-certification-manifest.json","certification/production-certification-attestation.json","certification/evidence/live-certification-status.json"}
INCLUDED_ROOTS=("src","tests","tools","architecture","deploy","examples","editors","requirements")
INCLUDED_TOP_LEVEL=("pyproject.toml",)
def canonical_json_bytes(v): return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def sha256_file(p): return sha256_bytes(Path(p).read_bytes())
def _excluded(rel):
 p=Path(rel).parts
 return rel in EXCLUDED_FILES or any(x in EXCLUDED_DIRS or x.endswith(".egg-info") for x in p)
def source_tree_entries(root):
 root=Path(root); c=[]
 for n in INCLUDED_TOP_LEVEL:
  p=root/n
  if p.is_file(): c.append(p)
 for d in INCLUDED_ROOTS:
  b=root/d
  if b.exists(): c += [p for p in b.rglob("*") if p.is_file()]
 seen=set(); out=[]
 for p in sorted(c,key=lambda p:p.relative_to(root).as_posix()):
  r=p.relative_to(root).as_posix()
  if r not in seen and not _excluded(r): seen.add(r); out.append({"path":r,"sha256":sha256_file(p)})
 return out
def load_json(p): return json.loads(Path(p).read_text())
def validate_live_evidence(e):
 req={"schema":"aodsl.live-certification.v1","version":"1.0.0","architecture":"PASSED","operations":"CERTIFIED","cert_pg_001":"CERTIFIED","production_deployment":"CERTIFIED"}
 for k,v in req.items():
  if e.get(k)!=v: raise ValueError(f"live evidence {k}: expected {v!r}, got {e.get(k)!r}")
 pg=e.get("postgres")
 if not isinstance(pg,dict) or pg.get("id")!="CERT-PG-001" or pg.get("status")!="CERTIFIED": raise ValueError("PostgreSQL evidence invalid")
def invariant_ids(reg):
 ids=[x.get("id") for x in reg.get("invariants",[]) if isinstance(x,dict)]; exp=[f"INV-{i:03d}" for i in range(1,57)]
 if ids!=exp: raise ValueError("architecture registry must contain exact ordered INV-001..INV-056")
 return ids
def build_manifest(root,evidence_path):
 root=Path(root); rp=root/"architecture/invariants.v1.json"; reg=load_json(rp); ev=load_json(evidence_path)
 validate_live_evidence(ev); ids=invariant_ids(reg); entries=source_tree_entries(root)
 current_source_sha=sha256_bytes(canonical_json_bytes(entries))
 tested_source_sha=ev.get("tested_source_tree_sha256")
 if tested_source_sha != current_source_sha: raise ValueError(f"INV-049 source binding mismatch: tested={tested_source_sha!r}, current={current_source_sha!r}")
 return {"schema":SCHEMA,"product":{"name":"aodsl","version":ev["version"]},"hash_algorithm":HASH_ALGORITHM,
 "source":{"canonical_tree_sha256":current_source_sha,"entry_count":len(entries)},
 "architecture":{"registry_path":"architecture/invariants.v1.json","registry_sha256":sha256_file(rp),"invariants":{"first":ids[0],"last":ids[-1],"count":len(ids)}},
 "evidence":{"path":"certification/evidence/live-certification-status.json","sha256":sha256_file(evidence_path),"tested_source_tree_sha256":tested_source_sha,"architecture":ev["architecture"],"operations":ev["operations"],"cert_pg_001":ev["cert_pg_001"],"production_deployment":ev["production_deployment"],"postgres":{"id":ev["postgres"]["id"],"status":ev["postgres"]["status"],"covered":ev["postgres"].get("covered",[])}}}
def create_attestation(root,evidence_path):
 m=build_manifest(root,evidence_path); return m,{"schema":"aodsl.production-attestation.v1","hash_algorithm":HASH_ALGORITHM,"manifest_sha256":sha256_bytes(canonical_json_bytes(m)),"verification":"FAIL_CLOSED"}
def verify_attestation(root,evidence_path,manifest_path,attestation_path):
 try: sm=load_json(manifest_path); sa=load_json(attestation_path); em=build_manifest(root,evidence_path)
 except Exception as exc: return [str(exc)]
 e=[]
 if sm!=em:e.append("manifest does not match current source/architecture/evidence")
 h=sha256_bytes(canonical_json_bytes(sm))
 if sa.get("schema")!="aodsl.production-attestation.v1":e.append("attestation schema mismatch")
 if sa.get("hash_algorithm")!=HASH_ALGORITHM:e.append("attestation hash algorithm mismatch")
 if sa.get("verification")!="FAIL_CLOSED":e.append("attestation verification mode is not FAIL_CLOSED")
 if sa.get("manifest_sha256")!=h:e.append("attestation manifest SHA-256 mismatch")
 return e
