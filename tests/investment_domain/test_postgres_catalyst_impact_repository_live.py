from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import CatalystImpact
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


UTC = timezone.utc
DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=UTC,
    )


def impact(seed="repository", **overrides):
    values = {
        "catalyst_id": canonical_id(
            "catalyst",
            {"seed": seed},
        ),
        "target_id": canonical_id(
            "claim",
            {"seed": seed},
        ),
        "direction": "POSITIVE",
        "magnitude": "HIGH",
        "probability": Decimal("0.75"),
        "confidence": Decimal("0.80"),
        "horizon": "NEAR_TERM",
        "rationale": f"Catalyst impact assessment {seed}",
        "as_of": utc(2026, 9, 27, 9),
        "created_by": "test-agent",
    }
    values.update(overrides)

    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or canonical_id(
        "catalyst_impact",
        values,
    )
    return CatalystImpact(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_database(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")


def test_add_catalyst_impact_persists_domain_node_and_projection(repo):
    node = impact("write")

    repo.add_catalyst_impact(node)

    with repo.connect() as con:
        anchor = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (node.id,),
        ).fetchone()

        projection = con.execute(
            """
            SELECT
                node_type,
                catalyst_id,
                target_id,
                direction,
                magnitude,
                probability,
                confidence,
                horizon,
                rationale,
                as_of,
                created_by
            FROM catalyst_impact_facts
            WHERE node_id = %s
            """,
            (node.id,),
        ).fetchone()

    assert anchor == ("CatalystImpact",)
    assert projection == (
        "CatalystImpact",
        node.catalyst_id,
        node.target_id,
        node.direction,
        node.magnitude,
        node.probability,
        node.confidence,
        node.horizon,
        node.rationale,
        node.as_of,
        node.created_by,
    )


def test_add_catalyst_impact_is_idempotent(repo):
    node = impact("idempotent")

    repo.add_catalyst_impact(node)
    repo.add_catalyst_impact(node)

    with repo.connect() as con:
        anchor_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id = %s
            """,
            (node.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            """
            SELECT COUNT(*)
            FROM catalyst_impact_facts
            WHERE node_id = %s
            """,
            (node.id,),
        ).fetchone()[0]

    assert anchor_count == 1
    assert projection_count == 1


def test_catalyst_impact_at_round_trips_visible_node(repo):
    node = impact("visible")

    repo.add_catalyst_impact(node)

    assert repo.catalyst_impact_at(
        node.id,
        utc(2026, 9, 27, 10),
    ) == node


def test_catalyst_impact_at_hides_future_assessment(repo):
    node = impact(
        "future",
        as_of=utc(2026, 9, 27, 13),
    )

    repo.add_catalyst_impact(node)

    assert repo.catalyst_impact_at(
        node.id,
        utc(2026, 9, 27, 12),
    ) is None


def test_catalyst_impact_at_is_visible_at_exact_as_of(repo):
    node = impact(
        "boundary",
        as_of=utc(2026, 9, 27, 12),
    )

    repo.add_catalyst_impact(node)

    assert repo.catalyst_impact_at(
        node.id,
        utc(2026, 9, 27, 12),
    ) == node


def test_multiple_assessments_for_same_pair_remain_distinct(repo):
    first = impact(
        "pair-first",
        catalyst_id="catalyst:shared",
        target_id="claim:shared",
        as_of=utc(2026, 9, 27, 9),
        probability=Decimal("0.60"),
    )
    second = impact(
        "pair-second",
        catalyst_id="catalyst:shared",
        target_id="claim:shared",
        as_of=utc(2026, 9, 27, 11),
        probability=Decimal("0.85"),
    )

    repo.add_catalyst_impact(first)
    repo.add_catalyst_impact(second)

    assert first.id != second.id

    with repo.connect() as con:
        rows = con.execute(
            """
            SELECT node_id
            FROM catalyst_impact_facts
            WHERE catalyst_id = %s
              AND target_id = %s
            ORDER BY as_of, node_id
            """,
            ("catalyst:shared", "claim:shared"),
        ).fetchall()

    assert rows == [(first.id,), (second.id,)]


def test_catalyst_impact_at_rejects_noncanonical_id_before_database(repo):
    with pytest.raises(ValueError):
        repo.catalyst_impact_at(
            "catalyst_impact:not-a-canonical-content-id",
            utc(2026, 9, 27, 12),
        )


def test_catalyst_impact_at_rejects_naive_cutoff_before_database(repo):
    node = impact("naive-cutoff")

    with pytest.raises(ValueError):
        repo.catalyst_impact_at(
            node.id,
            datetime(2026, 9, 27, 12),
        )


def test_catalyst_impact_at_fails_closed_when_projection_is_missing(repo):
    node = impact("missing-projection")
    repo.add_catalyst_impact(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM catalyst_impact_facts
                WHERE node_id = %s
                """,
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R543: CATALYST_IMPACT_PROJECTION_NOT_FOUND",
    ):
        repo.catalyst_impact_at(
            node.id,
            utc(2026, 9, 27, 12),
        )


