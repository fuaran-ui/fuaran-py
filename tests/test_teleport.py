"""Unit tests for the §17 teleport state bundle.

There is no teleport fixture family in the shared corpus (the cross-host
byte-identical reference-encoder certification is a documented follow-on), so
this host certifies its own round-trip + the digest / size / version rejects +
the budget — mirroring the Go ``teleport/teleport_test.go`` case-for-case.
"""

from __future__ import annotations

import base64
import zlib

import pytest

from fuaran_py.model import Obj
from fuaran_py.schema import decode_node
from fuaran_py.teleport import (
    BUDGET_QR_COMFORTABLE,
    DIGEST_MISMATCH,
    INVALID_ENVELOPE,
    INVALID_FORMAT,
    OVERSIZE,
    TREE_INVALID,
    UNSUPPORTED_VERSION,
    Bundle,
    TeleportError,
    decode,
    encode,
    encode_within,
)

_ZERO_DIGEST = "0" * 64
_VALID_TREE = '{"id":"a","kind":{"$type":"Skeleton","rows":1}}'


def _node(text: str):
    result = decode_node(text)
    assert result.ok, result.error
    return result.value


def _raw_bundle(envelope_json: str) -> str:
    """Wrap arbitrary envelope JSON into an FT1 string (no digest recomputation)."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    compressed = compressor.compress(envelope_json.encode("utf-8")) + compressor.flush()
    return "FT1." + base64.urlsafe_b64encode(compressed).rstrip(b"=").decode("ascii")


def _exemplar() -> Bundle:
    tree = _node(
        '{"id":"root","kind":{"$type":"Box","children":['
        '{"id":"h","kind":{"$type":"Heading","level":1,"text":{"$type":"Literal","text":"Onboarding"},"variant":"Standard"}},'
        '{"id":"name","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"Step 1"}}}'
        '],"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"role":"Group"}}'
    )
    return Bundle(
        tree=tree,
        state={"step": 2, "name": "Ada", "ratio": 0.75},
        history=(
            Obj("UpdateProp", {"path": "Text", "target": "name", "value": "Step 2"}),
            Obj("RemoveNode", {"target": "h"}),
        ),
        chain_head="ab" * 32,
    )


def test_round_trip_byte_exact_and_deterministic() -> None:
    bundle = _exemplar()
    s1 = encode(bundle)
    assert s1.startswith("FT1.")
    assert encode(bundle) == s1  # deterministic
    decoded = decode(s1)
    assert encode(decoded) == s1  # round-trip byte-exact
    assert decoded.state["step"] == 2
    assert len(decoded.history) == 2
    assert decoded.chain_head is not None


def test_digest_binds_state() -> None:
    a = _exemplar()
    b = Bundle(tree=a.tree, state={"step": 99})
    assert encode(a) != encode(b), "digest must bind state"
    stale = _raw_bundle(f'{{"bundle":"teleport@1","digest":"{_ZERO_DIGEST}","tree":{_VALID_TREE}}}')
    with pytest.raises(TeleportError) as exc:
        decode(stale)
    assert exc.value.kind == DIGEST_MISMATCH


def test_tree_invalid_refuses_bad_identity() -> None:
    dup = _node(
        '{"id":"root","kind":{"$type":"Box","children":['
        '{"id":"dup","kind":{"$type":"Skeleton","rows":1}},'
        '{"id":"dup","kind":{"$type":"Skeleton","rows":2}}'
        '],"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"role":"Group"}}'
    )
    with pytest.raises(TeleportError) as exc:
        decode(encode(Bundle(tree=dup)))
    assert exc.value.kind == TREE_INVALID


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("not-a-bundle", INVALID_FORMAT),
        ("FT1.!!!!", INVALID_FORMAT),
        ("FT1." + "A" * (64 * 1024), OVERSIZE),
        (
            _raw_bundle(f'{{"bundle":"teleport@2","digest":"{_ZERO_DIGEST}","tree":{_VALID_TREE}}}'),
            UNSUPPORTED_VERSION,
        ),
        (_raw_bundle(f'{{"bundle":"teleport@1","digest":"{_ZERO_DIGEST}"}}'), INVALID_ENVELOPE),
    ],
    # Explicit ids — the oversize case's 64K input must never leak into the node
    # id (Windows caps the PYTEST_CURRENT_TEST env var at 32767 chars).
    ids=["missing-prefix", "bad-base64", "oversize-input", "unsupported-version", "missing-tree"],
)
def test_rejects(text: str, want: str) -> None:
    with pytest.raises(TeleportError) as exc:
        decode(text)
    assert exc.value.kind == want


def test_budget_guard() -> None:
    bundle = _exemplar()
    # The exemplar fits the QR-comfortable budget.
    encode_within(bundle, BUDGET_QR_COMFORTABLE)
    # A pathological tiny budget refuses with Oversize.
    with pytest.raises(TeleportError) as exc:
        encode_within(bundle, 10)
    assert exc.value.kind == OVERSIZE
