from __future__ import annotations

import inspect
from pathlib import Path

from investment_domain.repository import EvidenceRepository


def test_repository_protocol_exposes_forecast_at():
    assert hasattr(EvidenceRepository, "forecast_at")


def test_forecast_at_signature_is_frozen():
    signature = inspect.signature(EvidenceRepository.forecast_at)

    assert tuple(signature.parameters) == (
        "self",
        "forecast_id",
        "research_cutoff",
    )


def test_repository_protocol_exposes_latest_forecast_at():
    assert hasattr(EvidenceRepository, "latest_forecast_at")


def test_latest_forecast_at_signature_is_frozen():
    signature = inspect.signature(
        EvidenceRepository.latest_forecast_at
    )

    assert tuple(signature.parameters) == (
        "self",
        "subject_id",
        "scenario",
        "research_cutoff",
    )


def test_forecast_read_diagnostics_are_reserved():
    source = Path(
        "src/investment_domain/postgres_repository.py"
    ).read_text()

    expected = (
        "IDM-R573: FORECAST_TYPE_MISMATCH",
        "IDM-R574: INVALID_STORED_FORECAST",
        "IDM-R575: FORECAST_INTEGRITY_FAILURE",
        "IDM-R576: FORECAST_PROJECTION_NOT_FOUND",
        "IDM-R577: FORECAST_PIT_AMBIGUITY",
    )

    for diagnostic in expected:
        assert diagnostic in source


def test_forecast_read_diagnostic_codes_do_not_collide():
    source = Path(
        "src/investment_domain/postgres_repository.py"
    ).read_text()

    expected = {
        "IDM-R573:": "FORECAST_TYPE_MISMATCH",
        "IDM-R574:": "INVALID_STORED_FORECAST",
        "IDM-R575:": "FORECAST_INTEGRITY_FAILURE",
        "IDM-R576:": "FORECAST_PROJECTION_NOT_FOUND",
        "IDM-R577:": "FORECAST_PIT_AMBIGUITY",
    }

    for code, name in expected.items():
        occurrences = [
            line.strip()
            for line in source.splitlines()
            if code in line
        ]
        assert occurrences
        assert all(name in line for line in occurrences)
