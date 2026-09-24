from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Metric
from investment_domain.postgres_migrations import (
    PostgreSQLMigrationManager,
)
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


def _metric_id(values):
    return canonical_id(
        "metric",
        {
            "subject_id": values["subject_id"],
            "name": values["name"],
            "period_start": values["period_start"],
            "period_end": values["period_end"],
            "effective_at": values["effective_at"],
            "observed_at": values["observed_at"],
            "published_at": values["published_at"],
            "source_id": values["source_id"],
            "source_version": values["source_version"],
        },
    )


def revenue_metric(seed="active", **overrides):
    values = {
        "subject_id": f"company:{seed}",
        "name": "financial.revenue",
        "value": Decimal("100.00"),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 1, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 7, 5),
        "published_at": utc(2026, 7, 5),
        "ingested_at": utc(2026, 7, 10),
        "source_id": f"source:{seed}",
        "source_version": "1",
        "supersedes_id": None,
    }
    values.update(overrides)
    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _metric_id(values)
    return Metric(**values)


def restatement(predecessor, **overrides):
    values = {
        "subject_id": predecessor.subject_id,
        "name": predecessor.name,
        "value": Decimal("105.00"),
        "unit": predecessor.unit,
        "currency": predecessor.currency,
        "period_start": predecessor.period_start,
        "period_end": predecessor.period_end,
        "effective_at": predecessor.effective_at,
        "observed_at": utc(2026, 8, 15),
        "published_at": utc(2026, 8, 15),
        "ingested_at": utc(2026, 8, 20),
        "source_id": predecessor.source_id,
        "source_version": "2",
        "supersedes_id": predecessor.id,
    }
    values.update(overrides)
    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _metric_id(values)
    return Metric(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_database(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")


def setup_lineage(repo, seed="active"):
    root = revenue_metric(seed)
    successor = restatement(root)

    repo.add_metric(root)
    repo.add_metric(successor)

    return root, successor


def test_missing_metric_anchor_is_r506(repo):
    with pytest.raises(
        RepositoryReadError,
        match="IDM-R506: METRIC_NOT_FOUND",
    ):
        repo.active_metric_at(
            "metric:missing",
            utc(2026, 9, 1),
        )


def test_naive_cutoff_is_rejected_before_database_access(repo):
    with pytest.raises(
        ValueError,
        match="research_cutoff must be timezone-aware",
    ):
        repo.active_metric_at(
            "metric:missing",
            datetime(2026, 9, 1),
        )


def test_invalid_metric_id_is_rejected(repo):
    with pytest.raises(
        ValueError,
        match="metric_id must reference Metric",
    ):
        repo.active_metric_at(
            "claim:not-a-metric",
            utc(2026, 9, 1),
        )


def test_root_only_before_visibility_returns_none(repo):
    root = revenue_metric("root-before")
    repo.add_metric(root)

    assert repo.active_metric_at(
        root.id,
        utc(2026, 7, 9),
    ) is None


def test_root_only_after_visibility_returns_root(repo):
    root = revenue_metric("root-after")
    repo.add_metric(root)

    assert repo.active_metric_at(
        root.id,
        utc(2026, 7, 11),
    ) == root


@pytest.mark.parametrize("anchor", ["root", "successor"])
def test_historical_cutoff_resolves_root_from_any_lineage_member(
    repo,
    anchor,
):
    root, successor = setup_lineage(repo, f"historical-{anchor}")

    anchor_id = (
        root.id
        if anchor == "root"
        else successor.id
    )

    assert repo.active_metric_at(
        anchor_id,
        utc(2026, 8, 1),
    ) == root


@pytest.mark.parametrize("anchor", ["root", "successor"])
def test_later_cutoff_resolves_successor_from_any_lineage_member(
    repo,
    anchor,
):
    root, successor = setup_lineage(repo, f"current-{anchor}")

    anchor_id = (
        root.id
        if anchor == "root"
        else successor.id
    )

    assert repo.active_metric_at(
        anchor_id,
        utc(2026, 8, 21),
    ) == successor


def test_effective_at_is_not_an_additional_visibility_gate(repo):
    root = revenue_metric(
        "effective-neutral",
        effective_at=utc(2026, 7, 5),
        observed_at=utc(2026, 7, 5),
        published_at=utc(2026, 7, 5),
        ingested_at=utc(2026, 7, 10),
    )
    repo.add_metric(root)

    assert repo.active_metric_at(
        root.id,
        utc(2026, 7, 11),
    ) == root


def test_corrupt_lineage_member_payload_fails_closed(repo):
    root, successor = setup_lineage(
        repo,
        "corrupt-member-payload",
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    canonical_payload - 'published_at'
                WHERE id = %s
                """,
                (successor.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R509: INVALID_STORED_METRIC_PAYLOAD",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )


def test_corrupt_lineage_member_hash_fails_closed(repo):
    root, successor = setup_lineage(
        repo,
        "corrupt-member-hash",
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, successor.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )


def test_corrupt_lineage_member_projection_fails_closed(repo):
    root, successor = setup_lineage(
        repo,
        "corrupt-member-projection",
    )

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999.00"), successor.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R510: STORED_METRIC_INTEGRITY_FAILURE",
    ):
        repo.active_metric_at(
            root.id,
            utc(2026, 9, 1),
        )


def test_middle_anchor_reconstructs_complete_three_revision_lineage(repo):
    root = revenue_metric("three-revision")

    second = restatement(
        root,
        value=Decimal("105.00"),
        observed_at=utc(2026, 8, 15),
        published_at=utc(2026, 8, 15),
        ingested_at=utc(2026, 8, 20),
        source_version="2",
    )

    third = restatement(
        second,
        value=Decimal("110.00"),
        observed_at=utc(2026, 9, 15),
        published_at=utc(2026, 9, 15),
        ingested_at=utc(2026, 9, 20),
        source_version="3",
    )

    repo.add_metric(root)
    repo.add_metric(second)
    repo.add_metric(third)

    assert repo.active_metric_at(
        second.id,
        utc(2026, 7, 15),
    ) == root

    assert repo.active_metric_at(
        second.id,
        utc(2026, 8, 25),
    ) == second

    assert repo.active_metric_at(
        second.id,
        utc(2026, 9, 25),
    ) == third
