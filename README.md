# AODSL 1.0

Deterministic orchestration DSL/control plane reference package.

## Commands

- `aodsl validate rules.aodsl`
- `aodsl test-contract`
- `aodsl certify`

`certify` is fail-closed and requires a live PostgreSQL DSN for CERT-PG-001.
Architecture contract and deployment certification remain separate.
