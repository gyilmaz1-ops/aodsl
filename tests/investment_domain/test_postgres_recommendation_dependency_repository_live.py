import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Claim, Recommendation, Valuation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


UTC = timezone.utc
DSN = os.environ["IDM_TEST_POSTGRES_DSN"]


def _claim(seed: str) -> Claim:
    payload = {
        "subject_id": f"company:{seed}",
        "predicate": "revenue_growth",
        "object_value": seed,
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "COMPANY",
        "as_of": datetime(2026, 9, 30, tzinfo=UTC),
    }
    return Claim(
        id=canonical_id("claim", payload),
        **payload,
        created_by="Fundamental_Analyst",
    )


def _valuation() -> Valuation:
    payload = {
        "security_id": "security:nasdaq:nvda",
        "method": "DCF",
        "value": Decimal("250.00"),
        "currency": "USD",
        "as_of": datetime(2026, 9, 30, tzinfo=UTC),
        "model_version": "1",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def _recommendation(
    rationale_claim_ids: tuple[str, ...],
) -> Recommendation:
    payload = {
        "security_id": "security:nasdaq:nvda",
        "action": "BUY",
        "as_of": datetime(2026, 9, 30, 12, tzinfo=UTC),
        "created_by": "Investment_Committee_Chairman",
        "rationale_claim_ids": rationale_claim_ids,
    }
    return Recommendation(
        id=canonical_id("recommendation", payload),
        **payload,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_type = 'Recommendation'
                   OR target_type = 'Recommendation'
                """
            )
            con.execute("DELETE FROM recommendation_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type IN (
                    'Recommendation',
                    'Valuation',
                    'Claim'
                )
                """
            )

    return repository


def _rows(repo, recommendation_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Recommendation'
              AND edge_type = 'DEPENDS_ON'
            ORDER BY target_id
            """,
            (recommendation_id,),
        ).fetchall()


def _persist_base(repo):
    claim = _claim("r566")
    valuation = _valuation()
    recommendation = _recommendation((claim.id,))

    repo.add_claim(claim)
    repo.add_valuation(valuation)
    repo.add_recommendation(recommendation)

    return recommendation, claim, valuation


def test_recommendation_dependency_aggregate_is_persisted(repo):
    recommendation, claim, valuation = _persist_base(repo)

    repo.add_recommendation_dependencies(
        recommendation.id,
        (valuation.id, claim.id),
    )

    assert set(_rows(repo, recommendation.id)) == {
        (valuation.id, "Valuation"),
        (claim.id, "Claim"),
    }


def test_identical_recommendation_dependency_replay_is_idempotent(repo):
    recommendation, claim, valuation = _persist_base(repo)
    dependencies = (valuation.id, claim.id)

    repo.add_recommendation_dependencies(
        recommendation.id,
        dependencies,
    )
    repo.add_recommendation_dependencies(
        recommendation.id,
        dependencies,
    )

    assert len(_rows(repo, recommendation.id)) == 2


def test_missing_recommendation_source_fails_closed(repo):
    claim = _claim("missing-source")
    repo.add_claim(claim)

    recommendation = _recommendation((claim.id,))

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (claim.id,),
        )


def test_missing_recommendation_target_fails_without_partial_write(repo):
    recommendation, claim, valuation = _persist_base(repo)
    missing = "claim:missing-r566"

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (valuation.id, claim.id, missing),
        )

    assert _rows(repo, recommendation.id) == []


def test_wrong_recommendation_target_type_fails_closed(repo):
    recommendation, claim, _ = _persist_base(repo)
    evidence_id = "evidence:r566-wrong-target"

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
                VALUES (
                    %s,
                    'Evidence',
                    '{}'::jsonb,
                    repeat('0', 64)
                )
                """,
                (evidence_id,),
            )

    try:
        with pytest.raises(
            RepositoryWriteError,
            match="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
        ):
            repo.add_recommendation_dependencies(
                recommendation.id,
                (claim.id, evidence_id),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM domain_nodes WHERE id = %s",
                    (evidence_id,),
                )


def test_rationale_claim_subset_must_match_projection(repo):
    recommendation, claim, valuation = _persist_base(repo)
    extra_claim = _claim("extra-rationale")
    repo.add_claim(extra_claim)

    with pytest.raises(
        RepositoryWriteError,
        match=(
            "IDM-W553: "
            "RECOMMENDATION_RATIONALE_CLAIM_SET_MISMATCH"
        ),
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (valuation.id, claim.id, extra_claim.id),
        )

    assert _rows(repo, recommendation.id) == []


def test_missing_rationale_claim_dependency_fails_closed(repo):
    recommendation, _, valuation = _persist_base(repo)

    with pytest.raises(
        RepositoryWriteError,
        match=(
            "IDM-W553: "
            "RECOMMENDATION_RATIONALE_CLAIM_SET_MISMATCH"
        ),
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (valuation.id,),
        )

    assert _rows(repo, recommendation.id) == []


def test_existing_partial_dependency_set_is_not_repaired(repo):
    recommendation, claim, valuation = _persist_base(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type,
                    created_at
                )
                VALUES (
                    %s,
                    'Recommendation',
                    'DEPENDS_ON',
                    %s,
                    'Claim',
                    CURRENT_TIMESTAMP
                )
                """,
                (recommendation.id, claim.id),
            )

    before = _rows(repo, recommendation.id)

    with pytest.raises(
        RepositoryWriteError,
        match=(
            "IDM-W552: "
            "RECOMMENDATION_DEPENDENCY_SET_MISMATCH"
        ),
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (claim.id, valuation.id),
        )

    assert _rows(repo, recommendation.id) == before


def test_missing_recommendation_projection_blocks_dependency_write(repo):
    recommendation, claim, valuation = _persist_base(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM recommendation_facts
                WHERE node_id = %s
                """,
                (recommendation.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W549: RECOMMENDATION_PROJECTION_WRITE_LOST",
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (claim.id, valuation.id),
        )

    assert _rows(repo, recommendation.id) == []


def test_recommendation_projection_mismatch_blocks_dependency_write(repo):
    recommendation, claim, valuation = _persist_base(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE recommendation_facts
                SET action = 'SELL'
                WHERE node_id = %s
                """,
                (recommendation.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W550: RECOMMENDATION_PROJECTION_MISMATCH",
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (claim.id, valuation.id),
        )

    assert _rows(repo, recommendation.id) == []


def test_empty_recommendation_dependency_set_is_rejected(repo):
    recommendation, _, _ = _persist_base(repo)

    with pytest.raises(
        ValueError,
        match="dependency_ids must be a non-empty tuple",
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (),
        )


def test_duplicate_recommendation_dependencies_are_rejected(repo):
    recommendation, claim, _ = _persist_base(repo)

    with pytest.raises(
        ValueError,
        match="dependency_ids must not contain duplicates",
    ):
        repo.add_recommendation_dependencies(
            recommendation.id,
            (claim.id, claim.id),
        )


def test_noncanonical_recommendation_source_id_is_rejected(repo):
    _, claim, _ = _persist_base(repo)

    with pytest.raises(
        ValueError,
        match="recommendation_id must be a canonical Recommendation ID",
    ):
        repo.add_recommendation_dependencies(
            "recommendation:not-canonical",
            (claim.id,),
        )
