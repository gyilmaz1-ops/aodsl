from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.types import NodeType


def _load_catalyst_impact():
    from investment_domain.nodes import CatalystImpact
    return CatalystImpact


def _impact(**overrides):
    CatalystImpact = _load_catalyst_impact()

    payload = {
        "catalyst_id": canonical_id(
            "catalyst",
            {"subject_id": "company:nvda", "description": "new platform"},
        ),
        "target_id": canonical_id(
            "forecast",
            {"subject_id": "company:nvda", "scenario": "BASE"},
        ),
        "direction": "POSITIVE",
        "magnitude": "HIGH",
        "probability": Decimal("0.80"),
        "confidence": Decimal("0.90"),
        "horizon": "MEDIUM_TERM",
        "rationale": "Expected to increase forecast revenue.",
        "as_of": datetime(2026, 9, 27, tzinfo=timezone.utc),
        "created_by": "Fundamental_Analyst",
    }
    payload.update(overrides)

    impact_id = canonical_id("catalyst_impact", payload)

    return CatalystImpact(
        id=impact_id,
        **payload,
    )


def test_node_type_exposes_catalyst_impact():
    assert NodeType.CATALYST_IMPACT.value == "CatalystImpact"


def test_catalyst_impact_has_exact_frozen_domain_shape():
    CatalystImpact = _load_catalyst_impact()

    assert CatalystImpact.__dataclass_params__.frozen is True

    assert [field.name for field in fields(CatalystImpact)] == [
        "id",
        "catalyst_id",
        "target_id",
        "direction",
        "magnitude",
        "probability",
        "confidence",
        "horizon",
        "rationale",
        "as_of",
        "created_by",
        "node_type",
    ]

    item = _impact()

    assert item.node_type is NodeType.CATALYST_IMPACT
    assert isinstance(item.probability, Decimal)
    assert isinstance(item.confidence, Decimal)


def test_catalyst_impact_uses_canonical_content_id():
    from investment_domain.validation import validate_node

    item = _impact()
    validate_node(item)

    assert item.id.startswith("catalyst_impact:")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("direction", "UP"),
        ("direction", ""),
        ("magnitude", "EXTREME"),
        ("magnitude", ""),
        ("horizon", "IMMEDIATE"),
        ("horizon", ""),
    ],
)
def test_catalyst_impact_rejects_unsupported_vocabularies(
    field_name,
    value,
):
    from investment_domain.validation import DomainValidationError, validate_node

    item = _impact(**{field_name: value})

    with pytest.raises(DomainValidationError):
        validate_node(item)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("probability", Decimal("-0.01")),
        ("probability", Decimal("1.01")),
        ("confidence", Decimal("-0.01")),
        ("confidence", Decimal("1.01")),
    ],
)
def test_catalyst_impact_rejects_values_outside_unit_interval(
    field_name,
    value,
):
    from investment_domain.validation import DomainValidationError, validate_node

    item = _impact(**{field_name: value})

    with pytest.raises(DomainValidationError):
        validate_node(item)


@pytest.mark.parametrize(
    "field_name",
    ["probability", "confidence"],
)
def test_catalyst_impact_rejects_float_scores(field_name):
    from dataclasses import replace

    from investment_domain.validation import (
        DomainValidationError,
        validate_node,
    )

    valid = _impact()
    item = replace(valid, **{field_name: 0.5})

    with pytest.raises(DomainValidationError):
        validate_node(item)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("catalyst_id", ""),
        ("target_id", ""),
        ("rationale", ""),
        ("created_by", ""),
    ],
)
def test_catalyst_impact_rejects_empty_required_strings(
    field_name,
    value,
):
    from investment_domain.validation import DomainValidationError, validate_node

    item = _impact(**{field_name: value})

    with pytest.raises(DomainValidationError):
        validate_node(item)


def test_catalyst_impact_accepts_boundary_scores():
    from investment_domain.validation import validate_node

    validate_node(
        _impact(
            probability=Decimal("0"),
            confidence=Decimal("1"),
        )
    )


def test_catalyst_impact_canonical_id_changes_with_assessment():
    first = _impact(direction="POSITIVE")
    second = _impact(direction="NEGATIVE")

    assert first.id != second.id


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("probability", Decimal("NaN")),
        ("probability", Decimal("Infinity")),
        ("probability", Decimal("-Infinity")),
        ("confidence", Decimal("NaN")),
        ("confidence", Decimal("Infinity")),
        ("confidence", Decimal("-Infinity")),
    ],
)
def test_catalyst_impact_rejects_non_finite_scores(
    field_name,
    value,
):
    from dataclasses import replace

    from investment_domain.validation import (
        DomainValidationError,
        validate_node,
    )

    valid = _impact()
    item = replace(valid, **{field_name: value})

    with pytest.raises(DomainValidationError):
        validate_node(item)


@pytest.mark.parametrize(
    "catalyst_id",
    [
        "claim:" + "a" * 64,
        "forecast:" + "b" * 64,
        "catalyst_impact:" + "c" * 64,
        "catalyst:not-a-canonical-hash",
    ],
)
def test_catalyst_impact_requires_canonical_catalyst_reference(
    catalyst_id,
):
    from dataclasses import replace

    from investment_domain.validation import (
        DomainValidationError,
        validate_node,
    )

    valid = _impact()
    item = replace(valid, catalyst_id=catalyst_id)

    with pytest.raises(DomainValidationError):
        validate_node(item)


@pytest.mark.parametrize(
    "target_id",
    [
        "valuation:" + "a" * 64,
        "catalyst:" + "b" * 64,
        "estimate:" + "c" * 64,
        "claim:not-a-canonical-hash",
        "forecast:not-a-canonical-hash",
    ],
)
def test_catalyst_impact_requires_claim_or_forecast_target(
    target_id,
):
    from dataclasses import replace

    from investment_domain.validation import (
        DomainValidationError,
        validate_node,
    )

    valid = _impact()
    item = replace(valid, target_id=target_id)

    with pytest.raises(DomainValidationError):
        validate_node(item)


def test_catalyst_impact_accepts_canonical_claim_target():
    from dataclasses import replace

    from investment_domain.validation import validate_node

    valid = _impact()
    claim_id = canonical_id(
        "claim",
        {"subject": "company:nvda", "predicate": "growth"},
    )

    item = replace(valid, target_id=claim_id)

    # Identity must be recomputed because target_id is content.
    payload = {
        field.name: getattr(item, field.name)
        for field in fields(item)
        if field.name not in {"id", "node_type"}
    }
    item = replace(
        item,
        id=canonical_id("catalyst_impact", payload),
    )

    validate_node(item)


def test_catalyst_impact_accepts_canonical_forecast_target():
    from investment_domain.validation import validate_node

    validate_node(_impact())


def test_valuation_may_depend_on_catalyst_impact():
    from investment_domain.edges import ALLOWED_EDGES, EdgeType
    from investment_domain.types import NodeType

    assert (
        NodeType.VALUATION,
        EdgeType.DEPENDS_ON,
        NodeType.CATALYST_IMPACT,
    ) in ALLOWED_EDGES
