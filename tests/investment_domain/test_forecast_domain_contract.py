from datetime import datetime, timezone

import pytest

from investment_domain import Forecast, canonical_id, validate_node
from investment_domain.validation import DomainValidationError


UTC = timezone.utc


def _forecast(**overrides):
    payload = {
        "subject_id": "security:nasdaq:nvda",
        "scenario": "BASE",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "model_version": "1",
    }
    payload.update(overrides)
    return Forecast(
        id=canonical_id("forecast", payload),
        **payload,
    )


def test_valid_forecast_accepted():
    validate_node(_forecast())


@pytest.mark.parametrize(
    "scenario",
    [
        "base",
        "BASE ",
        "UPSIDE",
        "UNKNOWN",
    ],
)
def test_forecast_rejects_unsupported_scenario(scenario):
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(_forecast(scenario=scenario))


def test_forecast_identity_changes_with_scenario():
    base = _forecast(scenario="BASE")
    bull = _forecast(scenario="BULL")

    assert base.id != bull.id


def test_forecast_identity_changes_with_model_version():
    v1 = _forecast(model_version="1")
    v2 = _forecast(model_version="2")

    assert v1.id != v2.id


def test_forecast_identity_changes_with_subject():
    nvda = _forecast(subject_id="security:nasdaq:nvda")
    amd = _forecast(subject_id="security:nasdaq:amd")

    assert nvda.id != amd.id
