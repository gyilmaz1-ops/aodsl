from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from investment_domain import (
    Claim,
    Evidence,
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
    canonical_id,
)
from investment_domain.canonical import canonical_json
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


def reset_database():
    with connect() as con:
        with con.transaction():
            con.execute("DROP TABLE IF EXISTS domain_edges CASCADE")
            con.execute("DROP TABLE IF EXISTS claim_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS evidence_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS domain_nodes CASCADE")
            con.execute(
                "DROP TABLE IF EXISTS "
                "investment_domain_schema_migrations CASCADE"
            )

    PostgreSQLMigrationManager(DSN).migrate()


@pytest.fixture(autouse=True)
def clean_database():
    reset_database()


def make_claim(*, created_by="Fundamental_Analyst"):
    payload = {
        "subject_id": "company:test",
        "predicate": "revenue_growth_accelerating",
        "object_value": "true",
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "COMPANY",
        "as_of": datetime(2027, 2, 10, tzinfo=UTC),
    }

    return Claim(
        id=canonical_id("claim", payload),
        **payload,
        created_by=created_by,
    )


def make_evidence(
    *,
    ingested_at=None,
    supersedes_id=None,
    source_uri="https://example.test/filing",
):
    payload = {
        "source_id": "filing:test",
        "source_version": "1",
        "content_hash": "a" * 64,
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 2, 10, 7, tzinfo=UTC),
        "published_at": datetime(2027, 2, 10, 8, tzinfo=UTC),
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=ingested_at
        or datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
        supersedes_id=supersedes_id,
        source_uri=source_uri,
    )


def stored_node(node_id):
    with connect() as con:
        return con.execute(
            """
            SELECT
                node_type,
                canonical_payload,
                payload_hash
            FROM domain_nodes
            WHERE id = %s
            """,
            (node_id,),
        ).fetchone()


