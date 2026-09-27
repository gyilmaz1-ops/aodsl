from __future__ import annotations

import os

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Security
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("IDM_TEST_POSTGRES_DSN"),
    reason="IDM_TEST_POSTGRES_DSN not configured",
)


def make_security() -> Security:
    fields = {
        "company_id": "company:acme",
        "venue": "NASDAQ",
        "ticker": "ACME",
        "currency": "USD",
    }
    return Security(
        id=canonical_id("security", fields),
        **fields,
    )


def repo() -> PostgreSQLEvidenceRepository:
    return PostgreSQLEvidenceRepository(
        os.environ["IDM_TEST_POSTGRES_DSN"]
    )


def test_security_round_trip_is_exact():
    r = repo()
    security = make_security()

    r.add_security(security)

    assert r.security(security.id) == security


def test_security_missing_returns_none():
    r = repo()

    assert r.security(
        "security:"
        "0000000000000000000000000000000000000000000000000000000000000000"
    ) is None


def test_security_write_is_idempotent():
    r = repo()
    security = make_security()

    r.add_security(security)
    r.add_security(security)

    assert r.security(security.id) == security


def test_security_read_fails_closed_on_payload_tamper():
    r = repo()
    security = make_security()
    r.add_security(security)

    with r.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{ticker}',
                        '"TAMPERED"'::jsonb
                    )
                WHERE id = %s
                """,
                (security.id,),
            )

    try:
        with pytest.raises(
            RepositoryReadError,
            match="IDM-R505: STORED_NODE_INTEGRITY_FAILURE",
        ):
            r.security(security.id)
    finally:
        payload, payload_hash = r._payload(security)

        with r.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET canonical_payload = %s::jsonb,
                        payload_hash = %s
                    WHERE id = %s
                    """,
                    (
                        payload,
                        payload_hash,
                        security.id,
                    ),
                )
