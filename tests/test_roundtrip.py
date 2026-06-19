"""Corpus round-trip conformance: ``encode(decode(input)) == expectedFile`` byte-for-byte.

Parametrized over every ``node-round-trip`` / ``op-round-trip`` fixture in the
workspace corpus manifest — the canonical form is the F# host's output, so a pass
proves this host is byte-identical to it for that fixture.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.conformance import run_fixture


@corpus_required
@pytest.mark.parametrize(
    "fixture",
    fixtures_of("node-round-trip", "op-round-trip"),
    ids=lambda fx: fx["id"],
)
def test_roundtrip(fixture: dict) -> None:
    result = run_fixture(fixture, CORPUS_ROOT)
    assert result.passed, f"{fixture['id']}: {result.detail}"
