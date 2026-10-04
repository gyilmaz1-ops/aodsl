from datetime import datetime, timezone
from decimal import Decimal
import os

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def estimate(
    seed,
    *,
    subject_id="company:acme",
    metric_name="financial.revenue",
    period_end=None,
    value=None,
    unit="currency",
    scenario="BASE",
    model_version="estimate-model-v1",
    as_of=None,
    currency="USD",
):
    values = {
        "subject_id": subject_id,
        "metric_name": metric_name,
        "period_end": period_end or utc(2027, 12, 31),
        "value": value or Decimal("100"),
        "unit": unit,
        "scenario": scenario,
        "model_version": model_version,
        "as_of": as_of or utc(2026, 10, 1, 10),
        "currency": currency,
    }

    return Estimate(
        id=canonical_id("estimate", values),
        **values,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM estimate_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = 'Estimate'"
            )

    return repository


def test_estimate_at_round_trips_visible_node(repo):
    node = estimate(
        "visible",
        as_of=utc(2026, 10, 1, 10),
    )
    repo.add_estimate(node)

    assert repo.estimate_at(
        node.id,
        utc(2026, 10, 1, 12),
    ) == node


def test_estimate_at_hides_valid_future_node(repo):
    node = estimate(
        "future",
        as_of=utc(2026, 10, 1, 13),
    )
    repo.add_estimate(node)

    assert repo.estimate_at(
        node.id,
        utc(2026, 10, 1, 12),
    ) is None


def test_estimate_at_is_visible_at_exact_as_of(repo):
    node = estimate(
        "boundary",
        as_of=utc(2026, 10, 1, 12),
    )
    repo.add_estimate(node)

    assert repo.estimate_at(
        node.id,
        utc(2026, 10, 1, 12),
    ) == node


def test_estimate_at_returns_none_when_anchor_is_missing(repo):
    node = estimate("missing")

    assert repo.estimate_at(
        node.id,
        utc(2026, 10, 1, 12),
    ) is None


def test_estimate_at_rejects_noncanonical_id(repo):
    with pytest.raises(ValueError):
        repo.estimate_at(
            "estimate:not-a-canonical-content-id",
            utc(2026, 10, 1, 12),
        )


def test_estimate_at_rejects_naive_cutoff(repo):
    node = estimate("naive")

    with pytest.raises(ValueError, match="timezone-aware"):
        repo.estimate_at(
            node.id,
            datetime(2026, 10, 1, 12),
        )


def test_latest_estimate_at_returns_latest_visible_revision(repo):
    older = estimate(
        "older",
        value=Decimal("100"),
        as_of=utc(2026, 10, 1, 10),
    )
    newer = estimate(
        "newer",
        value=Decimal("120"),
        as_of=utc(2026, 10, 1, 11),
    )

    repo.add_estimate(older)
    repo.add_estimate(newer)

    assert repo.latest_estimate_at(
        newer.subject_id,
        newer.metric_name,
        newer.period_end,
        newer.scenario,
        newer.model_version,
        utc(2026, 10, 1, 12),
    ) == newer


def test_latest_estimate_at_ignores_future_revision(repo):
    visible = estimate(
        "visible-latest",
        value=Decimal("100"),
        as_of=utc(2026, 10, 1, 11),
    )
    future = estimate(
        "future-latest",
        value=Decimal("130"),
        as_of=utc(2026, 10, 1, 13),
    )

    repo.add_estimate(visible)
    repo.add_estimate(future)

    assert repo.latest_estimate_at(
        visible.subject_id,
        visible.metric_name,
        visible.period_end,
        visible.scenario,
        visible.model_version,
        utc(2026, 10, 1, 12),
    ) == visible


def test_latest_estimate_at_fails_closed_on_same_time_ambiguity(repo):
    first = estimate(
        "ambiguous-1",
        value=Decimal("100"),
        as_of=utc(2026, 10, 1, 11),
    )
    second = estimate(
        "ambiguous-2",
        value=Decimal("110"),
        as_of=utc(2026, 10, 1, 11),
    )

    assert first.id != second.id

    repo.add_estimate(first)
    repo.add_estimate(second)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R587: ESTIMATE_PIT_AMBIGUITY",
    ):
        repo.latest_estimate_at(
            first.subject_id,
            first.metric_name,
            first.period_end,
            first.scenario,
            first.model_version,
            utc(2026, 10, 1, 12),
        )


def test_future_exact_estimate_checks_integrity_before_cutoff(repo):
    node = estimate(
        "future-corrupted",
        as_of=utc(2026, 10, 1, 13),
    )
    repo.add_estimate(node)

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
        match="IDM-R585: ESTIMATE_INTEGRITY_FAILURE",
    ):
        repo.estimate_at(
            node.id,
            utc(2026, 10, 1, 12),
        )


def test_latest_estimate_at_does_not_integrity_traverse_future_revision(repo):
    visible = estimate(
        "visible-good",
        value=Decimal("100"),
        as_of=utc(2026, 10, 1, 11),
    )
    future = estimate(
        "future-corrupted",
        value=Decimal("130"),
        as_of=utc(2026, 10, 1, 13),
    )

    repo.add_estimate(visible)
    repo.add_estimate(future)

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

    assert repo.latest_estimate_at(
        visible.subject_id,
        visible.metric_name,
        visible.period_end,
        visible.scenario,
        visible.model_version,
        utc(2026, 10, 1, 12),
    ) == visible
