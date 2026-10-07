from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.sec_financial_facts import ExtractedFinancialFact


UTC = timezone.utc


def utc(year, month, day):
    return datetime(year, month, day, tzinfo=UTC)


def revenue_fact(
    *,
    value=Decimal("300000000000"),
    period_start=utc(2026, 1, 1),
    period_end=utc(2026, 9, 26),
    context_id="FY2026",
    unit_ref="USD",
    dimensions=(),
):
    return ExtractedFinancialFact(
        concept="us-gaap:Revenues",
        value=value,
        unit_ref=unit_ref,
        period_start=period_start,
        period_end=period_end,
        context_id=context_id,
        dimensions=dimensions,
        decimals="-6",
    )


def unsupported_fact():
    return replace(
        revenue_fact(),
        concept="us-gaap:OperatingIncomeLoss",
        value=Decimal("90000000000"),
    )


def selector():
    from investment_domain.sec_financial_fact_selection import (
        select_revenue_fact,
    )

    return select_revenue_fact


def test_selects_single_matching_revenue_fact():
    selected = selector()(
        (revenue_fact(),),
        effective_at=utc(2026, 9, 26),
    )

    assert selected == revenue_fact()


def test_ignores_unsupported_concepts():
    expected = revenue_fact()

    selected = selector()(
        (
            unsupported_fact(),
            expected,
        ),
        effective_at=utc(2026, 9, 26),
    )

    assert selected == expected


def test_ignores_dimensioned_revenue_candidate():
    expected = revenue_fact()

    dimensioned = replace(
        expected,
        context_id="SEGMENT",
        dimensions=(
            ("us-gaap:ProductAxis", "example:Widgets"),
        ),
    )

    selected = selector()(
        (
            dimensioned,
            expected,
        ),
        effective_at=utc(2026, 9, 26),
    )

    assert selected == expected


def test_ignores_non_usd_revenue_candidate():
    expected = revenue_fact()

    selected = selector()(
        (
            replace(
                expected,
                context_id="EUR",
                unit_ref="EUR",
            ),
            expected,
        ),
        effective_at=utc(2026, 9, 26),
    )

    assert selected == expected


def test_ignores_revenue_from_different_period_end():
    expected = revenue_fact()

    prior = replace(
        expected,
        value=Decimal("250000000000"),
        context_id="FY2025",
        period_start=utc(2025, 1, 1),
        period_end=utc(2025, 9, 27),
    )

    selected = selector()(
        (
            prior,
            expected,
        ),
        effective_at=utc(2026, 9, 26),
    )

    assert selected == expected


def test_no_matching_revenue_fails_closed():
    from investment_domain.sec_financial_fact_selection import (
        SecFinancialFactSelectionError,
    )

    with pytest.raises(
        SecFinancialFactSelectionError,
        match="no eligible revenue fact",
    ):
        selector()(
            (unsupported_fact(),),
            effective_at=utc(2026, 9, 26),
        )


def test_semantically_distinct_matching_revenues_fail_closed():
    from investment_domain.sec_financial_fact_selection import (
        SecFinancialFactSelectionError,
    )

    first = revenue_fact(
        value=Decimal("300000000000"),
        context_id="FY2026-A",
    )
    second = revenue_fact(
        value=Decimal("310000000000"),
        context_id="FY2026-B",
    )

    with pytest.raises(
        SecFinancialFactSelectionError,
        match="ambiguous revenue facts",
    ):
        selector()(
            (first, second),
            effective_at=utc(2026, 9, 26),
        )


def test_identical_matching_revenues_collapse_deterministically():
    first = revenue_fact()
    second = revenue_fact()

    selected = selector()(
        (first, second),
        effective_at=utc(2026, 9, 26),
    )

    assert selected == first


def test_selection_is_order_independent_for_identical_candidates():
    first = revenue_fact()
    second = revenue_fact()

    forward = selector()(
        (first, second),
        effective_at=utc(2026, 9, 26),
    )
    reverse = selector()(
        (second, first),
        effective_at=utc(2026, 9, 26),
    )

    assert forward == reverse


def test_effective_at_must_be_timezone_aware():
    from investment_domain.sec_financial_fact_selection import (
        SecFinancialFactSelectionError,
    )

    with pytest.raises(
        SecFinancialFactSelectionError,
        match="effective_at must be timezone-aware",
    ):
        selector()(
            (revenue_fact(),),
            effective_at=datetime(2026, 9, 26),
        )
