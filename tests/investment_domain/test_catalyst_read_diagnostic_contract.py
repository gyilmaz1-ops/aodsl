from pathlib import Path

SOURCE = Path(
    "src/investment_domain/postgres_repository.py"
).read_text(encoding="utf-8")


def test_catalyst_type_mismatch_diagnostic_exists():
    assert "IDM-R578: CATALYST_TYPE_MISMATCH" in SOURCE


def test_invalid_stored_catalyst_diagnostic_exists():
    assert "IDM-R579: INVALID_STORED_CATALYST" in SOURCE


def test_catalyst_integrity_failure_diagnostic_exists():
    assert "IDM-R580: CATALYST_INTEGRITY_FAILURE" in SOURCE


def test_catalyst_projection_not_found_diagnostic_exists():
    assert "IDM-R581: CATALYST_PROJECTION_NOT_FOUND" in SOURCE


def test_catalyst_pit_ambiguity_diagnostic_exists():
    assert "IDM-R582: CATALYST_PIT_AMBIGUITY" in SOURCE
