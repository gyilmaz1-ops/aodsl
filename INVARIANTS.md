# AODSL Invariant Registry


## INV-043 — Globally Unique Durable Identity

Durable logical IDs use UUIDv7-compatible 128-bit identifiers with 74 random bits. Identity does not depend on process-local counters or synchronized clocks. Contract: `tests/contract/test_inv043_identity.py`.
