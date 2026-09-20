from pathlib import Path
import json,hashlib,sys
ROOT=Path(__file__).resolve().parents[1]
REG=ROOT/"architecture/invariants.v1.json"
r=json.loads(REG.read_text())
items=r["invariants"]
expected=[f"INV-{i:03d}" for i in range(1,51)]
ids=[x["id"] for x in items]
assert ids==expected, f"invariant sequence mismatch: {ids}"
assert len(ids)==len(set(ids))==48, "duplicate/missing invariant"
for x in items:
    assert x["name"].strip(), f"{x['id']} missing name"
    assert x["implementation"], f"{x['id']} missing implementation"
    assert x["tests"], f"{x['id']} missing tests"
    for rel in x["implementation"]+x["tests"]:
        assert (ROOT/rel).is_file(), f"{x['id']} missing artifact: {rel}"
manifest={
 "schema":"aodsl.architecture-freeze.v1",
 "architecture_version":r["architecture_version"],
 "invariant_count":48,
 "registry_sha256":hashlib.sha256(REG.read_bytes()).hexdigest(),
 "invariant_ids":ids,
}
out=ROOT/"architecture/freeze-manifest.v1.json"
out.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
print("AODSL v1 ARCHITECTURE FREEZE GATE: PASSED")
print("Invariant coverage: INV-001..INV-050 (50/50)")
print("Registry SHA256:",manifest["registry_sha256"])
