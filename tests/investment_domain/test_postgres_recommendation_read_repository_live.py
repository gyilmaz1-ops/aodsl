from datetime import datetime, timezone

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Recommendation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


DSN = "postgresql://aodsl:aodsl_cert@127.0.0.1:55433/aodsl_cert"


def utc(year, month, day, hour):
    return datetime(
        year,
        month,
        day,
        hour,
        0,
        tzinfo=timezone.utc,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM recommendation_facts")
            con.execute(
                """                 DELETE FROM domain_nodes
                WHERE node_type = 'Recommendation'
                """
            )

    return repository


def recommendation(
    seed,
    *,
    security_id="security:TEST",
    as_of=None,
):
    kwargs = {
        "security_id": security_id,
        "action": "BUY",
            "created_by": "Investment_Committee_Chairman_missing",
            "rationale_claim_ids": (),
        "created_by": f"Investment_Committee_Chairman_{seed}",
        "rationale_claim_ids": (),
        "as_of": as_of or utc(2026, 9, 30, 12),
    }
    return Recommendation(
        id=canonical_id("recommendation", kwargs),
        **kwargs,
    )


def test_recommendation_at_round_trips_visible_node(repo):
    node = recommendation("visible", as_of=utc(2026, 9, 30, 11))
    repo.add_recommendation(node)

    assert repo.recommendation_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_recommendation_at_hides_future_assessment(repo):
    node = recommendation("future", as_of=utc(2026, 9, 30, 13))
    repo.add_recommendation(node)

    assert repo.recommendation_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) is None


def test_recommendation_at_is_visible_at_exact_as_of(repo):
    node = recommendation("boundary", as_of=utc(2026, 9, 30, 12))
    repo.add_recommendation(node)

    assert repo.recommendation_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_recommendation_at_returns_none_when_anchor_is_missing(repo):
    missing = canonical_id(
        "recommendation",
        {
            "security_id": "security:MISSING",
            "action": "BUY",
            "created_by": "Investment_Committee_Chairman_missing",
            "rationale_claim_ids": (),
            "as_of": utc(2026, 9, 30, 11),
        },
    )

    assert repo.recommendation_at(
        missing,
        utc(2026, 9, 30, 12),
    ) is None


def test_recommendation_at_rejects_noncanonical_id_before_database(repo):
    with pytest.raises(ValueError):
        repo.recommendation_at(
            "risk:not-a-canonical-content-id",
            utc(2026, 9, 30, 12),
        )


def test_recommendation_at_rejects_naive_cutoff_before_database(repo):
    node = recommendation("naive-cutoff")

    with pytest.raises(ValueError, match="timezone-aware"):
        repo.recommendation_at(
            node.id,
            datetime(2026, 9, 30, 12),
        )


def test_latest_recommendation_at_returns_none_when_subject_has_no_assessment(repo):
    assert repo.latest_recommendation_at(
        "security:MISSING",
        utc(2026, 9, 30, 12),
    ) is None


