from __future__ import annotations

import re
from typing import Any

from .canonical import canonical_sha256


_TOKEN = re.compile(r"[^a-z0-9._-]+")


def _token(value: str) -> str:
    value = value.strip().lower()
    value = _TOKEN.sub("-", value).strip("-")
    if not value:
        raise ValueError("identity token must not be empty")
    return value


def canonical_id(kind: str, payload: Any) -> str:
    kind = _token(kind)

    if kind == "company":
        if not isinstance(payload, dict) or not payload.get("canonical_name"):
            raise ValueError("company identity requires canonical_name")
        return f"company:{_token(payload['canonical_name'])}"

    if kind == "security":
        if not isinstance(payload, dict):
            raise ValueError("security identity requires mapping payload")
        venue = _token(payload.get("venue", ""))
        ticker = _token(payload.get("ticker", ""))
        return f"security:{venue}:{ticker}"

    return f"{kind}:{canonical_sha256(payload)}"
