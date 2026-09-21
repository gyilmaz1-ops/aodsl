from pathlib import Path
import re


POSTGRES_IMAGE = (
    "postgres@sha256:"
    "a3b7f434b2dc57ce85a67e171163eb8ab1a1ebcb39d27484661f26b1dfbe30d6"
)

FILES = (
    Path("deploy/docker-compose.certification.yml"),
    Path(".github/workflows/postgres-certification.yml"),
)


def _image_references(path: Path) -> list[str]:
    refs = []

    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(
            r"^\s*image:\s*(\S+)\s*$",
            line,
        )
        if match:
            refs.append(match.group(1))

    return refs


def test_postgres_certification_image_is_immutable() -> None:
    for path in FILES:
        refs = [
            ref
            for ref in _image_references(path)
            if ref.startswith("postgres")
        ]

        assert refs == [POSTGRES_IMAGE]


def test_postgres_certification_uses_same_image_everywhere() -> None:
    refs = []

    for path in FILES:
        refs.extend(
            ref
            for ref in _image_references(path)
            if ref.startswith("postgres")
        )

    assert refs == [POSTGRES_IMAGE, POSTGRES_IMAGE]


def test_mutable_postgres_tag_is_forbidden() -> None:
    for path in FILES:
        text = path.read_text(encoding="utf-8")

        assert "postgres:16" not in text
        assert "postgres:latest" not in text
        assert re.search(
            r"postgres@sha256:[0-9a-f]{64}",
            text,
        )
