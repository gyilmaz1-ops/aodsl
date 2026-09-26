from __future__ import annotations

import inspect
from datetime import datetime

from investment_domain.nodes import Calculation
from investment_domain.repository import EvidenceRepository


def test_repository_exposes_calculation_pit_read():
    assert hasattr(EvidenceRepository, "calculation_at")


def test_calculation_pit_read_contract_signature():
    method = EvidenceRepository.calculation_at
    signature = inspect.signature(method)

    assert tuple(signature.parameters) == (
        "self",
        "calculation_id",
        "research_cutoff",
    )

    assert (
        signature.parameters["calculation_id"].annotation
        in (str, "str")
    )
    assert (
        signature.parameters["research_cutoff"].annotation
        in (datetime, "datetime")
    )


def test_calculation_pit_read_return_contract():
    method = EvidenceRepository.calculation_at
    annotation = inspect.signature(method).return_annotation

    assert annotation in (
        Calculation | None,
        "Calculation | None",
    )
