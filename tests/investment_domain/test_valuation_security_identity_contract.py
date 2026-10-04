import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Valuation
from investment_domain.validation import (
    DomainValidationError,
    validate_node,
)


def valuation(*, security_id: str) -> Valuation:
    from datetime import datetime, timezone
    from decimal import Decimal

    fields = {
        "security_id": security_id,
        "method": "DCF",
        "value": Decimal("100"),
        "currency": "USD",
        "as_of": datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        "model_version": "dcf-v1",
        "scenario": "BASE",
    }

    return Valuation(
        id=canonical_id("valuation", fields),
        **fields,
    )


def test_canonical_security_identity_is_accepted():
    validate_node(
        valuation(
            security_id="security:nasdaq:nvda",
        )
    )


@pytest.mark.parametrize(
    "security_id",
    [
        "security:",
        "security:nvda",
        "security:NASDAQ:nvda",
        "security:nasdaq:NVDA",
        "security:-nasdaq:nvda",
        "security:nasdaq:nvda-",
        "security:nasdaq:",
        "security::nvda",
        "company:nvidia",
        "nvda",
    ],
)
def test_noncanonical_security_identity_is_rejected(
    security_id: str,
):
    with pytest.raises(
        DomainValidationError,
        match="IDM-C004",
    ):
        validate_node(
            valuation(
                security_id=security_id,
            )
        )


def test_empty_security_identity_preserves_required_field_diagnostic():
    with pytest.raises(
        DomainValidationError,
        match="IDM-C001",
    ):
        validate_node(
            valuation(
                security_id="",
            )
        )


def test_security_reference_validation_is_independent_of_valuation_identity():
    node = valuation(
        security_id="security:NVDA",
    )

    # valuation() constructs a self-consistent Valuation node for the
    # supplied reference. The expected failure must therefore come from
    # validation of the Security reference itself.
    with pytest.raises(
        DomainValidationError,
        match="IDM-C004",
    ):
        validate_node(node)
