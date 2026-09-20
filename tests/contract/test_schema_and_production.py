from ._support import *

def test_inv038_041_versioned_schema_migrations():
    sm090.inv038_041_contract_tests()


# INV-042 — authoritative single runtime schema authority contract.

def test_inv042_single_runtime_schema_authority():
    usr091.inv042_contract_tests()

# INV-044 — exclusive migration ownership.

def test_inv044_exclusive_migration_ownership():
    em092.inv044_contract_tests()

# INV-045 — production multi-node claim/fencing semantic contract.

def test_inv045_production_multinode_claim_fencing():
    pg093.inv045_contract_tests()
    pga094.adapter_contract_tests()

# INV-046 — graceful lease handoff.

def test_inv046_graceful_lease_handoff():
    gh096.inv046_contract_tests()

# INV-047 — observable runtime health.

def test_inv047_observable_runtime_health():
    oh097.inv047_contract_tests()

# INV-048 — tamper-evident audit trail.

def test_inv048_tamper_evident_audit_trail():
    ta098.inv048_contract_tests()

