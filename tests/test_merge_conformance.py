"""Merge-conformance: the Python host reproduces the F# host's merge result exactly.

Mirrors the F#/TS ``merge.test.ts`` Leg-B gate over the workspace
``wire-format-fixtures/merge-conformance/`` corpus. Two fixture kinds:

* ``merge-3way`` — ``encode(merge_3way(base, a, b))`` is byte-identical to the
  committed ``expectedFile`` and ``sha256`` of those bytes == the manifest
  ``outcomeHash`` (the SemanticStyle sub-field blend + the NodeId-byte tie-break).
* ``merge-refusal`` — held under the manifest's SEPARATE ``refusalFixtures``
  key: decode base/a/b, assert the merge REFUSES, and assert the canonically
  encoded two-sided conflict envelope is byte-equal to ``envelopeFile`` with
  ``sha256`` == ``envelopeHash``. Swapping a and b must TRANSPOSE each entry's
  ``a`` and ``b`` and change nothing else. The key is separate because a host
  iterating ``fixtures`` and expecting every entry to auto-merge is correct to do
  so — and a host that never reads this key certifies nothing about what it does
  when it refuses.
* ``merge-validator-gated`` — a structurally-clean merge that *introduces* a
  domain-validity defect (present in the merged tree but in neither parent) is a
  semantic conflict; the deterministic artifact is the **verdict** (the
  introduced-defect set, canonically encoded). The sample domain validator + the
  introduced-defect diff + the verdict codec are ported **test-side**, exactly as
  the F#/TS hosts port them — the invariant is a documented sample, not a host API.
"""

from __future__ import annotations

import hashlib

import pytest

from _corpus import MERGE_CORPUS_ROOT, merge_corpus_required, merge_fixtures, merge_refusal_fixtures
from _merge_sample_validator import encode_verdict as _encode_verdict
from _merge_sample_validator import introduced_defects as _introduced_defects
from fuaran_ui import decode_node, encode_node
from fuaran_ui.merge import encode_envelope, merge_3way
from fuaran_ui.model import Node


def _read(rel: str) -> str:
    text = (MERGE_CORPUS_ROOT / rel).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def _sha256hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _decode_or_raise(rel: str) -> Node:
    result = decode_node(_read(rel))
    assert result.ok, f"decode {rel} failed: {result.error}"
    return result.value


# ─── the gate ─────────────────────────────────────────────────────────────────


@merge_corpus_required
@pytest.mark.parametrize("fixture", merge_fixtures(), ids=lambda fx: fx["id"])
def test_merge_conformance(fixture: dict) -> None:
    base = _decode_or_raise(fixture["baseFile"])
    a = _decode_or_raise(fixture["aFile"])
    b = _decode_or_raise(fixture["bFile"])

    result = merge_3way(base, a, b)
    assert result.ok, f"{fixture['id']}: unexpected conflicts: {result}"

    if fixture["kind"] == "merge-3way":
        merged_bytes = encode_node(result.tree)
        assert merged_bytes == _read(fixture["expectedFile"]), fixture["id"]
        assert _sha256hex(merged_bytes) == fixture["outcomeHash"], fixture["id"]
    else:  # merge-validator-gated
        introduced = _introduced_defects(a, b, result.tree)
        # The fixture exists to introduce a defect — guard against a silent no-op.
        assert len(introduced) > 0, f"{fixture['id']}: expected an introduced defect"
        verdict_bytes = _encode_verdict(introduced)
        assert verdict_bytes == _read(fixture["verdictFile"]), fixture["id"]
        assert _sha256hex(verdict_bytes) == fixture["verdictHash"], fixture["id"]


# ─── the refusal envelope (the manifest's separate ``refusalFixtures`` key) ────


@merge_corpus_required
@pytest.mark.parametrize("fixture", merge_refusal_fixtures(), ids=lambda fx: fx["id"])
def test_merge_refusal_envelope(fixture: dict) -> None:
    base = _decode_or_raise(fixture["baseFile"])
    a = _decode_or_raise(fixture["aFile"])
    b = _decode_or_raise(fixture["bFile"])

    result = merge_3way(base, a, b)
    assert not result.ok, f"{fixture['id']}: expected a refusal, got a clean merge"

    envelope = encode_envelope(result.conflicts)
    assert envelope == _read(fixture["envelopeFile"]), fixture["id"]
    assert _sha256hex(envelope) == fixture["envelopeHash"], fixture["id"]


@merge_corpus_required
@pytest.mark.parametrize("fixture", merge_refusal_fixtures(), ids=lambda fx: fx["id"])
def test_merge_refusal_envelope_transposes_on_swap(fixture: dict) -> None:
    """Swapping the branches transposes each entry's ``a`` and ``b`` and changes
    nothing else.

    This is the law the two-sided envelope exists for: two replicas that merged
    the same pair in opposite orders must agree about what the other side wanted.
    A host that populated only one side, or that let the class / base / facet /
    order depend on argument position, passes the byte-equality test above and
    fails here.
    """
    base = _decode_or_raise(fixture["baseFile"])
    a = _decode_or_raise(fixture["aFile"])
    b = _decode_or_raise(fixture["bFile"])

    forward = merge_3way(base, a, b)
    swapped = merge_3way(base, b, a)
    assert not forward.ok and not swapped.ok, fixture["id"]

    fwd = sorted(forward.conflicts, key=lambda c: (c.node_id, c.facet))
    swp = sorted(swapped.conflicts, key=lambda c: (c.node_id, c.facet))
    assert len(fwd) == len(swp), fixture["id"]
    for f, s in zip(fwd, swp, strict=True):
        assert (f.node_id, f.facet, f.conflict_class, f.base) == (s.node_id, s.facet, s.conflict_class, s.base)
        assert f.a == s.b, f"{fixture['id']}: {f.facet} — a did not transpose onto b"
        assert f.b == s.a, f"{fixture['id']}: {f.facet} — b did not transpose onto a"
    # Non-vacuity: a fixture whose two sides happen to be equal would satisfy the
    # law without exercising it.
    assert any(f.a != f.b for f in fwd), f"{fixture['id']}: the sides are identical, so the law is untested"
