from datetime import datetime, timezone

import pytest

from investment_domain.repository import EvidenceRepository


UTC = timezone.utc


def test_repository_protocol_exposes_valuation_input_resolution():
    assert hasattr(
        EvidenceRepository,
        "valuation_inputs_at",
    )


def test_valuation_input_resolution_contract_is_point_in_time():
    """
    Contract guard:

    Valuation input resolution must require an explicit research cutoff.
    This prevents a valuation from silently seeing information that was
    unavailable at the valuation/research point in time.
    """
    method = getattr(EvidenceRepository, "valuation_inputs_at", None)

    assert method is not None

    import inspect

    parameters = inspect.signature(method).parameters

    assert "valuation_id" in parameters
    assert "research_cutoff" in parameters


def test_valuation_input_resolution_contract_returns_dependency_nodes():
    """
    The resolver is a read-side operation over the persisted immutable
    Valuation DEPENDS_ON aggregate.

    It must resolve dependency nodes, not merely return edge IDs.
    """
    method = getattr(EvidenceRepository, "valuation_inputs_at", None)

    assert method is not None

    annotation = inspect_return_annotation(method)

    # Keep the contract deliberately structural at this stage.
    # Exact collection type will be frozen after the first implementation.
    assert annotation is not None


def inspect_return_annotation(method):
    import inspect

    annotation = inspect.signature(method).return_annotation

    if annotation is inspect.Signature.empty:
        return None

    return annotation
