"""Phase 2313 — an all-default style is stored as the ABSENT slot.

Certifies this host against the shared ``apply/style-normalise-apply.json``
family: each vector's tree and op are decoded by this host's own decoders and
applied, and the canonical re-encoding of the applied tree must equal
``expected.tree`` byte for byte. The family pins RESULT bytes, because the
property under test is that two hosts applying one op to one tree agree on
those bytes: a style result equal to the default record encodes with no
``style`` member, never ``"style":{}``.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui import decode_node
from fuaran_ui.ops import apply, decode_op
from fuaran_ui.ops.apply import Ok
from fuaran_ui.schema.encode import encode_node

APPLY_CORPUS_ROOT = CORPUS_ROOT / "apply"
_FAMILY_ID = "styleNormaliseApply"


def _family() -> tuple[dict, list[dict]]:
    manifest = json.loads((APPLY_CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    entry = next((f for f in manifest["families"] if f["id"] == _FAMILY_ID), None)
    assert entry is not None, f"apply/manifest.json declares no {_FAMILY_ID} family"
    body = json.loads((APPLY_CORPUS_ROOT / entry["file"]).read_text(encoding="utf-8"))
    return entry, list(body["vectors"])


def _vectors() -> list[dict]:
    if not (APPLY_CORPUS_ROOT / "manifest.json").is_file():
        return []
    return _family()[1]


# The snapshot carries apply/, so a missing manifest is a broken copy and must
# read RED, not as a skip.
def test_the_family_is_present_and_complete() -> None:
    assert (APPLY_CORPUS_ROOT / "manifest.json").is_file(), f"apply corpus not found at {APPLY_CORPUS_ROOT}"
    entry, vectors = _family()
    assert len(vectors) == entry["vectors"], (
        f"{_FAMILY_ID} holds {len(vectors)} vectors, the manifest declares {entry['vectors']}"
    )
    assert vectors, "an empty family certifies nothing"


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["id"])
def test_style_normalise_apply_vector(vector: dict) -> None:
    tree = decode_node(vector["input"]["tree"])
    assert tree.ok, f"the tree did not decode: {tree}"
    op = decode_op(vector["input"]["op"])
    assert op.ok, f"the op did not decode: {op}"

    expected = vector["expected"]
    assert expected["verdict"] == "accept", f"unknown verdict {expected['verdict']!r}"
    result = apply(op.value, tree.value)
    assert isinstance(result, Ok), f"expected accept, refused: {result}"
    assert encode_node(result.value) == expected["tree"]
