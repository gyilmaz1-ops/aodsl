from datetime import datetime, timezone

import pytest

from investment_domain import (
    EdgeType,
    Claim,
    ClaimEvidenceLink,
    ClaimStatus,
    Evidence,
    canonical_id,
    eligible_claim_evidence,
    validate_claim_evidence_link,
    validate_claim_transition,
)


UTC = timezone.utc


def make_claim(text: str = "Revenue growth is accelerating") -> Claim:
    identity_payload = {
        "subject_id": "company:x",
        "predicate": text,
        "object_value": None,
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "COMPANY",
        "as_of": datetime(2027, 2, 10, tzinfo=UTC),
    }

    return Claim(
        id=canonical_id("claim", identity_payload),
        **identity_payload,
        created_by="Fundamental_Analyst",
    )


def make_evidence(
    *,
    source_version: str = "1",
    content_hash: str = "a" * 64,
    published_at: datetime | None = None,
    ingested_at: datetime | None = None,
) -> Evidence:
    published_at = published_at or datetime(
        2027, 2, 10, 8, tzinfo=UTC
    )
    ingested_at = ingested_at or datetime(
        2027, 2, 10, 8, 4, tzinfo=UTC
    )

    payload = {
        "source_id": "filing:x",
        "source_version": source_version,
        "content_hash": content_hash,
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 2, 10, 7, tzinfo=UTC),
        "published_at": published_at,
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=ingested_at,
    )


def make_link(
    claim: Claim,
    evidence: Evidence,
    relation: EdgeType = EdgeType.SUPPORTED_BY,
    *,
    created_at: datetime | None = None,
) -> ClaimEvidenceLink:
    return ClaimEvidenceLink(
        claim_id=claim.id,
        evidence_id=evidence.id,
        relation=relation,
        created_at=created_at or datetime(
            2027, 2, 10, 8, 5, tzinfo=UTC
        ),
    )


def test_claim_lifecycle_happy_path():
    validate_claim_transition(
        ClaimStatus.PROPOSED,
        ClaimStatus.EVIDENCED,
    )
    validate_claim_transition(
        ClaimStatus.EVIDENCED,
        ClaimStatus.VERIFIED,
    )
    validate_claim_transition(
        ClaimStatus.VERIFIED,
        ClaimStatus.SUPERSEDED,
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ClaimStatus.PROPOSED, ClaimStatus.VERIFIED),
        (ClaimStatus.PROPOSED, ClaimStatus.REJECTED),
        (ClaimStatus.VERIFIED, ClaimStatus.REJECTED),
        (ClaimStatus.SUPERSEDED, ClaimStatus.PROPOSED),
        (ClaimStatus.EVIDENCED, ClaimStatus.EVIDENCED),
    ],
)
def test_invalid_claim_lifecycle_transition_rejected(current, target):
    with pytest.raises(ValueError):
        validate_claim_transition(current, target)


def test_claim_evidence_link_type_contract():
    claim = make_claim()
    evidence = make_evidence()

    validate_claim_evidence_link(
        make_link(claim, evidence)
    )


