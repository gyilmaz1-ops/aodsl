from ._support import *

def test_inv021_bounded_causal_propagation():
    a.inv021_contract_tests()


# INV-022 — authoritative bounded ephemeral idempotency resource-safety contract.

def test_inv022_bounded_ephemeral_idempotency_state():
    a.inv022_contract_tests()


# INV-023 — authoritative source-to-runtime compiler integrity contract.

def test_inv023_source_to_runtime_integrity():
    c.inv023_contract_tests()


# INV-024 — authoritative declared snapshot dependency contract.

def test_inv024_declared_snapshot_dependency():
    sc.inv024_contract_tests()


# INV-025 — authoritative bounded research termination contract.

def test_inv025_bounded_research_termination():
    re050.inv025_contract_tests()

# Frozen v0.5.0 behavioral baseline: R001-R009 source ruleset compiles and
# preserves the explicit R007 retry / R009 terminal-exhaustion boundary.

def test_inv026_029_multiworker_lease_fencing():
    mw060.inv026_029_contract_tests()


# INV-030..INV-033 — authoritative multi-worker outbox lease/fencing contracts.

def test_inv030_033_multiworker_outbox_lease_fencing():
    ob070.inv030_033_contract_tests()


# INV-034..INV-037 — authoritative end-to-end crash consistency contracts.

def test_inv034_037_e2e_crash_consistency():
    e2e080.inv034_037_contract_tests()


# INV-038..INV-041 — authoritative versioned schema evolution contracts.

