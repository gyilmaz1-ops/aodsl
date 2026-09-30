from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from investment_domain import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
    canonical_id,
)
from investment_domain.claims import ClaimEvidenceLink
from investment_domain.edges import EdgeType
from investment_domain.nodes import Claim, Evidence
from investment_domain.postgres_migrations import PostgreSQLMigrationManager


DSN = os.getenv("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN not configured",
)


def _claim(seed: str = "edge") -> Claim:
    payload = {
        "subject_id": f"company:{seed}",
        "predicate": "revenue_growth",
        "object_value": seed,
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "COMPANY",
        "as_of": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }

    return Claim(
        id=canonical_id("claim", payload),
        **payload,
        created_by="Fundamental_Analyst",
    )


def _evidence(seed: str = "edge") -> Evidence:
    payload = {
        "source_id": f"source:{seed}",
        "source_version": "1",
        "content_hash": "a" * 64,
        "effective_at": datetime(
            2026, 8, 31, tzinfo=timezone.utc
        ),
        "observed_at": datetime(
            2026, 9, 1, 8, tzinfo=timezone.utc
        ),
        "published_at": datetime(
            2026, 9, 1, 9, tzinfo=timezone.utc
        ),
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=datetime(
            2026, 9, 1, 10, tzinfo=timezone.utc
        ),
        source_uri=f"https://example.test/{seed}",
    )


def _link(
    claim: Claim,
    evidence: Evidence,
    *,
    relation: EdgeType = EdgeType.SUPPORTED_BY,
    created_at: datetime | None = None,
) -> ClaimEvidenceLink:
    return ClaimEvidenceLink(
        claim_id=claim.id,
        evidence_id=evidence.id,
        relation=relation,
        created_at=created_at
        or datetime(2026, 9, 1, 11, tzinfo=timezone.utc),
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM risk_facts")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    return repository


@pytest.mark.parametrize(
    "relation",
    [
        EdgeType.SUPPORTED_BY,
        EdgeType.CONTRADICTED_BY,
    ],
)
def test_live_persists_allowed_claim_evidence_relations(
    repo,
    relation,
):
    claim = _claim(relation.value.lower())
    evidence = _evidence(relation.value.lower())
    link = _link(claim, evidence, relation=relation)

    repo.add_claim(claim)
    repo.add_evidence(evidence)
    repo.add_claim_evidence_link(link)

    with repo.connect() as con:
        row = con.execute(
            """
            SELECT source_id, edge_type, target_id, created_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (claim.id, relation.value, evidence.id),
        ).fetchone()

    assert row == (
        claim.id,
        relation.value,
        evidence.id,
        link.created_at,
    )


def test_live_created_at_is_distinct_from_stored_at(repo):
    claim = _claim("semantic-time")
    evidence = _evidence("semantic-time")
    semantic_time = datetime(
        2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )
    link = _link(
        claim,
        evidence,
        created_at=semantic_time,
    )

    repo.add_claim(claim)
    repo.add_evidence(evidence)
    repo.add_claim_evidence_link(link)

    with repo.connect() as con:
        row = con.execute(
            """
            SELECT created_at, stored_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                claim.id,
                link.relation.value,
                evidence.id,
            ),
        ).fetchone()

    assert row[0] == semantic_time
    assert row[1] != semantic_time


def test_live_identical_edge_replay_is_idempotent(repo):
    claim = _claim("replay")
    evidence = _evidence("replay")
    link = _link(claim, evidence)

    repo.add_claim(claim)
    repo.add_evidence(evidence)

    repo.add_claim_evidence_link(link)
    repo.add_claim_evidence_link(link)

    with repo.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                claim.id,
                link.relation.value,
                evidence.id,
            ),
        ).fetchone()[0]

    assert count == 1


def test_live_same_edge_different_created_at_fails_closed(repo):
    claim = _claim("collision")
    evidence = _evidence("collision")
    first = _link(claim, evidence)
    second = _link(
        claim,
        evidence,
        created_at=first.created_at + timedelta(seconds=1),
    )

    repo.add_claim(claim)
    repo.add_evidence(evidence)
    repo.add_claim_evidence_link(first)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W506: EDGE_CONTENT_COLLISION",
    ):
        repo.add_claim_evidence_link(second)

    with repo.connect() as con:
        row = con.execute(
            """
            SELECT created_at, COUNT(*) OVER ()
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                claim.id,
                first.relation.value,
                evidence.id,
            ),
        ).fetchone()

    assert row == (first.created_at, 1)


def test_live_missing_claim_fails_without_edge(repo):
    evidence = _evidence("missing-claim")
    claim = _claim("missing-claim")
    link = _link(claim, evidence)

    repo.add_evidence(evidence)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_claim_evidence_link(link)

    with repo.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM domain_edges"
        ).fetchone()[0]

    assert count == 0


def test_live_missing_evidence_fails_without_edge(repo):
    claim = _claim("missing-evidence")
    evidence = _evidence("missing-evidence")
    link = _link(claim, evidence)

    repo.add_claim(claim)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_claim_evidence_link(link)

    with repo.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM domain_edges"
        ).fetchone()[0]

    assert count == 0


def test_live_source_type_corruption_fails_closed(repo):
    claim = _claim("source-type")
    evidence = _evidence("source-type")
    link = _link(claim, evidence)

    repo.add_evidence(evidence)

    # Simulate repository-external corruption of canonical ID/type.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_nodes (
                    id,
                    node_type,
                    canonical_payload,
                    payload_hash
                )
                VALUES (%s, 'Evidence', '{}'::jsonb, %s)
                """,
                (claim.id, "b" * 64),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W509: EDGE_SOURCE_TYPE_MISMATCH",
    ):
        repo.add_claim_evidence_link(link)


