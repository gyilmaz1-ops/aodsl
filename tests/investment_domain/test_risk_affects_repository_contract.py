from pathlib import Path
from typing import get_type_hints

from investment_domain.repository import EvidenceRepository


ROOT = Path(__file__).resolve().parents[2]

POSTGRES_REPOSITORY_PATH = (
    ROOT / "src" / "investment_domain" / "postgres_repository.py"
)


def _repository_source() -> str:
    return POSTGRES_REPOSITORY_PATH.read_text(
        encoding="utf-8"
    )


def _method_section(source: str) -> str:
    marker = "    def add_risk_affects("
    assert marker in source

    section = source.split(marker, 1)[1]

    next_method = section.find("\n    def ")
    if next_method != -1:
        section = section[:next_method]

    return marker + section


def test_repository_protocol_exposes_add_risk_affects():
    assert hasattr(
        EvidenceRepository,
        "add_risk_affects",
    )

    annotations = get_type_hints(
        EvidenceRepository.add_risk_affects
    )

    assert annotations["risk_id"] is str
    assert annotations["target_ids"] == tuple[str, ...]
    assert annotations["return"] is type(None)


def test_postgres_repository_exposes_add_risk_affects():
    source = _repository_source()

    assert "def add_risk_affects(" in source
    assert "risk_id: str" in source
    assert "target_ids: tuple[str, ...]" in source


def test_risk_affects_validates_aggregate_arguments():
    source = _method_section(
        _repository_source()
    )

    assert "risk_id must not be empty" in source
    assert (
        "risk_id must be a canonical Risk ID"
        in source
    )
    assert (
        "target_ids must be a non-empty tuple"
        in source
    )
    assert (
        "target_ids must contain non-empty strings"
        in source
    )
    assert (
        "target_ids must not contain duplicates"
        in source
    )


def test_risk_affects_is_specialized_to_affects():
    source = _method_section(
        _repository_source()
    )

    assert "AFFECTS" in source
    assert "Risk" in source

    # Target semantics may be expressed through NodeType constants rather
    # than duplicated raw string literals.
    assert (
        '"Claim"' in source
        or "'Claim'" in source
        or "NodeType.CLAIM" in source
    )
    assert (
        '"Forecast"' in source
        or "'Forecast'" in source
        or "NodeType.FORECAST" in source
    )


def test_risk_affects_uses_standard_endpoint_diagnostics():
    source = _method_section(
        _repository_source()
    )

    assert "IDM-W507: EDGE_SOURCE_NOT_FOUND" in source
    assert "IDM-W509: EDGE_SOURCE_TYPE_MISMATCH" in source
    assert "IDM-W508: EDGE_TARGET_NOT_FOUND" in source
    assert "IDM-W510: EDGE_TARGET_TYPE_MISMATCH" in source


def test_risk_affects_checks_risk_projection_integrity():
    source = _method_section(
        _repository_source()
    )

    assert "risk_facts" in source
    assert "RISK_PROJECTION_WRITE_LOST" in source
    assert "_assert_risk_projection_matches" in source
    assert "_assert_existing_node_matches" in source


def test_risk_affects_has_fail_closed_set_contract():
    source = _method_section(
        _repository_source()
    )

    assert "RISK_AFFECTS_SET_MISMATCH" in source

    assert (
        "existing != expected"
        in source
    )

    assert (
        "len(existing_rows) != len(expected)"
        in source
    )


def test_risk_affects_writes_canonical_domain_edges():
    source = _method_section(
        _repository_source()
    )

    assert "INSERT INTO domain_edges" in source
    assert "source_type" in source
    assert "edge_type" in source
    assert "target_type" in source

    assert (
        "NodeType.RISK.value"
        in source
        or "'Risk'" in source
        or '"Risk"' in source
    )

    assert (
        "EdgeType.AFFECTS.value"
        in source
        or "'AFFECTS'" in source
        or '"AFFECTS"' in source
    )


def test_risk_affects_verifies_persisted_set_after_write():
    source = _method_section(
        _repository_source()
    )

    assert "persisted_rows" in source
    assert "persisted" in source
    assert "expected" in source
    assert "RISK_AFFECTS_SET_MISMATCH" in source


def test_risk_affects_allows_valuation_target():
    source = _method_section(_repository_source())
    assert "NodeType.VALUATION" in source or "Valuation" in source


def test_risk_affects_reserves_w557_diagnostic():
    source = _method_section(_repository_source())
    assert "IDM-W557" in source
    assert "RISK_AFFECTS_SET_MISMATCH" in source
