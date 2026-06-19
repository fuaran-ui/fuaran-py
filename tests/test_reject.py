"""Corpus reject conformance: malformed input fails with the canonical code + path.

Parametrized over every ``reject`` fixture: ``decode(input)`` must fail, the
error ``code`` must equal ``expectedErrorCode``, and ``path`` must start with
``expectedPath``.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.conformance import run_fixture


@corpus_required
@pytest.mark.parametrize("fixture", fixtures_of("reject"), ids=lambda fx: fx["id"])
def test_reject(fixture: dict) -> None:
    result = run_fixture(fixture, CORPUS_ROOT)
    assert result.passed, f"{fixture['id']}: {result.detail}"
