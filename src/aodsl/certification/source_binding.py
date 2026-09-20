from pathlib import Path
from aodsl.certification.attestation import source_tree_entries,canonical_json_bytes,sha256_bytes
def canonical_source_tree_sha256(root: Path)->str:
 return sha256_bytes(canonical_json_bytes(source_tree_entries(Path(root))))
