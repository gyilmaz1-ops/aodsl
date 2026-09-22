from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from investment_domain import (
    Claim,
    ClaimEvidenceLink,
    EdgeType,
    Evidence,
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
    canonical_id,
)
from investment_domain.postgres_migrations import (
    PostgreSQLMigrationManager,
)

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
        con.execute("DELETE FROM evidence_facts")
        con.execute("DELETE FROM claim_facts")
        con.execute("DELETE FROM domain_nodes")
        con.commit()

    yield

    with connect() as con:
        con.execute("DELETE FROM domain_edges")
        con.execute("DELETE FROM evidence_facts")
        con.execute("DELETE FROM claim_facts")
        con.execute("DELETE FROM domain_nodes")
        con.commit()


def make_claim(seed="pit"):
    payload = {
        "subject_id": f"company:{seed}",
        "predicate": "revenue_growth",
        "object_value": seed,
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "COMPANY",
        "as_of": datetime(2026, 9, 1, tzinfo=UTC),
    }
    return Claim(
        id=canonical_id("claim", payload),
        **payload,
        created_by="Fundamental_Analyst",
    )


def make_evidence(
    seed,
    *,
    effective_at=None,
    observed_at=None,
    published_at=None,
    ingested_at=None,
    supersedes_id=None,
):
    effective_at = effective_at or datetime(
        2026, 6, 30, tzinfo=UTC
    )
    observed_at = observed_at or datetime(
        2026, 9, 1, 8, tzinfo=UTC
    )
    published_at = published_at or datetime(
        2026, 9, 1, 9, tzinfo=UTC
    )
    ingested_at = ingested_at or datetime(
        2026, 9, 1, 10, tzinfo=UTC
    )

    payload = {
        "source_id": f"source:{seed}",
        "source_version": "1",
        "content_hash": (
            f"{sum(seed.encode()):064x}"[-64:]
        ),
        "effective_at": effective_at,
        "observed_at": observed_at,
        "published_at": published_at,
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=ingested_at,
        supersedes_id=supersedes_id,
        source_uri=f"https://example.test/{seed}",
    )


def add_link(
    repo,
    claim,
    evidence,
    *,
    relation=EdgeType.SUPPORTED_BY,
    created_at=None,
):
    repo.add_claim_evidence_link(
        ClaimEvidenceLink(
            claim_id=claim.id,
            evidence_id=evidence.id,
            relation=relation,
            created_at=created_at
            or datetime(2026, 9, 1, 10, 5, tzinfo=UTC),
        )
    )


def setup_pair(
    seed="pit",
    *,
    evidence=None,
    relation=EdgeType.SUPPORTED_BY,
    created_at=None,
):
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim(seed)
    evidence = evidence or make_evidence(seed)

    repo.add_claim(claim)
    repo.add_evidence(evidence)
    add_link(
        repo,
        claim,
        evidence,
        relation=relation,
        created_at=created_at,
    )
    return repo, claim, evidence


def test_read_visible_evidence():
    repo, claim, evidence = setup_pair()

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 11, tzinfo=UTC),
    )

    assert result == (
        (evidence, EdgeType.SUPPORTED_BY),
    )


def test_pre_publication_cutoff_hides_evidence():
    repo, claim, _ = setup_pair()

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 8, 59, tzinfo=UTC),
    )

    assert result == ()


def test_pre_ingestion_cutoff_hides_evidence():
    evidence = make_evidence(
        "ingestion",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    repo, claim, _ = setup_pair(
        "ingestion",
        evidence=evidence,
        created_at=datetime(2026, 9, 1, 9, 30, tzinfo=UTC),
    )

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 11, tzinfo=UTC),
    )

    assert result == ()


def test_pre_edge_cutoff_hides_evidence():
    repo, claim, _ = setup_pair(
        created_at=datetime(2026, 9, 1, 13, tzinfo=UTC),
    )

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result == ()


def test_effective_at_is_not_visibility_gate():
    evidence = make_evidence(
        "future-economic",
        effective_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    repo, claim, evidence = setup_pair(
        "future-economic",
        evidence=evidence,
    )

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 11, tzinfo=UTC),
    )

    assert result == (
        (evidence, EdgeType.SUPPORTED_BY),
    )


def test_predecessor_active_before_successor_available():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("revision-before")

    first = make_evidence(
        "revision-v1",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )
    second = make_evidence(
        "revision-v2",
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=first.id,
    )

    repo.add_claim(claim)
    repo.add_evidence(first)
    repo.add_evidence(second)
    add_link(
        repo,
        claim,
        first,
        created_at=datetime(2026, 9, 1, 10, 5, tzinfo=UTC),
    )

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result == (
        (first, EdgeType.SUPPORTED_BY),
    )


def test_successor_without_explicit_link_does_not_inherit():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("revision-no-inherit")

    first = make_evidence("no-inherit-v1")
    second = make_evidence(
        "no-inherit-v2",
        observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=first.id,
    )

    repo.add_claim(claim)
    repo.add_evidence(first)
    repo.add_evidence(second)
    add_link(repo, claim, first)

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    assert result == ()


def test_successor_with_explicit_link_becomes_eligible():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("revision-relinked")

    first = make_evidence("relinked-v1")
    second = make_evidence(
        "relinked-v2",
        observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=first.id,
    )

    repo.add_claim(claim)
    repo.add_evidence(first)
    repo.add_evidence(second)

    add_link(repo, claim, first)
    add_link(
        repo,
        claim,
        second,
        created_at=datetime(2026, 9, 2, 10, 5, tzinfo=UTC),
    )

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    assert result == (
        (second, EdgeType.SUPPORTED_BY),
    )