def test_latest_recommendation_at_returns_latest_visible_assessment(repo):
    security_id = "security:LATEST"

    older = recommendation(
        "latest-older",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 10),
    )
    newer = recommendation(
        "latest-newer",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_recommendation(older)
    repo.add_recommendation(newer)

    assert repo.latest_recommendation_at(
        security_id,
        utc(2026, 9, 30, 12),
    ) == newer


def test_latest_recommendation_at_ignores_future_assessment_for_selection(repo):
    security_id = "security:FUTURE-SELECTION"

    visible = recommendation(
        "future-selection-visible",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = recommendation(
        "future-selection-future",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_recommendation(visible)
    repo.add_recommendation(future)

    assert repo.latest_recommendation_at(
        security_id,
        utc(2026, 9, 30, 12),
    ) == visible


def test_latest_recommendation_at_exact_cutoff_is_visible(repo):
    node = recommendation(
        "latest-boundary",
        security_id="security:BOUNDARY",
        as_of=utc(2026, 9, 30, 12),
    )
    repo.add_recommendation(node)

    assert repo.latest_recommendation_at(
        node.security_id,
        utc(2026, 9, 30, 12),
    ) == node


def test_latest_recommendation_at_rejects_naive_cutoff(repo):
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.latest_recommendation_at(
            "security:TEST",
            datetime(2026, 9, 30, 12),
        )


def test_latest_recommendation_at_rejects_empty_security_id(repo):
    with pytest.raises(
        ValueError,
        match="security_id must not be empty",
    ):
        repo.latest_recommendation_at(
            "",
            utc(2026, 9, 30, 12),
        )


def test_latest_recommendation_at_accepts_noncanonical_business_reference_id(repo):
    node = recommendation(
        "business-reference",
        security_id="security:business-key",
        as_of=utc(2026, 9, 30, 11),
    )
    repo.add_recommendation(node)

    assert repo.latest_recommendation_at(
        "security:business-key",
        utc(2026, 9, 30, 12),
    ) == node



def test_recommendation_at_fails_closed_when_projection_is_missing(repo):
    node = recommendation("missing-projection")
    repo.add_recommendation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM recommendation_facts
                WHERE node_id = %s
                """,
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R566: RECOMMENDATION_PROJECTION_NOT_FOUND",
    ):
        repo.recommendation_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_recommendation_at_fails_closed_on_projection_tampering(repo):
    node = recommendation("projection-tamper")
    repo.add_recommendation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE recommendation_facts
                SET action = %s
                WHERE node_id = %s
                """,
                ("", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R564: INVALID_STORED_RECOMMENDATION",
    ):
        repo.recommendation_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_recommendation_at_fails_closed_on_canonical_payload_tampering(repo):
    node = recommendation("canonical-payload-tamper")
    repo.add_recommendation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{action}',
                        to_jsonb(%s::text)
                    )
                WHERE id = %s
                """,
                ("Tampered canonical recommendation", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R565: RECOMMENDATION_INTEGRITY_FAILURE",
    ):
        repo.recommendation_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_recommendation_at_fails_closed_on_payload_hash_tampering(repo):
    node = recommendation("hash-tamper")
    repo.add_recommendation(node)

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
        match="IDM-R565: RECOMMENDATION_INTEGRITY_FAILURE",
    ):
        repo.recommendation_at(
            node.id,
            utc(2026, 9, 30, 12),
        )



def test_recommendation_at_fails_closed_on_future_anchor_integrity_failure(repo):
    node = recommendation(
        "future-anchor-integrity",
        as_of=utc(2026, 9, 30, 13),
    )
    repo.add_recommendation(node)

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
        match="IDM-R565: RECOMMENDATION_INTEGRITY_FAILURE",
    ):
        repo.recommendation_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_latest_recommendation_at_fails_closed_on_latest_as_of_tie(repo):
    security_id = "security:TIE"

    first = recommendation(
        "tie-first",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    second = recommendation(
        "tie-second",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_recommendation(first)
    repo.add_recommendation(second)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R567: RECOMMENDATION_PIT_AMBIGUITY",
    ):
        repo.latest_recommendation_at(
            security_id,
            utc(2026, 9, 30, 12),
        )


def test_latest_recommendation_at_does_not_look_ahead_into_future_integrity(repo):
    security_id = "security:NO-LOOK-AHEAD"

    visible = recommendation(
        "no-look-ahead-visible",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = recommendation(
        "no-look-ahead-future",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_recommendation(visible)
    repo.add_recommendation(future)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, future.id),
            )

    assert repo.latest_recommendation_at(
        security_id,
        utc(2026, 9, 30, 12),
    ) == visible

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R565: RECOMMENDATION_INTEGRITY_FAILURE",
    ):
        repo.recommendation_at(
            future.id,
            utc(2026, 9, 30, 12),
        )


def test_latest_recommendation_at_fails_closed_on_selected_anchor_integrity(repo):
    security_id = "security:SELECTED-INTEGRITY"

    selected = recommendation(
        "selected-integrity",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    repo.add_recommendation(selected)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, selected.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R565: RECOMMENDATION_INTEGRITY_FAILURE",
    ):
        repo.latest_recommendation_at(
            security_id,
            utc(2026, 9, 30, 12),
        )



def test_recommendation_at_fails_closed_on_wrong_anchor_type(repo):
    node = recommendation("wrong-anchor-type")
    repo.add_recommendation(node)

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
                UPDATE domain_nodes
                SET node_type = 'Claim'
                WHERE id = %s
                """,
                (node.id,),
            )

    try:
        with pytest.raises(
            RepositoryReadError,
            match="IDM-R563: RECOMMENDATION_TYPE_MISMATCH",
        ):
            repo.recommendation_at(
                node.id,
                utc(2026, 9, 30, 12),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Recommendation'
                    WHERE id = %s
                    """,
                    (node.id,),
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


def test_recommendation_at_fails_closed_on_wrong_projection_type(repo):
    node = recommendation("wrong-projection-type")
    repo.add_recommendation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE recommendation_facts
                DROP CONSTRAINT recommendation_node_type
                """
            )
            con.execute(
                """
                ALTER TABLE recommendation_facts
                DROP CONSTRAINT recommendation_domain_node_fk
                """
            )
            con.execute(
                """
                UPDATE recommendation_facts
                SET node_type = 'Claim'
                WHERE node_id = %s
                """,
                (node.id,),
            )

    try:
        with pytest.raises(
            RepositoryReadError,
            match="IDM-R563: RECOMMENDATION_TYPE_MISMATCH",
        ):
            repo.recommendation_at(
                node.id,
                utc(2026, 9, 30, 12),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE recommendation_facts
                    SET node_type = 'Recommendation'
                    WHERE node_id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE recommendation_facts
                    ADD CONSTRAINT recommendation_node_type
                    CHECK (node_type = 'Recommendation')
                    """
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
