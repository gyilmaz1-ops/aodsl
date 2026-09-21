from decimal import Decimal

import pytest

from investment_domain.canonical import canonical_json


def test_decimal_is_canonical_string():
    assert canonical_json({"x": Decimal("10.500")}) == '{"x":"10.500"}'


def test_binary_float_is_forbidden():
    with pytest.raises(TypeError):
        canonical_json({"x": 10.5})
