from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from investment_domain import (
    Evidence,
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
    canonical_id,
)
from investment_domain.postgres_migrations import PostgreSQLMigrationManager


UTC = timezone.utc
DSN = os.environ.get("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN is not configured",
)


def connect():
    import psycopg

    return psycopg.connect(DSN)


@pytest.fixture(autouse=True)
def clean_database():
    manager = PostgreSQLMigrationManager(DSN)
    manager.migrate()

    with connect() as con:
        con.execute("DELETE FROM domain_edges")
        con.execute("DELETE FROM valuation_facts")
        con.execute("DELETE FROM forecast_facts")
        con.execute("DELETE FROM estimate_facts")
        con.execute("DELETE FROM calculation_facts")
        con.execute("DELETE FROM metric_facts")
        con.execute("DELETE FROM evidence_facts")
        con.execute("DELETE FROM claim_facts")
        con.execute("DELETE FROM domain_nodes")
        con.commit()

    yield

    with connect() as con:
        con.execute("DELETE FROM domain_edges")
        con.execute("DELETE FROM valuation_facts")
        con.execute("DELETE FROM forecast_facts")
        con.execute("DELETE FROM estimate_facts")
        con.execute("DELETE FROM calculation_facts")
        con.execute("DELETE FROM metric_facts")
        con.execute("DELETE FROM evidence_facts")
        con.execute("DELETE FROM claim_facts")
        con.execute("DELETE FROM domain_nodes")
        con.commit()


def utc(hour):
    return datetime(2026, 9, 1, hour, tzinfo=UTC)


def make_evidence(seed):
    payload = {
        "source_id": f"source:{seed}",
        "source_version": "1",
        "content_hash": f"{sum(seed.encode()):064x}"[-64:],
        "effective_at": datetime(2026, 6, 30, tzinfo=UTC),
        "observed_at": utc(8),
        "published_at": utc(9),
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=utc(10),
        supersedes_id=None,
        source_uri=f"https://example.test/{seed}",
    )


def test_evidence_at_anchor_type_mismatch_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    node = make_evidence("anchor-type")
    repo.add_evidence(node)

    try:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    DROP CONSTRAINT evidence_domain_node_fk
                    """
                )
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Claim'
                    WHERE id = %s
                    """,
                    (node.id,),
                )

        with pytest.raises(
            RepositoryReadError,
            match="IDM-R588: EVIDENCE_TYPE_MISMATCH",
        ):
            repo.evidence_at(node.id, utc(12))
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Evidence'
                    WHERE id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    ADD CONSTRAINT evidence_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )


def test_evidence_at_projection_type_mismatch_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    node = make_evidence("projection-type")
    repo.add_evidence(node)

    try:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    DROP CONSTRAINT evidence_domain_node_fk
                    """
                )
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    DROP CONSTRAINT evidence_node_type
                    """
                )
                con.execute(
                    """
                    UPDATE evidence_facts
                    SET node_type = 'Claim'
                    WHERE node_id = %s
                    """,
                    (node.id,),
                )

        with pytest.raises(
            RepositoryReadError,
            match="IDM-R588: EVIDENCE_TYPE_MISMATCH",
        ):
            repo.evidence_at(node.id, utc(12))
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE evidence_facts
                    SET node_type = 'Evidence'
                    WHERE node_id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    ADD CONSTRAINT evidence_node_type
                    CHECK (node_type = 'Evidence')
                    """
                )
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    ADD CONSTRAINT evidence_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )


def test_evidence_at_invalid_stored_projection_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    node = make_evidence("invalid-stored")
    repo.add_evidence(node)

    try:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    DROP CONSTRAINT evidence_publication_order
                    """
                )
                con.execute(
                    """
                    UPDATE evidence_facts
                    SET published_at = %s
                    WHERE node_id = %s
                    """,
                    (utc(7), node.id),
                )

        with pytest.raises(
            RepositoryReadError,
            match="IDM-R589: INVALID_STORED_EVIDENCE",
        ):
            repo.evidence_at(node.id, utc(12))
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE evidence_facts
                    SET published_at = %s
                    WHERE node_id = %s
                    """,
                    (utc(9), node.id),
                )
                con.execute(
                    """
                    ALTER TABLE evidence_facts
                    ADD CONSTRAINT evidence_publication_order
                    CHECK (observed_at <= published_at)
                    """
                )


def test_evidence_at_integrity_failure_uses_evidence_diagnostic():
    repo = PostgreSQLEvidenceRepository(DSN)
    node = make_evidence("integrity")
    repo.add_evidence(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R590: EVIDENCE_INTEGRITY_FAILURE",
    ):
        repo.evidence_at(node.id, utc(12))


def test_future_exact_evidence_checks_integrity_before_visibility():
    repo = PostgreSQLEvidenceRepository(DSN)

    payload = {
        "source_id": "source:future-corrupted",
        "source_version": "1",
        "content_hash": "f" * 64,
        "effective_at": datetime(2026, 6, 30, tzinfo=UTC),
        "observed_at": utc(11),
        "published_at": utc(12),
    }

    node = Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=utc(13),
        supersedes_id=None,
        source_uri="https://example.test/future-corrupted",
    )
    repo.add_evidence(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R590: EVIDENCE_INTEGRITY_FAILURE",
    ):
        repo.evidence_at(node.id, utc(10))


def test_evidence_at_missing_projection_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    node = make_evidence("missing-projection")
    repo.add_evidence(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM evidence_facts WHERE node_id = %s",
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R591: EVIDENCE_PROJECTION_NOT_FOUND",
    ):
        repo.evidence_at(node.id, utc(12))
