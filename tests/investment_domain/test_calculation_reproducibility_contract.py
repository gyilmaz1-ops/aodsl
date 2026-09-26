from __future__ import annotations

import inspect

from investment_domain.nodes import Calculation
from investment_domain.repository import EvidenceRepository


def test_repository_exposes_calculation_reproducibility_verification():
    assert hasattr(EvidenceRepository, "verify_calculation")


def test_calculation_reproducibility_contract_signature():
    method = EvidenceRepository.verify_calculation
    signature = inspect.signature(method)

    assert tuple(signature.parameters) == (
        "self",
        "calculation_id",
    )

    assert signature.parameters["calculation_id"].annotation in (
        str,
        "str",
    )


def test_calculation_reproducibility_return_contract():
    method = EvidenceRepository.verify_calculation
    annotation = inspect.signature(method).return_annotation

    assert annotation in (
        Calculation,
        "Calculation",
    )
