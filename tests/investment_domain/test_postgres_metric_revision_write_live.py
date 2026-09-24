from datetime import datetime, timezone
from decimal import Decimal
import os

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Metric
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)
from investment_domain.postgres_migrations import PostgreSQLMigrationManager


UTC = timezone.utc
DSN = os.environ["IDM_TEST_DSN"]


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=UTC)


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


def root_metric(*, metric_id=None):
    values = {
        "subject_id": "company:abc",
        "name": "financial.revenue",
        "value": Decimal("100"),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 4, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 7, 10),
        "published_at": utc(2026, 7, 11),
        "ingested_at": utc(2026, 7, 11, 1),
        "source_id": "issuer:abc",
        "source_version": "q2-original",
        "supersedes_id": None,
    }

    # metric_id is retained only for call-site compatibility.
    # Valid Metric identity is always canonical.
    values["id"] = _metric_id(values)
    return Metric(**values)


def revision(
    predecessor,
    *,
    metric_id=None,
    **changes,
):
    values = {
        "subject_id": predecessor.subject_id,
        "name": predecessor.name,
        "value": Decimal("105"),
        "unit": predecessor.unit,
        "currency": predecessor.currency,
        "period_start": predecessor.period_start,
        "period_end": predecessor.period_end,
        "effective_at": utc(2026, 7, 1),
        "observed_at": utc(2026, 8, 10),
        "published_at": utc(2026, 8, 11),
        "ingested_at": utc(2026, 8, 11, 1),
        "source_id": predecessor.source_id,
        "source_version": "q2-restated",
        "supersedes_id": predecessor.id,
    }
    values.update(changes)

    # Recompute identity after all identity-bearing overrides.
    values["id"] = _metric_id(values)
    return Metric(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_type = 'Metric'
                   OR target_type = 'Metric'
                """
            )
            con.execute(
                """
                DELETE FROM metric_facts
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type = 'Metric'
                """
            )

    return repository


def test_valid_metric_revision_is_persisted(repo):
    root = root_metric()
    successor = revision(root)

    repo.add_metric(root)
    repo.add_metric(successor)

    with repo.connect() as con:
        row = con.execute(
            """
            SELECT supersedes_id, value, source_version
            FROM metric_facts
            WHERE node_id = %s
            """,
            (successor.id,),
        ).fetchone()

    assert row is not None
    assert row[0] == root.id
    assert row[1] == Decimal("105")
    assert row[2] == "q2-restated"


def test_missing_predecessor_fails_closed(repo):
    predecessor = root_metric()
    successor = revision(predecessor)

    # predecessor is intentionally valid but never persisted.
    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W512: REVISION_PREDECESSOR_NOT_FOUND",
    ):
        repo.add_metric(successor)


def test_revision_key_mismatch_fails_closed(repo):
    root = root_metric()
    repo.add_metric(root)

    successor = revision(
        root,
        subject_id="company:other",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W519: METRIC_REVISION_KEY_MISMATCH",
    ):
        repo.add_metric(successor)


def test_non_monotonic_ingestion_fails_closed(repo):
    root = root_metric()
    repo.add_metric(root)

    successor = revision(
        root,
        effective_at=root.effective_at,
        observed_at=root.observed_at,
        published_at=root.published_at,
        ingested_at=root.ingested_at,
        source_version="q2-restated-nonmonotonic",
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W514: NON_MONOTONIC_REVISION_INGESTION",
    ):
        repo.add_metric(successor)


def test_metric_revision_branch_is_forbidden(repo):
    root = root_metric()
    first = revision(
        root,
        metric_id="metric:005e-child-1",
    )
    second = revision(
        root,
        metric_id="metric:005e-child-2",
        value=Decimal("106"),
        ingested_at=utc(2026, 8, 12, 1),
        source_version="q2-restated-again",
    )

    repo.add_metric(root)
    repo.add_metric(first)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W515: REVISION_BRANCH_FORBIDDEN",
    ):
        repo.add_metric(second)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("period_start", utc(2026, 1, 1)),
        ("period_end", utc(2026, 9, 30)),
        ("source_id", "issuer:other"),
    ],
)
def test_all_repository_revision_key_changes_fail_closed(
    repo,
    field,
    value,
):
    root = root_metric(
        metric_id=f"metric:005e-key-root-{field}"
    )
    repo.add_metric(root)

    successor = revision(
        root,
        metric_id=f"metric:005e-key-child-{field}",
        **{field: value},
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W519: METRIC_REVISION_KEY_MISMATCH",
    ):
        repo.add_metric(successor)


def test_corrupt_predecessor_payload_fails_closed(repo):
    root = root_metric()
    repo.add_metric(root)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{source_version}',
                        '"corrupt-version"'::jsonb
                    )
                WHERE id = %s
                """,
                (root.id,),
            )

    successor = revision(root)

    with pytest.raises(
        RepositoryWriteError,
        match=(
            "IDM-W520: "
            "METRIC_REVISION_PREDECESSOR_INTEGRITY_FAILURE"
        ),
    ):
        repo.add_metric(successor)


def test_corrupt_predecessor_projection_fails_closed(repo):
    root = root_metric()
    repo.add_metric(root)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = value + 1
                WHERE node_id = %s
                """,
                (root.id,),
            )

    successor = revision(root)

    with pytest.raises(
        RepositoryWriteError,
        match=(
            "IDM-W520: "
            "METRIC_REVISION_PREDECESSOR_INTEGRITY_FAILURE"
        ),
    ):
        repo.add_metric(successor)