def test_wrong_claim_identity_rejected():
    evidence = make_evidence()

    link = ClaimEvidenceLink(
        claim_id="metric:x",
        evidence_id=evidence.id,
        relation=EdgeType.SUPPORTED_BY,
        created_at=datetime(2027, 2, 10, 8, 5, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="must reference Claim"):
        validate_claim_evidence_link(link)


def test_wrong_evidence_identity_rejected():
    claim = make_claim()

    link = ClaimEvidenceLink(
        claim_id=claim.id,
        evidence_id="metric:x",
        relation=EdgeType.SUPPORTED_BY,
        created_at=datetime(2027, 2, 10, 8, 5, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="must reference Evidence"):
        validate_claim_evidence_link(link)


def test_naive_link_time_rejected():
    claim = make_claim()
    evidence = make_evidence()

    link = ClaimEvidenceLink(
        claim_id=claim.id,
        evidence_id=evidence.id,
        relation=EdgeType.SUPPORTED_BY,
        created_at=datetime(2027, 2, 10, 8, 5),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        validate_claim_evidence_link(link)


def test_available_supporting_evidence_is_returned():
    claim = make_claim()
    evidence = make_evidence()
    link = make_link(claim, evidence)

    result = eligible_claim_evidence(
        claim,
        [evidence],
        [link],
        datetime(2027, 2, 20, tzinfo=UTC),
    )

    assert result == (
        (evidence, EdgeType.SUPPORTED_BY),
    )


def test_support_and_contradiction_are_preserved():
    claim = make_claim()

    support = make_evidence(
        source_version="1",
        content_hash="a" * 64,
    )
    contradiction = make_evidence(
        source_version="2",
        content_hash="b" * 64,
        published_at=datetime(2027, 2, 11, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 11, 8, 4, tzinfo=UTC),
    )

    links = [
        make_link(
            claim,
            support,
            EdgeType.SUPPORTED_BY,
        ),
        make_link(
            claim,
            contradiction,
            EdgeType.CONTRADICTED_BY,
            created_at=datetime(2027, 2, 11, 8, 5, tzinfo=UTC),
        ),
    ]

    result = eligible_claim_evidence(
        claim,
        [support, contradiction],
        links,
        datetime(2027, 2, 20, tzinfo=UTC),
    )

    assert {
        relation
        for _, relation in result
    } == {
        EdgeType.SUPPORTED_BY,
        EdgeType.CONTRADICTED_BY,
    }


def test_future_evidence_excluded_from_historical_claim():
    claim = make_claim()

    evidence = make_evidence(
        published_at=datetime(2027, 3, 15, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 3, 15, 8, 4, tzinfo=UTC),
    )

    link = make_link(
        claim,
        evidence,
        created_at=datetime(2027, 3, 15, 8, 5, tzinfo=UTC),
    )

    result = eligible_claim_evidence(
        claim,
        [evidence],
        [link],
        datetime(2027, 2, 20, tzinfo=UTC),
    )

    assert result == ()


def test_future_link_excluded_even_when_evidence_already_available():
    claim = make_claim()
    evidence = make_evidence()

    link = make_link(
        claim,
        evidence,
        created_at=datetime(2027, 3, 1, tzinfo=UTC),
    )

    result = eligible_claim_evidence(
        claim,
        [evidence],
        [link],
        datetime(2027, 2, 20, tzinfo=UTC),
    )

    assert result == ()


def test_duplicate_claim_evidence_link_rejected():
    claim = make_claim()
    evidence = make_evidence()
    link = make_link(claim, evidence)

    with pytest.raises(ValueError, match="duplicate claim-evidence link"):
        eligible_claim_evidence(
            claim,
            [evidence],
            [link, link],
            datetime(2027, 2, 20, tzinfo=UTC),
        )


def test_duplicate_evidence_identity_rejected():
    claim = make_claim()
    evidence = make_evidence()
    link = make_link(claim, evidence)

    with pytest.raises(ValueError, match="duplicate evidence identity"):
        eligible_claim_evidence(
            claim,
            [evidence, evidence],
            [link],
            datetime(2027, 2, 20, tzinfo=UTC),
        )


def test_missing_evidence_reference_rejected():
    claim = make_claim()
    evidence = make_evidence()
    link = make_link(claim, evidence)

    with pytest.raises(ValueError, match="missing Evidence"):
        eligible_claim_evidence(
            claim,
            [],
            [link],
            datetime(2027, 2, 20, tzinfo=UTC),
        )


def test_mixed_claim_links_rejected():
    claim_a = make_claim("Revenue growth is accelerating")
    claim_b = make_claim("Margins are expanding")
    evidence = make_evidence()

    link = make_link(claim_b, evidence)

    with pytest.raises(ValueError, match="different claim"):
        eligible_claim_evidence(
            claim_a,
            [evidence],
            [link],
            datetime(2027, 2, 20, tzinfo=UTC),
        )


def test_naive_research_cutoff_rejected_for_claim_evaluation():
    claim = make_claim()
    evidence = make_evidence()
    link = make_link(claim, evidence)

    with pytest.raises(ValueError, match="timezone-aware"):
        eligible_claim_evidence(
            claim,
            [evidence],
            [link],
            datetime(2027, 2, 20),
        )


def test_superseded_evidence_is_active_before_successor_available():
    claim = make_claim()

    original = make_evidence(
        source_version="1",
        content_hash="a" * 64,
    )

    revised_payload = {
        "source_id": original.source_id,
        "source_version": "2",
        "content_hash": "b" * 64,
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 3, 15, 7, tzinfo=UTC),
        "published_at": datetime(2027, 3, 15, 8, tzinfo=UTC),
    }
    revised = Evidence(
        id=canonical_id("evidence", revised_payload),
        **revised_payload,
        ingested_at=datetime(2027, 3, 15, 8, 4, tzinfo=UTC),
        supersedes_id=original.id,
    )

    link = make_link(claim, original)

    result = eligible_claim_evidence(
        claim,
        [original, revised],
        [link],
        datetime(2027, 2, 20, tzinfo=UTC),
    )

    assert result == (
        (original, EdgeType.SUPPORTED_BY),
    )


def test_superseded_link_is_not_inherited_by_successor():
    claim = make_claim()

    original = make_evidence(
        source_version="1",
        content_hash="a" * 64,
    )

    revised_payload = {
        "source_id": original.source_id,
        "source_version": "2",
        "content_hash": "b" * 64,
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 3, 15, 7, tzinfo=UTC),
        "published_at": datetime(2027, 3, 15, 8, tzinfo=UTC),
    }
    revised = Evidence(
        id=canonical_id("evidence", revised_payload),
        **revised_payload,
        ingested_at=datetime(2027, 3, 15, 8, 4, tzinfo=UTC),
        supersedes_id=original.id,
    )

    original_link = make_link(claim, original)

    result = eligible_claim_evidence(
        claim,
        [original, revised],
        [original_link],
        datetime(2027, 4, 1, tzinfo=UTC),
    )

    assert result == ()


def test_successor_requires_explicit_claim_link():
    claim = make_claim()

    original = make_evidence(
        source_version="1",
        content_hash="a" * 64,
    )

    revised_payload = {
        "source_id": original.source_id,
        "source_version": "2",
        "content_hash": "b" * 64,
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 3, 15, 7, tzinfo=UTC),
        "published_at": datetime(2027, 3, 15, 8, tzinfo=UTC),
    }
    revised = Evidence(
        id=canonical_id("evidence", revised_payload),
        **revised_payload,
        ingested_at=datetime(2027, 3, 15, 8, 4, tzinfo=UTC),
        supersedes_id=original.id,
    )

    original_link = make_link(claim, original)
    revised_link = make_link(
        claim,
        revised,
        created_at=datetime(2027, 3, 15, 8, 5, tzinfo=UTC),
    )

    result = eligible_claim_evidence(
        claim,
        [original, revised],
        [original_link, revised_link],
        datetime(2027, 4, 1, tzinfo=UTC),
    )

    assert result == (
        (revised, EdgeType.SUPPORTED_BY),
    )


def test_non_claim_evidence_edge_type_rejected():
    claim = make_claim()

    link = ClaimEvidenceLink(
        claim_id=claim.id,
        evidence_id="evidence:" + "a" * 64,
        relation=EdgeType.AFFECTS,
        created_at=datetime(2027, 2, 10, tzinfo=UTC),
    )

    with pytest.raises(
        ValueError,
        match="relation must be SUPPORTED_BY or CONTRADICTED_BY",
    ):
        validate_claim_evidence_link(link)
