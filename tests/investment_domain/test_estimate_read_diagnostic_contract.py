import re
from pathlib import Path


SOURCE = Path(
    "src/investment_domain/postgres_repository.py"
).read_text()


EXPECTED = {
    "IDM-R583": "ESTIMATE_TYPE_MISMATCH",
    "IDM-R584": "INVALID_STORED_ESTIMATE",
    "IDM-R585": "ESTIMATE_INTEGRITY_FAILURE",
    "IDM-R586": "ESTIMATE_PROJECTION_NOT_FOUND",
    "IDM-R587": "ESTIMATE_PIT_AMBIGUITY",
}


def _diagnostic_pairs():
    return re.findall(
        r"(IDM-R58[3-7]): ([A-Z_]+)",
        SOURCE,
    )


def test_estimate_read_diagnostic_contract():
    pairs = set(_diagnostic_pairs())

    for code, name in EXPECTED.items():
        assert (code, name) in pairs


def test_estimate_read_codes_map_to_one_name():
    pairs = _diagnostic_pairs()

    for code, expected_name in EXPECTED.items():
        names = {
            name
            for found_code, name in pairs
            if found_code == code
        }
        assert names == {expected_name}


def test_estimate_read_names_map_to_one_code():
    pairs = _diagnostic_pairs()

    for expected_code, name in EXPECTED.items():
        codes = {
            code
            for code, found_name in pairs
            if found_name == name
        }
        assert codes == {expected_code}
