from pathlib import Path

import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "architecture/invariants.v1.json"

registry = json.loads(REG.read_text())
items = registry["invariants"]

expected = [f"INV-{i:03d}" for i in range(1, 51)]
ids = [item["id"] for item in items]

assert ids == expected, f"invariant sequence mismatch: {ids}"
assert len(ids) == len(set(ids)) == len(expected), "duplicate/missing invariant"

for item in items:
    assert item["name"].strip(), f"{item['id']} missing name"
    assert item["implementation"], f"{item['id']} missing implementation"
    assert item["tests"], f"{item['id']} missing tests"

    for rel in item["implementation"] + item["tests"]:
        assert (ROOT / rel).is_file(), f"{item['id']} missing artifact: {rel}"

manifest = {
    "schema": "aodsl.architecture-freeze.v1",
    "architecture_version": registry["architecture_version"],
    "invariant_count": len(expected),
    "registry_sha256": hashlib.sha256(REG.read_bytes()).hexdigest(),
    "invariant_ids": ids,
}

out = ROOT / "architecture/freeze-manifest.v1.json"
out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

print("AODSL v1 ARCHITECTURE FREEZE GATE: PASSED")
print(
    f"Invariant coverage: {expected[0]}..{expected[-1]} "
    f"({len(ids)}/{len(expected)})"
)
print("Registry SHA256:", manifest["registry_sha256"])
