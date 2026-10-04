from datetime import datetime, timedelta, timezone

import os
import pytest

from investment_domain import (
    Claim,
    PostgreSQLEvidenceRepository,
)
from investment_domain.repository import EvidenceRepository
from investment_domain.postgres_repository import RepositoryReadError
from investment_domain.validation import canonical_id


UTC = timezone.utc
DSN = (
    os.getenv("IDM_TEST_POSTGRES_DSN")
    or os.getenv("IDM_POSTGRES_DSN")
    or os.getenv("PG_DSN")
)

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="live PostgreSQL DSN is not configured",
)


def make_claim(tag: str, *, as_of: datetime) -> Claim:
    draft = Claim(
        id="claim:placeholder",
        subject_id=f"security:r576-{tag}",
        predicate="HAS_R576_EXACT_READ",
        as_of=as_of,
        created_by="R576_Test_Agent",
        object_value=f"value-{tag}",
        object_ref=None,
        polarity="POSITIVE",
        scope="R576",
    )
    return Claim(
        id=canonical_id(
            "claim",
            {
                "subject_id": draft.subject_id,
                "predicate": draft.predicate,
                "object_value": draft.object_value,
                "object_ref": draft.object_ref,
                "polarity": draft.polarity,
                "scope": draft.scope,
                "as_of": draft.as_of,
            },
        ),
        subject_id=draft.subject_id,
        predicate=draft.predicate,
        as_of=draft.as_of,
        created_by=draft.created_by,
        object_value=draft.object_value,
        object_ref=draft.object_ref,
        polarity=draft.polarity,
        scope=draft.scope,
    )


def test_repository_protocol_exposes_claim_at():
    assert hasattr(EvidenceRepository, "claim_at")


def test_postgres_repository_exposes_claim_at():
    assert hasattr(PostgreSQLEvidenceRepository, "claim_at")


def test_claim_at_round_trips_exact_claim():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim(
        "round-trip",
        as_of=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )
    repo.add_claim(claim)

    loaded = repo.claim_at(
        claim.id,
        datetime(2026, 10, 4, 12, tzinfo=UTC),
    )

    assert loaded == claim


def test_claim_at_unknown_canonical_id_returns_none():
    repo = PostgreSQLEvidenceRepository(DSN)

    assert (
        repo.claim_at(
            "claim:0000000000000000000000000000000000000000000000000000000000000000",
            datetime(2026, 10, 4, 12, tzinfo=UTC),
        )
        is None
    )


def test_claim_at_does_not_use_as_of_as_visibility_gate():
    repo = PostgreSQLEvidenceRepository(DSN)

    cutoff = datetime(2026, 10, 4, 12, tzinfo=UTC)
    claim = make_claim(
        "future-as-of",
        as_of=cutoff + timedelta(days=30),
    )
    repo.add_claim(claim)

    loaded = repo.claim_at(claim.id, cutoff)

    assert loaded == claim

def test_claim_at_fails_closed_when_projection_missing():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim(
        "projection-missing",
        as_of=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )

    with repo.connect() as cleanup:
        cleanup.execute(
            "DELETE FROM claim_facts WHERE node_id = %s",
            (claim.id,),
        )
        cleanup.execute(
            "DELETE FROM domain_nodes WHERE id = %s",
            (claim.id,),
        )
        cleanup.commit()

    repo.add_claim(claim)

    try:
        with repo.connect() as con:
            con.execute(
                "DELETE FROM claim_facts WHERE node_id = %s",
                (claim.id,),
            )
            con.commit()

        with pytest.raises(
            RepositoryReadError,
            match=r"IDM-R593: CLAIM_PROJECTION_NOT_FOUND",
        ):
            repo.claim_at(
                claim.id,
                datetime(2026, 10, 4, 12, tzinfo=UTC),
            )
    finally:
        with repo.connect() as cleanup:
            cleanup.execute(
                "DELETE FROM claim_facts WHERE node_id = %s",
                (claim.id,),
            )
            cleanup.execute(
                "DELETE FROM domain_nodes WHERE id = %s",
                (claim.id,),
            )
            cleanup.commit()


def test_claim_at_fails_closed_when_projection_mismatches():
    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim(
        "projection-mismatch",
        as_of=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )

    with repo.connect() as cleanup:
        cleanup.execute(
            "DELETE FROM claim_facts WHERE node_id = %s",
            (claim.id,),
        )
        cleanup.execute(
            "DELETE FROM domain_nodes WHERE id = %s",
            (claim.id,),
        )
        cleanup.commit()

    repo.add_claim(claim)

    try:
        with repo.connect() as con:
            con.execute(
                """
                UPDATE claim_facts
                SET predicate = %s
                WHERE node_id = %s
                """,
                ("corrupted-projection-predicate", claim.id),
            )
            con.commit()

        with pytest.raises(
            RepositoryReadError,
            match=r"IDM-R594: CLAIM_PROJECTION_MISMATCH",
        ):
            repo.claim_at(
                claim.id,
                datetime(2026, 10, 4, 12, tzinfo=UTC),
            )
    finally:
        with repo.connect() as cleanup:
            cleanup.execute(
                "DELETE FROM claim_facts WHERE node_id = %s",
                (claim.id,),
            )
            cleanup.execute(
                "DELETE FROM domain_nodes WHERE id = %s",
                (claim.id,),
            )
            cleanup.commit()
