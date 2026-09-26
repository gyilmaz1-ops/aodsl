from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import Valuation, canonical_id, validate_node
from investment_domain.validation import DomainValidationError


UTC = timezone.utc


def _valuation(**overrides):
    payload = {
        "security_id": "security:nasdaq:nvda",
        "method": "DCF",
        "value": Decimal("250.00"),
        "currency": "USD",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "model_version": "1",
        "scenario": "BASE",
    }
    payload.update(overrides)
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def test_valid_valuation_accepted():
    validate_node(_valuation())


@pytest.mark.parametrize(
    "method",
    [
        "dcf",
        "DCF ",
        "P/E",
        "UNKNOWN",
    ],
)
def test_valuation_rejects_unsupported_method(method):
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(_valuation(method=method))


@pytest.mark.parametrize(
    "scenario",
    [
        "base",
        "BASE ",
        "UPSIDE",
        "UNKNOWN",
    ],
)
def test_valuation_rejects_unsupported_scenario(scenario):
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(_valuation(scenario=scenario))


@pytest.mark.parametrize(
    "currency",
    [
        "usd",
        "US",
        "USDD",
        "U1D",
    ],
)
def test_valuation_rejects_invalid_currency(currency):
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(_valuation(currency=currency))


def test_valuation_rejects_non_security_subject():
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(
            _valuation(
                security_id="company:nvidia",
            )
        )


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_valuation_rejects_non_finite_decimal(value):
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(_valuation(value=value))


def test_valuation_identity_changes_with_method():
    dcf = _valuation(method="DCF")
    multiple = _valuation(method="EV_EBITDA")

    assert dcf.id != multiple.id


def test_valuation_identity_changes_with_scenario():
    base = _valuation(scenario="BASE")
    bull = _valuation(scenario="BULL")

    assert base.id != bull.id


def test_valuation_identity_changes_with_model_version():
    v1 = _valuation(model_version="1")
    v2 = _valuation(model_version="2")

    assert v1.id != v2.id