def test_catalyst_impact_at_fails_closed_on_projection_tampering(repo):
    node = impact("projection-tamper")
    repo.add_catalyst_impact(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE catalyst_impact_facts
                SET rationale = %s
                WHERE node_id = %s
                """,
                ("tampered rationale", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R541: INVALID_STORED_CATALYST_IMPACT",
    ):
        repo.catalyst_impact_at(
            node.id,
            utc(2026, 9, 27, 12),
        )


def test_catalyst_impact_at_fails_closed_on_canonical_payload_tampering(repo):
    node = impact("payload-tamper")
    repo.add_catalyst_impact(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{rationale}',
                        to_jsonb(%s::text)
                    )
                WHERE id = %s
                """,
                ("tampered canonical payload", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R542: CATALYST_IMPACT_INTEGRITY_FAILURE",
    ):
        repo.catalyst_impact_at(
            node.id,
            utc(2026, 9, 27, 12),
        )


def test_catalyst_impact_at_fails_closed_on_payload_hash_tampering(repo):
    node = impact("hash-tamper")
    repo.add_catalyst_impact(node)

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
        match="IDM-R542: CATALYST_IMPACT_INTEGRITY_FAILURE",
    ):
        repo.catalyst_impact_at(
            node.id,
            utc(2026, 9, 27, 12),
        )


def test_catalyst_impact_at_fails_closed_on_future_anchor_integrity_failure(repo):
    node = impact(
        "future-anchor-integrity",
        as_of=utc(2026, 9, 27, 13),
    )
    repo.add_catalyst_impact(node)

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
        match="IDM-R542: CATALYST_IMPACT_INTEGRITY_FAILURE",
    ):
        repo.catalyst_impact_at(
            node.id,
            utc(2026, 9, 27, 12),
        )


def test_latest_catalyst_impact_at_returns_none_when_pair_has_no_assessment(repo):
    assert (
        repo.latest_catalyst_impact_at(
            canonical_id("catalyst", {"seed": "missing"}),
            canonical_id("claim", {"seed": "missing"}),
            utc(2026, 9, 27, 12),
        )
        is None
    )


def test_latest_catalyst_impact_at_returns_latest_visible_assessment(repo):
    catalyst_id = canonical_id(
        "catalyst",
        {"seed": "pair-latest"},
    )
    target_id = canonical_id(
        "claim",
        {"seed": "pair-latest"},
    )

    older = impact(
        "pair-latest-older",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 10),
    )
    newer = impact(
        "pair-latest-newer",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 11),
    )

    repo.add_catalyst_impact(older)
    repo.add_catalyst_impact(newer)

    actual = repo.latest_catalyst_impact_at(
        older.catalyst_id,
        older.target_id,
        utc(2026, 9, 27, 12),
    )

    assert actual == newer


def test_latest_catalyst_impact_at_ignores_future_assessment_for_selection(repo):
    catalyst_id = canonical_id(
        "catalyst",
        {"seed": "pair-future-selection"},
    )
    target_id = canonical_id(
        "claim",
        {"seed": "pair-future-selection"},
    )

    visible = impact(
        "pair-visible",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 11),
    )
    future = impact(
        "pair-future",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 13),
    )

    repo.add_catalyst_impact(visible)
    repo.add_catalyst_impact(future)

    actual = repo.latest_catalyst_impact_at(
        visible.catalyst_id,
        visible.target_id,
        utc(2026, 9, 27, 12),
    )

    assert actual == visible


