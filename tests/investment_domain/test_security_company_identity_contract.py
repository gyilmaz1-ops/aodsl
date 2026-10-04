import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Security
from investment_domain.validation import validate_node


def security(*, company_id: str) -> Security:
    fields = {
        "company_id": company_id,
        "venue": "NASDAQ",
        "ticker": "ACME",
        "currency": "USD",
    }
    return Security(
        id=canonical_id("security", fields),
        **fields,
    )


def test_security_accepts_canonical_company_identity():
    node = security(company_id="company:acme")

    validate_node(node)


@pytest.mark.parametrize(
    "company_id",
    (
        "security:acme",
        "company",
        "company:",
        "company:ACME",
        "company:-acme",
        "company:acme-",
        "company:acm  corp",
        "company:acme!",
        "acme",
        "",
    ),
)
def test_security_rejects_non_company_identity(company_id):
    node = security(company_id=company_id)

    with pytest.raises(ValueError):
        validate_node(node)
