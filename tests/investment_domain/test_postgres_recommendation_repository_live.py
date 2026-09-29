from datetime import datetime, timezone

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Recommendation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository


DSN = "postgresql://aodsl:aodsl_cert@127.0.0.1:55433/aodsl_cert"


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM recommendation_facts
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type = 'Recommendation'
                """
            )

    return repository


def make_recommendation() -> Recommendation:
    kwargs = {
        "security_id": "security:TEST",
        "action": "BUY",
        "as_of": datetime(
            2026,
            9,
            30,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        "created_by": "Investment_Committee_Chairman",
        "rationale_claim_ids": (
            "claim:R565-A",
            "claim:R565-B",
        ),
    }

    return Recommendation(
        id=canonical_id("recommendation", kwargs),
        **kwargs,
    )


def test_add_recommendation_persists_domain_node_and_projection(repo):
    recommendation = make_recommendation()

    repo.add_recommendation(recommendation)

    with repo.connect() as con:
        node_row = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (recommendation.id,),
        ).fetchone()

        projection_row = con.execute(
            """
            SELECT
                node_type,
                security_id,
                action,
                as_of,
                created_by,
                rationale_claim_ids
            FROM recommendation_facts
            WHERE node_id = %s
            """,
            (recommendation.id,),
        ).fetchone()

    assert node_row is not None
    assert str(node_row[0]) == "Recommendation"

    assert projection_row is not None
    assert (
        str(projection_row[0]),
        str(projection_row[1]),
        str(projection_row[2]),
        projection_row[3],
        str(projection_row[4]),
        tuple(projection_row[5]),
    ) == (
        "Recommendation",
        recommendation.security_id,
        recommendation.action,
        recommendation.as_of,
        recommendation.created_by,
        recommendation.rationale_claim_ids,
    )


def test_add_recommendation_exact_replay_is_idempotent(repo):
    recommendation = make_recommendation()

    repo.add_recommendation(recommendation)
    repo.add_recommendation(recommendation)

    with repo.connect() as con:
        node_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id = %s
            """,
            (recommendation.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            """
            SELECT COUNT(*)
            FROM recommendation_facts
            WHERE node_id = %s
            """,
            (recommendation.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


def test_add_recommendation_rejects_missing_projection(repo):
    recommendation = make_recommendation()
    repo.add_recommendation(recommendation)

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
        Exception,
        match="IDM-W549: RECOMMENDATION_PROJECTION_WRITE_LOST",
    ):
        repo.add_recommendation(recommendation)


def test_add_recommendation_rejects_projection_mismatch(repo):
    recommendation = make_recommendation()
    repo.add_recommendation(recommendation)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE recommendation_facts
                SET action = %s
                WHERE node_id = %s
                """,
                (
                    "SELL",
                    recommendation.id,
                ),
            )

    with pytest.raises(
        Exception,
        match="IDM-W550: RECOMMENDATION_PROJECTION_MISMATCH",
    ):
        repo.add_recommendation(recommendation)


def test_add_recommendation_rejects_orphan_projection(repo):
    recommendation = make_recommendation()
    repo.add_recommendation(recommendation)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE recommendation_facts
                DROP CONSTRAINT recommendation_domain_node_fk
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id = %s
                """,
                (recommendation.id,),
            )

    try:
        with pytest.raises(
            Exception,
            match="IDM-W551: RECOMMENDATION_ORPHAN_PROJECTION",
        ):
            repo.add_recommendation(recommendation)
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DELETE FROM recommendation_facts
                    WHERE node_id = %s
                    """,
                    (recommendation.id,),
                )
                con.execute(
                    """
                    ALTER TABLE recommendation_facts
                    ADD CONSTRAINT recommendation_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )
