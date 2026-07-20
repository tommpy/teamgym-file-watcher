"""Shared pytest fixtures."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import pytest


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@pytest.fixture
def make_jwt() -> Callable[[dict[str, object]], str]:
    """Build an unsigned (header.payload.signature) JWT string from payload claims."""

    def _make(payload: dict[str, object]) -> str:
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
        body = _b64url(json.dumps(payload).encode("utf-8"))
        return f"{header}.{body}.signature"

    return _make