def test_live_claim_first_write_and_identical_replay():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim()

    repo.add_claim(claim)
    repo.add_claim(claim)

    with connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (claim.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM claim_facts WHERE node_id = %s",
            (claim.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_live_evidence_first_write_and_identical_replay():
    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence()

    repo.add_evidence(evidence)
    repo.add_evidence(evidence)

    with connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (evidence.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM evidence_facts WHERE node_id = %s",
            (evidence.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_live_claim_same_identity_different_full_payload_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    original = make_claim(created_by="Fundamental_Analyst")
    collision = replace(
        original,
        created_by="Industry_Analyst",
    )

    assert collision.id == original.id

    repo.add_claim(original)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501",
    ):
        repo.add_claim(collision)

    row = stored_node(original.id)
    assert row[1] == json.loads(canonical_json(original))


def test_live_evidence_same_identity_different_full_payload_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    original = make_evidence(
        source_uri="https://example.test/original"
    )
    collision = replace(
        original,
        source_uri="https://example.test/different",
    )

    assert collision.id == original.id

    repo.add_evidence(original)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501",
    ):
        repo.add_evidence(collision)

    row = stored_node(original.id)
    assert row[1] == json.loads(canonical_json(original))


@pytest.mark.parametrize(
    "factory,writer",
    [
        (make_claim, "add_claim"),
        (make_evidence, "add_evidence"),
    ],
)
def test_live_canonical_payload_and_hash_are_preserved(
    factory,
    writer,
):
    repo = PostgreSQLEvidenceRepository(DSN)
    node = factory()

    getattr(repo, writer)(node)

    row = stored_node(node.id)

    payload = canonical_json(node)
    expected_hash = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()

    assert row[1] == json.loads(payload)
    assert row[2] == expected_hash


def test_live_claim_projection_corruption_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim()

    repo.add_claim(claim)

    with connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE claim_facts
                SET predicate = %s
                WHERE node_id = %s
                """,
                ("corrupted_predicate", claim.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W504",
    ):
        repo.add_claim(claim)


def test_live_evidence_projection_corruption_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence()

    repo.add_evidence(evidence)

    with connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE evidence_facts
                SET source_version = %s
                WHERE node_id = %s
                """,
                ("corrupted", evidence.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W505",
    ):
        repo.add_evidence(evidence)


def test_live_projection_failure_rolls_back_domain_node():
    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence()

    # Force projection failure while leaving domain_nodes writable.
    with connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE evidence_facts
                ADD CONSTRAINT test_reject_source
                CHECK (source_id <> 'filing:test')
                """
            )

    with pytest.raises(Exception):
        repo.add_evidence(evidence)

    with connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (evidence.id,),
        ).fetchone()[0]

    assert count == 0


def test_live_concurrent_identical_claim_writes_are_idempotent():
    claim = make_claim()

    def write():
        PostgreSQLEvidenceRepository(DSN).add_claim(claim)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write) for _ in range(8)]
        for future in futures:
            future.result()

    with connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (claim.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM claim_facts WHERE node_id = %s",
            (claim.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_live_concurrent_identical_evidence_writes_are_idempotent():
    evidence = make_evidence()

    def write():
        PostgreSQLEvidenceRepository(DSN).add_evidence(evidence)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write) for _ in range(8)]
        for future in futures:
            future.result()

    with connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (evidence.id,),
        ).fetchone()[0]
        projection_count = con.execute(
            "SELECT COUNT(*) FROM evidence_facts WHERE node_id = %s",
            (evidence.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_live_invalid_node_fails_before_write():
    repo = PostgreSQLEvidenceRepository(DSN)
    valid = make_evidence()

    invalid = replace(
        valid,
        ingested_at=valid.published_at - timedelta(seconds=1),
    )

    with pytest.raises(Exception):
        repo.add_evidence(invalid)

    with connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (invalid.id,),
        ).fetchone()[0]

    assert count == 0


def test_live_concurrent_conflicting_claim_payloads_fail_closed():
    original = make_claim(
        created_by="Fundamental_Analyst"
    )
    collision = replace(
        original,
        created_by="Industry_Analyst",
    )

    assert original.id == collision.id
    assert canonical_json(original) != canonical_json(collision)

    def write(node):
        try:
            PostgreSQLEvidenceRepository(DSN).add_claim(node)
            return ("success", canonical_json(node))
        except RepositoryWriteError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(write, original),
            pool.submit(write, collision),
        ]
        results = [future.result() for future in futures]

    successes = [
        value
        for status, value in results
        if status == "success"
    ]
    errors = [
        value
        for status, value in results
        if status == "error"
    ]

    assert len(successes) == 1
    assert len(errors) == 1
    assert "IDM-W501" in errors[0]

    row = stored_node(original.id)

    assert row is not None
    assert row[1] == json.loads(successes[0])

    with connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (original.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM claim_facts WHERE node_id = %s",
            (original.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_live_concurrent_conflicting_evidence_payloads_fail_closed():
    original = make_evidence(
        source_uri="https://example.test/original"
    )
    collision = replace(
        original,
        source_uri="https://example.test/collision",
    )

    assert original.id == collision.id
    assert canonical_json(original) != canonical_json(collision)

    def write(node):
        try:
            PostgreSQLEvidenceRepository(DSN).add_evidence(node)
            return ("success", canonical_json(node))
        except RepositoryWriteError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(write, original),
            pool.submit(write, collision),
        ]
        results = [future.result() for future in futures]

    successes = [
        value
        for status, value in results
        if status == "success"
    ]
    errors = [
        value
        for status, value in results
        if status == "error"
    ]

    assert len(successes) == 1
    assert len(errors) == 1
    assert "IDM-W501" in errors[0]

    row = stored_node(original.id)

    assert row is not None
    assert row[1] == json.loads(successes[0])

    with connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (original.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM evidence_facts WHERE node_id = %s",
            (original.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1
