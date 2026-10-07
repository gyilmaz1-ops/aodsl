from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.sec_financial_facts import ExtractedFinancialFact


UTC = timezone.utc


def fact() -> ExtractedFinancialFact:
    return ExtractedFinancialFact(
        concept="us-gaap:Revenues",
        value=Decimal("1000000"),
        unit_ref="USD",
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 12, 31, tzinfo=UTC),
        context_id="FY2026",
        dimensions=(),
        decimals="-3",
    )


def test_extracted_financial_fact_preserves_source_semantics():
    node = fact()

    assert node.concept == "us-gaap:Revenues"
    assert node.value == Decimal("1000000")
    assert node.unit_ref == "USD"
    assert node.period_start == datetime(2026, 1, 1, tzinfo=UTC)
    assert node.period_end == datetime(2026, 12, 31, tzinfo=UTC)
    assert node.context_id == "FY2026"
    assert node.dimensions == ()
    assert node.decimals == "-3"


def test_extracted_financial_fact_is_immutable():
    node = fact()

    with pytest.raises(FrozenInstanceError):
        node.value = Decimal("2000000")


from investment_domain.sec_financial_facts import (
    FinancialFactValidationError,
    validate_extracted_financial_fact,
)


def test_valid_extracted_financial_fact_is_accepted():
    validate_extracted_financial_fact(fact())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("concept", ""),
        ("unit_ref", ""),
        ("context_id", ""),
    ],
)
def test_required_text_fields_fail_closed(field, value):
    from dataclasses import replace

    node = replace(fact(), **{field: value})

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_value_must_be_decimal():
    from dataclasses import replace

    node = replace(fact(), value=1000000)

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_value_must_be_finite(value):
    from dataclasses import replace

    node = replace(fact(), value=value)

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_period_end_must_be_timezone_aware():
    from dataclasses import replace

    node = replace(
        fact(),
        period_end=datetime(2026, 12, 31),
    )

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_period_start_must_be_timezone_aware_when_present():
    from dataclasses import replace

    node = replace(
        fact(),
        period_start=datetime(2026, 1, 1),
    )

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_period_start_cannot_exceed_period_end():
    from dataclasses import replace

    node = replace(
        fact(),
        period_start=datetime(2027, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_dimensions_must_be_canonical_and_unique():
    from dataclasses import replace

    node = replace(
        fact(),
        dimensions=(
            ("us-gaap:ProductAxis", "example:Widgets"),
            ("us-gaap:ProductAxis", "example:Widgets"),
        ),
    )

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


@pytest.mark.parametrize("field", ["concept", "unit_ref", "context_id"])
def test_required_text_fields_reject_whitespace_only(field):
    from dataclasses import replace

    node = replace(fact(), **{field: "   "})

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


@pytest.mark.parametrize(
    "dimensions",
    [
        [],
        (("us-gaap:ProductAxis",),),
        (("us-gaap:ProductAxis", ""),),
        (("", "example:Widgets"),),
    ],
)
def test_invalid_dimension_shapes_fail_closed(dimensions):
    from dataclasses import replace

    node = replace(fact(), dimensions=dimensions)

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_dimensions_must_use_canonical_ordering():
    from dataclasses import replace

    node = replace(
        fact(),
        dimensions=(
            ("us-gaap:RegionAxis", "example:US"),
            ("us-gaap:ProductAxis", "example:Widgets"),
        ),
    )

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_period_end_must_be_datetime():
    from dataclasses import replace

    node = replace(fact(), period_end="2026-12-31")

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)


def test_period_start_must_be_datetime_when_present():
    from dataclasses import replace

    node = replace(fact(), period_start="2026-01-01")

    with pytest.raises(FinancialFactValidationError):
        validate_extracted_financial_fact(node)




from investment_domain.sec_financial_facts import (
    extract_financial_facts_from_ixbrl,
)


MINIMAL_IXBRL = """
<html
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:us-gaap="http://fasb.org/us-gaap/2026">
  <body>
    <xbrli:context id="FY2026">
      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">
          0000123456
        </xbrli:identifier>
      </xbrli:entity>
      <xbrli:period>
        <xbrli:startDate>2026-01-01</xbrli:startDate>
        <xbrli:endDate>2026-12-31</xbrli:endDate>
      </xbrli:period>
    </xbrli:context>

    <xbrli:unit id="USD">
      <xbrli:measure>iso4217:USD</xbrli:measure>
    </xbrli:unit>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>
  </body>
</html>
"""


def test_extracts_one_revenue_fact_from_minimal_ixbrl():
    facts = extract_financial_facts_from_ixbrl(MINIMAL_IXBRL)

    assert len(facts) == 1

    extracted = facts[0]

    assert extracted.concept == "us-gaap:Revenues"
    assert extracted.value == Decimal("1000000")
    assert extracted.unit_ref == "USD"
    assert extracted.context_id == "FY2026"
    assert extracted.period_start == datetime(
        2026, 1, 1, tzinfo=UTC
    )
    assert extracted.period_end == datetime(
        2026, 12, 31, tzinfo=UTC
    )
    assert extracted.dimensions == ()
    assert extracted.decimals == "-3"


def test_unknown_context_reference_fails_closed():
    broken = MINIMAL_IXBRL.replace(
        'contextRef="FY2026"',
        'contextRef="MISSING"',
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(broken)


def test_missing_unit_reference_fails_closed():
    broken = MINIMAL_IXBRL.replace(
        ' unitRef="USD"',
        "",
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(broken)


def test_malformed_numeric_value_fails_closed():
    broken = MINIMAL_IXBRL.replace(
        ">1000000</ix:nonFraction>",
        ">NOT_A_NUMBER</ix:nonFraction>",
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(broken)


def test_unknown_unit_reference_fails_closed():
    broken = MINIMAL_IXBRL.replace(
        'unitRef="USD"',
        'unitRef="MISSING"',
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(broken)


def test_missing_unit_definition_fails_closed():
    broken = MINIMAL_IXBRL.replace(
        """    <xbrli:unit id="USD">
      <xbrli:measure>iso4217:USD</xbrli:measure>
    </xbrli:unit>

""",
        "",
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(broken)


def test_blank_unit_id_cannot_satisfy_unit_reference():
    broken = MINIMAL_IXBRL.replace(
        '<xbrli:unit id="USD">',
        '<xbrli:unit id="">',
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(broken)


def test_unit_reference_resolves_among_multiple_units():
    expanded = MINIMAL_IXBRL.replace(
        """    <xbrli:unit id="USD">
      <xbrli:measure>iso4217:USD</xbrli:measure>
    </xbrli:unit>
""",
        """    <xbrli:unit id="EUR">
      <xbrli:measure>iso4217:EUR</xbrli:measure>
    </xbrli:unit>

    <xbrli:unit id="USD">
      <xbrli:measure>iso4217:USD</xbrli:measure>
    </xbrli:unit>
""",
    )

    facts = extract_financial_facts_from_ixbrl(expanded)

    assert len(facts) == 1
    assert facts[0].unit_ref == "USD"


def test_conflicting_duplicate_financial_fact_fails_closed():
    conflicting = MINIMAL_IXBRL.replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">2000000</ix:nonFraction>""",
    )

    with pytest.raises(
        FinancialFactValidationError,
        match="conflicting duplicate financial fact",
    ):
        extract_financial_facts_from_ixbrl(conflicting)


def test_identical_duplicate_financial_fact_is_allowed():
    duplicate = MINIMAL_IXBRL.replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
    )

    facts = extract_financial_facts_from_ixbrl(duplicate)

    assert len(facts) == 2
    assert facts[0].value == facts[1].value


def test_numerically_equal_duplicate_financial_fact_is_allowed():
    duplicate = MINIMAL_IXBRL.replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000.00</ix:nonFraction>""",
    )

    facts = extract_financial_facts_from_ixbrl(duplicate)

    assert len(facts) == 2
    assert facts[0].value == facts[1].value


def test_same_concept_with_different_context_is_not_duplicate():
    different_context = MINIMAL_IXBRL.replace(
        """  <xbrli:unit id="USD">""",
        """  <xbrli:context id="FY2025">
    <xbrli:entity>
      <xbrli:identifier scheme="example">
        EXAMPLE
      </xbrli:identifier>
    </xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2025-01-01</xbrli:startDate>
      <xbrli:endDate>2025-12-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>

  <xbrli:unit id="USD">""",
    ).replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2025"
      unitRef="USD"
      decimals="-3">2000000</ix:nonFraction>""",
    )

    facts = extract_financial_facts_from_ixbrl(
        different_context
    )

    assert len(facts) == 2
    assert facts[0].context_id != facts[1].context_id


def test_same_concept_and_context_with_different_unit_is_not_duplicate():
    different_unit = MINIMAL_IXBRL.replace(
        """  <xbrli:unit id="USD">""",
        """  <xbrli:unit id="shares">
    <xbrli:measure>xbrli:shares</xbrli:measure>
  </xbrli:unit>

  <xbrli:unit id="USD">""",
    ).replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="shares"
      decimals="-3">2000000</ix:nonFraction>""",
    )

    facts = extract_financial_facts_from_ixbrl(
        different_unit
    )

    assert len(facts) == 2
    assert facts[0].unit_ref != facts[1].unit_ref


def test_context_explicit_member_dimension_is_preserved():
    dimensional = MINIMAL_IXBRL.replace(
        """      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">
          0000123456
        </xbrli:identifier>
      </xbrli:entity>""",
        """      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">
          0000123456
        </xbrli:identifier>
        <xbrli:segment>
          <xbrldi:explicitMember
            dimension="us-gaap:ProductAxis">example:Widgets</xbrldi:explicitMember>
        </xbrli:segment>
      </xbrli:entity>""",
    ).replace(
        'xmlns:us-gaap="http://fasb.org/us-gaap/2026">',
        """xmlns:us-gaap="http://fasb.org/us-gaap/2026"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:example="http://example.com/2026">""",
    )

    facts = extract_financial_facts_from_ixbrl(dimensional)

    assert len(facts) == 1
    assert facts[0].dimensions == (
        ("us-gaap:ProductAxis", "example:Widgets"),
    )



def _ixbrl_with_explicit_members(members):
    member_xml = "\n".join(
        (
            '          <xbrldi:explicitMember '
            f'dimension="{dimension}">'
            f'{member}</xbrldi:explicitMember>'
        )
        for dimension, member in members
    )

    return MINIMAL_IXBRL.replace(
        '''      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">
          0000123456
        </xbrli:identifier>
      </xbrli:entity>''',
        f'''      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">
          0000123456
        </xbrli:identifier>
        <xbrli:segment>
{member_xml}
        </xbrli:segment>
      </xbrli:entity>''',
    ).replace(
        'xmlns:us-gaap="http://fasb.org/us-gaap/2026">',
        '''xmlns:us-gaap="http://fasb.org/us-gaap/2026"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:example="http://example.com/2026">''',
    )


def test_multiple_explicit_member_dimensions_are_canonicalized():
    dimensional = _ixbrl_with_explicit_members(
        [
            ("us-gaap:ProductAxis", "example:Widgets"),
            ("us-gaap:GeographyAxis", "example:Europe"),
        ]
    )

    facts = extract_financial_facts_from_ixbrl(dimensional)

    assert facts[0].dimensions == (
        ("us-gaap:GeographyAxis", "example:Europe"),
        ("us-gaap:ProductAxis", "example:Widgets"),
    )


def test_blank_explicit_member_dimension_fails_closed():
    dimensional = _ixbrl_with_explicit_members(
        [
            ("", "example:Widgets"),
        ]
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(dimensional)


def test_blank_explicit_member_value_fails_closed():
    dimensional = _ixbrl_with_explicit_members(
        [
            ("us-gaap:ProductAxis", ""),
        ]
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(dimensional)


def test_duplicate_explicit_member_dimension_fails_closed():
    dimensional = _ixbrl_with_explicit_members(
        [
            ("us-gaap:ProductAxis", "example:Widgets"),
            ("us-gaap:ProductAxis", "example:Gadgets"),
        ]
    )

    with pytest.raises(FinancialFactValidationError):
        extract_financial_facts_from_ixbrl(dimensional)

def test_conflicting_duplicate_in_dimensional_context_fails_closed():
    dimensional = _ixbrl_with_explicit_members(
        [
            ("us-gaap:ProductAxis", "example:Widgets"),
        ]
    )

    conflicting = dimensional.replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">2000000</ix:nonFraction>""",
    )

    with pytest.raises(
        FinancialFactValidationError,
        match="conflicting duplicate financial fact",
    ):
        extract_financial_facts_from_ixbrl(conflicting)


def test_identical_duplicate_in_dimensional_context_preserves_dimensions():
    dimensional = _ixbrl_with_explicit_members(
        [
            ("us-gaap:ProductAxis", "example:Widgets"),
        ]
    )

    duplicate = dimensional.replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
    )

    facts = extract_financial_facts_from_ixbrl(duplicate)

    assert len(facts) == 2
    assert facts[0].value == facts[1].value
    assert facts[0].dimensions == (
        ("us-gaap:ProductAxis", "example:Widgets"),
    )
    assert facts[1].dimensions == facts[0].dimensions

def test_different_dimensional_contexts_are_independent_fact_identities():
    dimensional = _ixbrl_with_explicit_members(
        [
            ("us-gaap:ProductAxis", "example:Widgets"),
        ]
    )

    expanded = dimensional.replace(
        """  <xbrli:unit id="USD">""",
        """  <xbrli:context id="FY2025">
    <xbrli:entity>
      <xbrli:identifier scheme="http://www.sec.gov/CIK">
        0000123456
      </xbrli:identifier>
      <xbrli:segment>
        <xbrldi:explicitMember
          dimension="us-gaap:ProductAxis">example:Gadgets</xbrldi:explicitMember>
      </xbrli:segment>
    </xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2025-01-01</xbrli:startDate>
      <xbrli:endDate>2025-12-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>

  <xbrli:unit id="USD">""",
        1,
    ).replace(
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>""",
        """    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2026"
      unitRef="USD"
      decimals="-3">1000000</ix:nonFraction>

    <ix:nonFraction
      name="us-gaap:Revenues"
      contextRef="FY2025"
      unitRef="USD"
      decimals="-3">2000000</ix:nonFraction>""",
        1,
    )

    facts = extract_financial_facts_from_ixbrl(expanded)

    assert len(facts) == 2
    assert facts[0].context_id == "FY2026"
    assert facts[0].dimensions == (
        ("us-gaap:ProductAxis", "example:Widgets"),
    )
    assert facts[1].context_id == "FY2025"
    assert facts[1].dimensions == (
        ("us-gaap:ProductAxis", "example:Gadgets"),
    )
    assert facts[0].value != facts[1].value


def test_valid_instant_context_fact_does_not_invalidate_duration_facts():
    raw = """
    <html
        xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
        xmlns:xbrli="http://www.xbrl.org/2003/instance"
        xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
    >
      <body>
        <xbrli:context id="FY2025">
          <xbrli:entity>
            <xbrli:identifier scheme="test">TEST</xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2024-09-29</xbrli:startDate>
            <xbrli:endDate>2025-09-27</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:context id="I2025">
          <xbrli:entity>
            <xbrli:identifier scheme="test">TEST</xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:instant>2025-09-27</xbrli:instant>
          </xbrli:period>
        </xbrli:context>

        <xbrli:unit id="USD">
          <xbrli:measure>iso4217:USD</xbrli:measure>
        </xbrli:unit>

        <ix:nonFraction
            name="dei:EntityPublicFloat"
            contextRef="I2025"
            unitRef="USD"
            decimals="-6"
        >3253431000000</ix:nonFraction>

        <ix:nonFraction
            name="us-gaap:Revenues"
            contextRef="FY2025"
            unitRef="USD"
            decimals="-6"
        >416161000000</ix:nonFraction>
      </body>
    </html>
    """

    facts = extract_financial_facts_from_ixbrl(raw)

    assert len(facts) == 1

    fact = facts[0]

    assert fact.concept == "us-gaap:Revenues"
    assert fact.context_id == "FY2025"
    assert fact.unit_ref == "USD"
    assert fact.value == Decimal("416161000000")
    assert fact.period_start == datetime(
        2024,
        9,
        29,
        tzinfo=timezone.utc,
    )
    assert fact.period_end == datetime(
        2025,
        9,
        27,
        tzinfo=timezone.utc,
    )


def test_fixed_zero_transformation_extracts_numeric_zero():
    raw = """
    <html
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2025"
    >
      <body>
        <xbrli:context id="FY2025">
          <xbrli:entity>
            <xbrli:identifier scheme="test">TEST</xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2024-09-29</xbrli:startDate>
            <xbrli:endDate>2025-09-27</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:unit id="usd">
          <xbrli:measure>iso4217:USD</xbrli:measure>
        </xbrli:unit>

        <ix:nonFraction
          name="us-gaap:ProceedsFromIssuanceOfLongTermDebt"
          contextRef="FY2025"
          unitRef="usd"
          decimals="-6"
          scale="6"
          format="ixt:fixed-zero"
        >—</ix:nonFraction>
      </body>
    </html>
    """

    facts = extract_financial_facts_from_ixbrl(raw)

    assert len(facts) == 1

    fact = facts[0]

    assert fact.concept == (
        "us-gaap:ProceedsFromIssuanceOfLongTermDebt"
    )
    assert fact.value == Decimal("0")
    assert fact.unit_ref == "usd"
    assert fact.context_id == "FY2025"
    assert fact.period_start == datetime(
        2024, 9, 29, tzinfo=timezone.utc
    )
    assert fact.period_end == datetime(
        2025, 9, 27, tzinfo=timezone.utc
    )
    assert fact.dimensions == ()
    assert fact.decimals == "-6"


def test_num_dot_decimal_transformation_applies_scale():
    raw = """
    <html
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2025"
    >
      <body>
        <xbrli:context id="c-12">
          <xbrli:entity>
            <xbrli:identifier scheme="test">TEST</xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2024-09-29</xbrli:startDate>
            <xbrli:endDate>2025-09-27</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:unit id="usd">
          <xbrli:measure>iso4217:USD</xbrli:measure>
        </xbrli:unit>

        <ix:nonFraction
          name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
          contextRef="c-12"
          unitRef="usd"
          decimals="-6"
          format="ixt:num-dot-decimal"
          scale="6"
        >307,003</ix:nonFraction>
      </body>
    </html>
    """

    facts = extract_financial_facts_from_ixbrl(raw)

    assert len(facts) == 1

    fact = facts[0]

    assert fact.concept == (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert fact.value == Decimal("307003000000")
    assert fact.unit_ref == "usd"
    assert fact.context_id == "c-12"
    assert fact.period_start == datetime(
        2024, 9, 29, tzinfo=timezone.utc
    )
    assert fact.period_end == datetime(
        2025, 9, 27, tzinfo=timezone.utc
    )
    assert fact.dimensions == ()
    assert fact.decimals == "-6"


def test_nested_nonfraction_text_is_extracted_for_outer_duration_fact():
    raw = """
    <html
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2025"
    >
      <body>
        <xbrli:context id="c-19">
          <xbrli:entity>
            <xbrli:identifier scheme="test">TEST</xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2022-09-25</xbrli:startDate>
            <xbrli:endDate>2023-09-30</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:context id="c-18">
          <xbrli:entity>
            <xbrli:identifier scheme="test">TEST</xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-10-01</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:context id="c-1">
          <xbrli:entity>
            <xbrli:identifier scheme="test">TEST</xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2024-09-29</xbrli:startDate>
            <xbrli:endDate>2025-09-27</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:unit id="number">
          <xbrli:measure>xbrli:pure</xbrli:measure>
        </xbrli:unit>

        <ix:nonFraction
          unitRef="number"
          contextRef="c-19"
          decimals="INF"
          name="us-gaap:EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate"
          scale="-2"
        ><ix:nonFraction
          unitRef="number"
          contextRef="c-18"
          decimals="INF"
          name="us-gaap:EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate"
          scale="-2"
        ><ix:nonFraction
          unitRef="number"
          contextRef="c-1"
          decimals="INF"
          name="us-gaap:EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate"
          scale="-2"
        >21</ix:nonFraction></ix:nonFraction></ix:nonFraction>
      </body>
    </html>
    """

    facts = extract_financial_facts_from_ixbrl(raw)

    by_context = {
        fact.context_id: fact
        for fact in facts
    }

    assert set(by_context) == {"c-19", "c-18", "c-1"}

    for context_id in ("c-19", "c-18", "c-1"):
        fact = by_context[context_id]

        assert fact.concept == (
            "us-gaap:"
            "EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate"
        )
        assert fact.value == Decimal("0.21")
        assert fact.unit_ref == "number"
        assert fact.dimensions == ()
        assert fact.decimals == "INF"


def test_nil_numeric_fact_is_ignored():
    raw = MINIMAL_IXBRL.replace(
        "</body>",
        """
        <ix:nonFraction
            name="us-gaap:CommitmentsAndContingencies"
            contextRef="FY2026"
            unitRef="USD"
            xsi:nil="true"
        />
        </body>
        """,
    ).replace(
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"',
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        1,
    )

    facts = extract_financial_facts_from_ixbrl(raw)

    assert all(
        fact.concept != "us-gaap:CommitmentsAndContingencies"
        for fact in facts
    )


def test_numwordsen_transformation_extracts_numeric_words():
    raw = """
    <html
        xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
        xmlns:xbrli="http://www.xbrl.org/2003/instance"
    >
      <body>
        <xbrli:context id="FY2025">
          <xbrli:entity>
            <xbrli:identifier scheme="test">
              TEST
            </xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2024-09-29</xbrli:startDate>
            <xbrli:endDate>2025-09-27</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:unit id="customer">
          <xbrli:measure>xbrli:pure</xbrli:measure>
        </xbrli:unit>

        <xbrli:unit id="vendor">
          <xbrli:measure>xbrli:pure</xbrli:measure>
        </xbrli:unit>

        <ix:nonFraction
            name="aapl:NumberOfCustomersWithSignificantAccountsReceivableBalance"
            contextRef="FY2025"
            unitRef="customer"
            decimals="INF"
            format="ixt-sec:numwordsen"
            scale="0"
        >one</ix:nonFraction>

        <ix:nonFraction
            name="aapl:NumberOfSignificantVendors"
            contextRef="FY2025"
            unitRef="vendor"
            decimals="INF"
            format="ixt-sec:numwordsen"
            scale="0"
        >two</ix:nonFraction>
      </body>
    </html>
    """

    facts = extract_financial_facts_from_ixbrl(raw)

    by_concept = {
        fact.concept: fact
        for fact in facts
    }

    assert (
        by_concept[
            "aapl:NumberOfCustomersWithSignificantAccountsReceivableBalance"
        ].value
        == Decimal("1")
    )

    assert (
        by_concept[
            "aapl:NumberOfSignificantVendors"
        ].value
        == Decimal("2")
    )


def test_unknown_numwordsen_value_fails_closed():
    raw = """
    <html
        xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
        xmlns:xbrli="http://www.xbrl.org/2003/instance"
    >
      <body>
        <xbrli:context id="FY2025">
          <xbrli:entity>
            <xbrli:identifier scheme="test">
              TEST
            </xbrli:identifier>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2024-09-29</xbrli:startDate>
            <xbrli:endDate>2025-09-27</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>

        <xbrli:unit id="pure">
          <xbrli:measure>xbrli:pure</xbrli:measure>
        </xbrli:unit>

        <ix:nonFraction
            name="test:UnsupportedNumericWord"
            contextRef="FY2025"
            unitRef="pure"
            decimals="INF"
            format="ixt-sec:numwordsen"
            scale="0"
        >three</ix:nonFraction>
      </body>
    </html>
    """

    with pytest.raises(
        FinancialFactValidationError,
        match="financial fact has invalid numeric content",
    ):
        extract_financial_facts_from_ixbrl(raw)
