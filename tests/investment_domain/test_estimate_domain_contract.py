from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Estimate
from investment_domain.types import NodeType
from investment_domain.validation import (
    DomainValidationError,
    validate_node,
)


UTC = timezone.utc


def utc(year, month, day):
    return datetime(year, month, day, tzinfo=UTC)


def estimate(**overrides):
    explicit_id = overrides.pop("id", None)

    values = {
        "subject_id": "company:estimate-test",
        "metric_name": "financial.revenue",
        "period_end": utc(2027, 12, 31),
        "value": Decimal("210000000000"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "estimate-v1",
        "as_of": utc(2026, 9, 26),
        "currency": "USD",
    }
    values.update(overrides)

    if explicit_id is None:
        explicit_id = canonical_id(
            "estimate",
            {
                "subject_id": values["subject_id"],
                "metric_name": values["metric_name"],
                "period_end": values["period_end"],
                "value": values["value"],
                "unit": values["unit"],
                "scenario": values["scenario"],
                "model_version": values["model_version"],
                "as_of": values["as_of"],
                "currency": values["currency"],
            },
        )

    return Estimate(id=explicit_id, **values)


def test_estimate_is_immutable_typed_domain_node():
    node = estimate()

    assert node.node_type is NodeType.ESTIMATE

    with pytest.raises(FrozenInstanceError):
        node.value = Decimal("1")


def test_valid_estimate_passes_domain_validation():
    validate_node(estimate())


def test_estimate_value_is_identity_bearing():
    first = estimate(value=Decimal("210000000000"))
    second = estimate(value=Decimal("211000000000"))

    assert first.id != second.id

    validate_node(first)
    validate_node(second)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject_id", ""),
        ("metric_name", ""),
        ("unit", ""),
        ("scenario", ""),
        ("model_version", ""),
    ],
)
def test_estimate_rejects_empty_semantic_strings(field, value):
    node = estimate(**{field: value})

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_rejects_unknown_metric_name():
    node = estimate(metric_name="financial.not_registered")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_rejects_wrong_unit_for_metric_definition():
    node = estimate(unit="shares")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_requires_currency_when_metric_requires_currency():
    node = estimate(currency=None)

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_rejects_invalid_currency_code():
    node = estimate(currency="usd")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_rejects_binary_float_value():
    node = estimate(
        value=1.5,
        id="estimate:" + "0" * 64,
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_estimate_rejects_non_finite_decimal(value):
    node = estimate(value=value)

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_rejects_naive_period_end():
    node = estimate(
        period_end=datetime(2027, 12, 31),
        id="estimate:" + "0" * 64,
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_rejects_naive_as_of():
    node = estimate(
        as_of=datetime(2026, 9, 26),
        id="estimate:" + "0" * 64,
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_rejects_wrong_subject_type_for_metric_definition():
    node = estimate(subject_id="security:estimate-test")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_estimate_forbids_currency_when_metric_does_not_require_it():
    # Pick a registered non-currency metric dynamically so this contract
    # does not hard-code a registry name that may not exist.
    from investment_domain.metrics import METRIC_DEFINITIONS

    definition = next(
        d
        for d in METRIC_DEFINITIONS.values()
        if not d.requires_currency
    )

    prefix = {
        NodeType.COMPANY: "company:",
        NodeType.SECURITY: "security:",
    }[definition.subject_type]

    node = estimate(
        subject_id=prefix + "estimate-test",
        metric_name=definition.name,
        unit=definition.unit,
        currency="USD",
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("period_end", utc(2028, 12, 31)),
        ("value", Decimal("211000000000")),
        ("scenario", "BULL"),
        ("model_version", "estimate-v2"),
        ("as_of", utc(2026, 9, 27)),
        ("currency", "EUR"),
    ],
)
def test_estimate_semantic_fields_are_identity_bearing(field, replacement):
    original = estimate()

    changed_values = {
        "subject_id": original.subject_id,
        "metric_name": original.metric_name,
        "period_end": original.period_end,
        "value": original.value,
        "unit": original.unit,
        "scenario": original.scenario,
        "model_version": original.model_version,
        "as_of": original.as_of,
        "currency": original.currency,
    }
    changed_values[field] = replacement

    changed = estimate(**changed_values)

    assert changed.id != original.id


def test_estimate_identity_is_independent_of_dataclass_fallback():
    node = estimate()

    expected = canonical_id(
        "estimate",
        {
            "subject_id": node.subject_id,
            "metric_name": node.metric_name,
            "period_end": node.period_end,
            "value": node.value,
            "unit": node.unit,
            "scenario": node.scenario,
            "model_version": node.model_version,
            "as_of": node.as_of,
            "currency": node.currency,
        },
    )

    assert node.id == expected
    validate_node(node)