def test_latest_catalyst_impact_at_exact_cutoff_is_visible(repo):
    exact = impact(
        "pair-exact",
        as_of=utc(2026, 9, 27, 12),
    )

    repo.add_catalyst_impact(exact)

    actual = repo.latest_catalyst_impact_at(
        exact.catalyst_id,
        exact.target_id,
        utc(2026, 9, 27, 12),
    )

    assert actual == exact


def test_latest_catalyst_impact_at_fails_closed_on_latest_as_of_tie(repo):
    catalyst_id = canonical_id(
        "catalyst",
        {"seed": "pair-tie"},
    )
    target_id = canonical_id(
        "claim",
        {"seed": "pair-tie"},
    )

    first = impact(
        "pair-tie-first",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 11),
    )
    second = impact(
        "pair-tie-second",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 11),
    )

    repo.add_catalyst_impact(first)
    repo.add_catalyst_impact(second)

    assert first.id != second.id

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R544: CATALYST_IMPACT_PIT_AMBIGUITY",
    ):
        repo.latest_catalyst_impact_at(
            first.catalyst_id,
            first.target_id,
            utc(2026, 9, 27, 12),
        )


def test_latest_catalyst_impact_at_rejects_naive_cutoff(repo):
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.latest_catalyst_impact_at(
            canonical_id("catalyst", {"seed": "any"}),
            canonical_id("claim", {"seed": "any"}),
            datetime(2026, 9, 27, 12),
        )



def test_latest_catalyst_impact_at_rejects_empty_catalyst_id(repo):
    with pytest.raises(
        ValueError,
        match="catalyst_id must not be empty",
    ):
        repo.latest_catalyst_impact_at(
            "",
            canonical_id("claim", {"seed": "target"}),
            utc(2026, 9, 27, 12),
        )


def test_latest_catalyst_impact_at_rejects_empty_target_id(repo):
    with pytest.raises(
        ValueError,
        match="target_id must not be empty",
    ):
        repo.latest_catalyst_impact_at(
            canonical_id("catalyst", {"seed": "source"}),
            "",
            utc(2026, 9, 27, 12),
        )


def test_latest_catalyst_impact_at_accepts_noncanonical_business_reference_ids(
    repo,
):
    node = impact(
        "business-reference-ids",
        catalyst_id="catalyst:business-key",
        target_id="claim:business-key",
        as_of=utc(2026, 9, 27, 11),
    )
    repo.add_catalyst_impact(node)

    assert repo.latest_catalyst_impact_at(
        "catalyst:business-key",
        "claim:business-key",
        utc(2026, 9, 27, 12),
    ) == node


def test_latest_catalyst_impact_at_does_not_look_ahead_into_future_assessment_integrity(
    repo,
):
    catalyst_id = canonical_id(
        "catalyst",
        {"seed": "pair-future-integrity"},
    )
    target_id = canonical_id(
        "claim",
        {"seed": "pair-future-integrity"},
    )

    visible = impact(
        "pair-future-integrity-visible",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 11),
    )
    future = impact(
        "pair-future-integrity-future",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 13),
    )

    repo.add_catalyst_impact(visible)
    repo.add_catalyst_impact(future)

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

    assert repo.latest_catalyst_impact_at(
        catalyst_id,
        target_id,
        utc(2026, 9, 27, 12),
    ) == visible

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R542: CATALYST_IMPACT_INTEGRITY_FAILURE",
    ):
        repo.catalyst_impact_at(
            future.id,
            utc(2026, 9, 27, 12),
        )

def test_latest_catalyst_impact_at_fails_closed_on_selected_anchor_integrity_failure(
    repo,
):
    catalyst_id = canonical_id(
        "catalyst",
        {"seed": "pair-selected-anchor-integrity"},
    )
    target_id = canonical_id(
        "claim",
        {"seed": "pair-selected-anchor-integrity"},
    )

    selected = impact(
        "pair-selected-anchor-integrity",
        catalyst_id=catalyst_id,
        target_id=target_id,
        as_of=utc(2026, 9, 27, 11),
    )
    repo.add_catalyst_impact(selected)

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
        match="IDM-R542: CATALYST_IMPACT_INTEGRITY_FAILURE",
    ):
        repo.latest_catalyst_impact_at(
            catalyst_id,
            target_id,
            utc(2026, 9, 27, 12),
        )
