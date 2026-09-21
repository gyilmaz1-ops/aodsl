from datetime import datetime, timezone

import pytest

from investment_domain import Claim, canonical_id, validate_node
from investment_domain.validation import DomainValidationError


UTC = timezone.utc


def claim_id(**overrides):
    payload = {
        "subject_id": "company:x",
        "predicate": "growth_accelerating",
        "object_value": "true",
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "COMPANY",
        "as_of": datetime(2026, 1, 1, tzinfo=UTC),
    }
    payload.update(overrides)
    return canonical_id("claim", payload)


def test_claim_requires_exactly_one_object():
    node = Claim(
        id=claim_id(object_value=None, object_ref=None),
        subject_id="company:x",
        predicate="growth_accelerating",
        object_value=None,
        object_ref=None,
        polarity="POSITIVE",
        scope="COMPANY",
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        created_by="Fundamental_Analyst",
    )
    with pytest.raises(DomainValidationError, match="IDM-C001"):
        validate_node(node)


def test_noncanonical_claim_identity_rejected():
    node = Claim(
        id="claim:" + "0" * 64,
        subject_id="company:x",
        predicate="growth_accelerating",
        object_value="true",
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        created_by="Fundamental_Analyst",
    )
    with pytest.raises(DomainValidationError, match="IDM-C003"):
        validate_node(node)
