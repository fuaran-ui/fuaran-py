"""Corpus lenient-accept conformance (WIRE_FORMAT §16, normative).

Every §16 shorthand fixture MUST decode and re-encode to the verbose canonical
``expectedFile`` bytes. A host that rejects the shorthand, or normalises it to
different bytes, is non-conformant — and a host that silently skips this
family can pass certification while diverging, which is exactly what this
suite exists to prevent (the §16 MUST-run clause).
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.conformance import run_fixture


@corpus_required
@pytest.mark.parametrize(
    "fixture",
    fixtures_of("lenient-accept"),
    ids=lambda fx: fx["id"],
)
def test_lenient_accept(fixture: dict) -> None:
    result = run_fixture(fixture, CORPUS_ROOT)
    assert result.passed, f"{fixture['id']}: {result.detail}"


@corpus_required
def test_lenient_family_present() -> None:
    """The corpus carries the family — a silently-absent family cannot certify."""
    assert len(fixtures_of("lenient-accept")) > 0