def test_both_relation_types_round_trip():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("relations")
    support = make_evidence("support")
    contradict = make_evidence("contradict")

    repo.add_claim(claim)
    repo.add_evidence(support)
    repo.add_evidence(contradict)

    add_link(
        repo,
        claim,
        support,
        relation=EdgeType.SUPPORTED_BY,
    )
    add_link(
        repo,
        claim,
        contradict,
        relation=EdgeType.CONTRADICTED_BY,
    )

    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert set(result) == {
        (support, EdgeType.SUPPORTED_BY),
        (contradict, EdgeType.CONTRADICTED_BY),
    }


def test_result_order_is_deterministic():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("ordering")

    later = make_evidence(
        "later",
        published_at=datetime(2026, 9, 1, 9, 30, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
    )
    earlier = make_evidence(
        "earlier",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )

    repo.add_claim(claim)
    repo.add_evidence(later)
    repo.add_evidence(earlier)
    add_link(repo, claim, later)
    add_link(repo, claim, earlier)

    cutoff = datetime(2026, 9, 1, 12, tzinfo=UTC)

    first = repo.evidence_for_claim_at(claim.id, cutoff)
    second = repo.evidence_for_claim_at(claim.id, cutoff)

    assert first == second
    assert [item[0].id for item in first] == [
        earlier.id,
        later.id,
    ]


def test_naive_cutoff_rejected_before_connection(monkeypatch):
    repo = PostgreSQLEvidenceRepository(DSN)

    def forbidden_connect():
        raise AssertionError("database must not be touched")

    monkeypatch.setattr(repo, "connect", forbidden_connect)

    with pytest.raises(
        ValueError,
        match="research_cutoff must be timezone-aware",
    ):
        repo.evidence_for_claim_at(
            "claim:anything",
            datetime(2026, 9, 1, 12),
        )


def test_missing_claim_fails_closed():
    repo = PostgreSQLEvidenceRepository(DSN)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R501: CLAIM_NOT_FOUND",
    ):
        repo.evidence_for_claim_at(
            "claim:missing",
            datetime(2026, 9, 1, 12, tzinfo=UTC),
        )


def test_invalid_claim_prefix_rejected_before_connection(
    monkeypatch,
):
    repo = PostgreSQLEvidenceRepository(DSN)

    def forbidden_connect():
        raise AssertionError("database must not be touched")

    monkeypatch.setattr(repo, "connect", forbidden_connect)

    with pytest.raises(
        ValueError,
        match="claim_id must reference Claim",
    ):
        repo.evidence_for_claim_at(
            "evidence:not-a-claim",
            datetime(2026, 9, 1, 12, tzinfo=UTC),
        )


def test_read_path_does_not_mutate_persistent_state():
    repo, claim, _ = setup_pair()

    with connect() as con:
        before = (
            con.execute(
                "SELECT COUNT(*) FROM domain_nodes"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM evidence_facts"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM domain_edges"
            ).fetchone()[0],
        )

    repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    with connect() as con:
        after = (
            con.execute(
                "SELECT COUNT(*) FROM domain_nodes"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM evidence_facts"
            ).fetchone()[0],
            con.execute(
                "SELECT COUNT(*) FROM domain_edges"
            ).fetchone()[0],
        )

    assert after == before



def test_missing_predecessor_rejected_by_database():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("missing-predecessor")
    evidence = make_evidence("missing-predecessor")

    repo.add_claim(claim)
    repo.add_evidence(evidence)
    add_link(repo, claim, evidence)

    missing_id = "evidence:" + ("f" * 64)

    # Referential integrity must prevent a dangling revision
    # predecessor from entering persistent state.
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with connect() as con:
            con.execute(
                """
                UPDATE evidence_facts
                SET supersedes_id = %s
                WHERE node_id = %s
                """,
                (missing_id, evidence.id),
            )

    # Failed corruption must not damage the valid historical read.
    result = repo.evidence_for_claim_at(
        claim.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result == (
        (evidence, EdgeType.SUPPORTED_BY),
    )



def test_branching_revision_rejected_by_database():
    repo = PostgreSQLEvidenceRepository(DSN)

    root = make_evidence("branch-root")
    left = make_evidence(
        "branch-left",
        observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )
    right = make_evidence(
        "branch-right",
        observed_at=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, 30, tzinfo=UTC),
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(left)

    import psycopg

    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.add_evidence(right)

    # Failed competing successor must not leave a partial node.
    with connect() as con:
        right_node = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id = %s
            """,
            (right.id,),
        ).fetchone()[0]

        right_projection = con.execute(
            """
            SELECT COUNT(*)
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (right.id,),
        ).fetchone()[0]

    assert right_node == 0
    assert right_projection == 0


def test_corrupted_evidence_projection_fails_closed():
    repo, claim, evidence = setup_pair(
        "corrupt-evidence-projection"
    )

    # Corrupt a field that participates in canonical Evidence
    # identity while leaving the domain node untouched.
    with connect() as con:
        con.execute(
            """
            UPDATE evidence_facts
            SET source_version = %s
            WHERE node_id = %s
            """,
            ("CORRUPTED", evidence.id),
        )
        con.commit()

    with pytest.raises(Exception):
        repo.evidence_for_claim_at(
            claim.id,
            datetime(2026, 9, 1, 12, tzinfo=UTC),
        )
