from datetime import datetime, timezone

from investment_domain import canonical_id


def test_identity_is_order_independent():
    a = canonical_id("claim", {
        "predicate": "growth_accelerating",
        "subject_id": "company:x",
        "as_of": datetime(2026, 1, 1, tzinfo=timezone.utc),
    })
    b = canonical_id("claim", {
        "as_of": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "subject_id": "company:x",
        "predicate": "growth_accelerating",
    })
    assert a == b


def test_security_identity_normalization():
    assert canonical_id(
        "security", {"venue": "NASDAQ", "ticker": "NVDA"}
    ) == "security:nasdaq:nvda"
