import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_release_gate_binds_actual_certification_file_bytes(tmp_path):
    folder = tmp_path / "certification"
    folder.mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/verify_production_attestation.py").write_text("")
    evidence = folder / "evidence"
    evidence.mkdir()
    (evidence / "live-certification-status.json").write_text("{}")

    manifest = {
        "evidence": {
            "cert_pg_001": "CERTIFIED",
            "production_deployment": "CERTIFIED",
        },
        "source": {"canonical_tree_sha256": "b" * 64},
    }
    path = folder / "production-certification-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"),
    ).encode()
    canonical_sha = hashlib.sha256(canonical).hexdigest()
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert canonical_sha != file_sha

    (folder / "production-certification-attestation.json").write_text(
        json.dumps({"manifest_sha256": canonical_sha})
    )

    tree = ast.parse((ROOT / "tools/release_gate.py").read_text())
    blocks = [
        node for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "production"
        and any(
            isinstance(child, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "prod_manifest_sha"
                for target in child.targets
            )
            for child in ast.walk(node)
        )
    ]
    assert len(blocks) == 1
    module = ast.Module(body=blocks, type_ignores=[])
    ast.fix_missing_locations(module)
    calls = []
    namespace = {
        "ROOT": tmp_path,
        "production": True,
        "json": json,
        "hashlib": hashlib,
        "sys": sys,
        "run": lambda command: calls.append(command),
    }
    exec(compile(module, "release_gate_production_block", "exec"), namespace)
    assert calls, "attestation verification must still run"
    assert namespace["prod_manifest_sha"] == file_sha
    assert namespace["prod_source_sha"] == "b" * 64
