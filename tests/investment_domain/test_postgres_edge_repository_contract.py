from __future__ import annotations

import inspect

from investment_domain import PostgreSQLEvidenceRepository
from investment_domain.claims import ClaimEvidenceLink


def test_edge_write_method_has_typed_contract():
    signature = inspect.signature(
        PostgreSQLEvidenceRepository.add_claim_evidence_link
    )

    assert list(signature.parameters) == ["self", "link"]
    assert signature.parameters["link"].annotation in {
        ClaimEvidenceLink,
        "ClaimEvidenceLink",
    }
    assert signature.return_annotation in {
        None,
        "None",
    }


def test_edge_write_uses_domain_validators():
    source = inspect.getsource(
        PostgreSQLEvidenceRepository.add_claim_evidence_link
    )

    assert "validate_claim_evidence_link(link)" in source
    assert "validate_edge(edge)" in source


def test_edge_write_uses_explicit_claim_and_evidence_types():
    source = inspect.getsource(
        PostgreSQLEvidenceRepository.add_claim_evidence_link
    )

    assert "source_type=NodeType.CLAIM" in source
    assert "target_type=NodeType.EVIDENCE" in source


def test_edge_write_preserves_semantic_created_at():
    source = inspect.getsource(
        PostgreSQLEvidenceRepository.add_claim_evidence_link
    )

    assert "link.created_at" in source
    assert "stored_at" not in source


def test_edge_write_is_conflict_safe():
    source = inspect.getsource(
        PostgreSQLEvidenceRepository.add_claim_evidence_link
    )

    assert "ON CONFLICT DO NOTHING" in source
    assert "_assert_existing_edge_matches" in source


def test_edge_endpoint_integrity_is_checked():
    source = inspect.getsource(
        PostgreSQLEvidenceRepository.add_claim_evidence_link
    )

    assert source.count("_assert_edge_endpoint") == 2
    assert "IDM-W507" in source
    assert "IDM-W508" in source
    assert "IDM-W509" in source
    assert "IDM-W510" in source


def test_edge_collision_has_dedicated_diagnostic():
    source = inspect.getsource(
        PostgreSQLEvidenceRepository._assert_existing_edge_matches
    )

    assert "IDM-W506" in source
    assert "created_at" in source