def test_live_target_type_corruption_fails_closed(repo):
    claim = _claim("target-type")
    evidence = _evidence("target-type")
    link = _link(claim, evidence)

    repo.add_claim(claim)

    # Simulate repository-external corruption of canonical ID/type.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_nodes (
                    id,
                    node_type,
                    canonical_payload,
                    payload_hash
                )
                VALUES (%s, 'Claim', '{}'::jsonb, %s)
                """,
                (evidence.id, "c" * 64),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
    ):
        repo.add_claim_evidence_link(link)


def test_live_failed_edge_write_does_not_mutate_endpoints(repo):
    claim = _claim("isolation")
    evidence = _evidence("isolation")
    link = _link(claim, evidence)

    repo.add_claim(claim)
    repo.add_evidence(evidence)

    with repo.connect() as con:
        before = con.execute(
            """
            SELECT id, node_type, canonical_payload, payload_hash
            FROM domain_nodes
            WHERE id IN (%s, %s)
            ORDER BY id
            """,
            (claim.id, evidence.id),
        ).fetchall()

    # Force an edge insert failure after endpoint validation.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE domain_edges
                ADD CONSTRAINT test_reject_all_edges
                CHECK (FALSE)
                """
            )

    try:
        with pytest.raises(Exception):
            repo.add_claim_evidence_link(link)
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    ALTER TABLE domain_edges
                    DROP CONSTRAINT IF EXISTS test_reject_all_edges
                    """
                )

    with repo.connect() as con:
        after = con.execute(
            """
            SELECT id, node_type, canonical_payload, payload_hash
            FROM domain_nodes
            WHERE id IN (%s, %s)
            ORDER BY id
            """,
            (claim.id, evidence.id),
        ).fetchall()

        edge_count = con.execute(
            "SELECT COUNT(*) FROM domain_edges"
        ).fetchone()[0]

    assert after == before
    assert edge_count == 0


def test_live_concurrent_identical_edge_writes_are_idempotent(repo):
    claim = _claim("concurrent-same")
    evidence = _evidence("concurrent-same")
    link = _link(claim, evidence)

    repo.add_claim(claim)
    repo.add_evidence(evidence)

    def write():
        repo.add_claim_evidence_link(link)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write) for _ in range(8)]
        for future in futures:
            future.result()

    with repo.connect() as con:
        rows = con.execute(
            """
            SELECT created_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                claim.id,
                link.relation.value,
                evidence.id,
            ),
        ).fetchall()

    assert rows == [(link.created_at,)]


def test_live_concurrent_conflicting_edge_writes_have_one_winner(repo):
    claim = _claim("concurrent-conflict")
    evidence = _evidence("concurrent-conflict")

    first = _link(
        claim,
        evidence,
        created_at=datetime(
            2026, 9, 1, 11, 0, 0, tzinfo=timezone.utc
        ),
    )
    second = _link(
        claim,
        evidence,
        created_at=datetime(
            2026, 9, 1, 11, 0, 1, tzinfo=timezone.utc
        ),
    )

    repo.add_claim(claim)
    repo.add_evidence(evidence)

    def write(link):
        try:
            repo.add_claim_evidence_link(link)
            return ("success", link.created_at)
        except RepositoryWriteError as exc:
            return (str(exc), link.created_at)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(write, [first, second])
        )

    successes = [
        item for item in results
        if item[0] == "success"
    ]
    collisions = [
        item for item in results
        if "IDM-W506: EDGE_CONTENT_COLLISION" in item[0]
    ]

    assert len(successes) == 1
    assert len(collisions) == 1

    with repo.connect() as con:
        rows = con.execute(
            """
            SELECT created_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                claim.id,
                first.relation.value,
                evidence.id,
            ),
        ).fetchall()

    assert rows == [(successes[0][1],)]


def test_live_link_write_never_creates_missing_endpoints(repo):
    claim = _claim("no-implicit-create")
    evidence = _evidence("no-implicit-create")
    link = _link(claim, evidence)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_claim_evidence_link(link)

    with repo.connect() as con:
        node_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id IN (%s, %s)
            """,
            (claim.id, evidence.id),
        ).fetchone()[0]

        edge_count = con.execute(
            "SELECT COUNT(*) FROM domain_edges"
        ).fetchone()[0]

    assert node_count == 0
    assert edge_count == 0
