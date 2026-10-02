from pathlib import Path
from typing import get_type_hints

from investment_domain.nodes import Risk
from investment_domain.repository import EvidenceRepository


ROOT = Path(__file__).resolve().parents[2]
POSTGRES_REPOSITORY_PATH = ROOT / "src" / "investment_domain" / "postgres_repository.py"


def _repository_source() -> str:
    return POSTGRES_REPOSITORY_PATH.read_text(encoding="utf-8")


def _method_section(name: str) -> str:
    source = _repository_source()
    marker = f"    def {name}("
    assert marker in source
    section = source.split(marker, 1)[1]
    next_method = section.find("\n    def ")
    if next_method != -1:
        section = section[:next_method]
    return marker + section


def test_repository_protocol_exposes_risk_at():
    assert hasattr(EvidenceRepository, "risk_at")
    annotations = get_type_hints(EvidenceRepository.risk_at)
    assert annotations["risk_id"] is str
    assert annotations["research_cutoff"].__name__ == "datetime"
    assert annotations["return"] == Risk | None


def test_repository_protocol_exposes_latest_risk_at():
    assert hasattr(EvidenceRepository, "latest_risk_at")
    annotations = get_type_hints(EvidenceRepository.latest_risk_at)
    assert annotations["subject_id"] is str
    assert annotations["research_cutoff"].__name__ == "datetime"
    assert annotations["return"] == Risk | None


def test_risk_at_has_exact_id_validation_contract():
    source = _method_section("risk_at")
    assert "risk_id must be a canonical Risk ID" in source
    assert "research_cutoff must be timezone-aware" in source


def test_risk_at_has_fail_closed_read_diagnostics():
    source = _method_section("risk_at")
    assert "IDM-R558: RISK_TYPE_MISMATCH" in source
    assert "IDM-R559: INVALID_STORED_RISK" in source
    assert "IDM-R560: RISK_INTEGRITY_FAILURE" in source
    assert "IDM-R561: RISK_PROJECTION_NOT_FOUND" in source


def test_risk_at_reads_canonical_anchor_and_projection():
    source = _method_section("risk_at")
    assert "domain_nodes" in source
    assert "risk_facts" in source
    assert "canonical_payload" in source
    assert "payload_hash" in source


def test_risk_at_enforces_pit_visibility():
    source = _method_section("risk_at")
    assert "risk.as_of > research_cutoff" in source
    assert "return None" in source


def test_latest_risk_at_validates_arguments():
    source = _method_section("latest_risk_at")
    assert "subject_id must not be empty" in source
    assert "research_cutoff must be timezone-aware" in source


def test_latest_risk_at_is_visibility_first():
    source = _method_section("latest_risk_at")
    assert "risk_facts" in source
    assert "MAX(as_of)" in source
    assert "as_of <= %s" in source


def test_latest_risk_at_fails_closed_on_pit_ambiguity():
    source = _method_section("latest_risk_at")
    assert "IDM-R562: RISK_PIT_AMBIGUITY" in source
    assert "len(rows) != 1" in source


def test_latest_risk_at_delegates_selected_integrity_to_risk_at():
    source = _method_section("latest_risk_at")
    assert "self.risk_at(" in source
    assert "research_cutoff" in source
