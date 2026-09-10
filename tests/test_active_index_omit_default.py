"""fuaran#1585 — ``Tabs.activeIndex`` is omit-at-default, and the explicit form is still read.

The sibling of ``test_stacked_omit_default.py``, landed one step behind it. Until
1585 the encoder always emitted ``activeIndex`` while every host's decoder already
restored the ``Static`` binding carrying ``0`` on absence — a tolerance five hosts
happened to share rather than a stated rule. The IDL now declares the member
``omitDefault Static{value=0}``, so the omission is the CONTRACT: the encoder
omits at that identity, the decoder restores it, and the corpus's tabs fixtures
carry the shorter bytes.

The half worth testing here is the one a corpus of re-emitted fixtures cannot
state, because every fixture carrying the identity now omits the member: that a
document carrying the OLD explicit ``{"$type":"Static","value":0}`` still decodes
to exactly the same tree.

Two things make this harder than the ``stacked`` case, and both are tested below.
The identity is one inhabitant of a union whose payload domain is unbounded, so
the omit test is on the CASE **and** its PAYLOAD — a ``Static`` carrying any other
index must still ride, and so must every other binding case; an encoder testing
only the tag would silently discard a document's authored tab. And the
normalisation half is not free in this host: ``Tabs`` decodes through the
schema-driven path, whose model re-emits exactly the fields present, so an
explicit identity would be carried through and re-emitted verbatim unless the
decoder drops it — which would make the pre-phase spelling a SECOND canonical form
rather than a lenient accept.
"""

from __future__ import annotations

import pytest

from fuaran_ui.result import Ok
from fuaran_ui.schema import decode_node, encode_node

_CHILD = '{"id":"p1","kind":{"$type":"Markdown","text":"one"}}'

_OMITTED = f'{{"id":"t1","kind":{{"$type":"Tabs","children":[{_CHILD}]}}}}'

_EXPLICIT_ZERO = (
    f'{{"id":"t1","kind":{{"$type":"Tabs","activeIndex":{{"$type":"Static","value":0}},"children":[{_CHILD}]}}}}'
)

_EXPLICIT_ONE = (
    f'{{"id":"t1","kind":{{"$type":"Tabs","activeIndex":{{"$type":"Static","value":1}},"children":[{_CHILD}]}}}}'
)

_STATE_BOUND = (
    f'{{"id":"t1","kind":{{"$type":"Tabs","activeIndex":{{"$type":"State","key":"pane"}},"children":[{_CHILD}]}}}}'
)


def _decoded(raw: str):
    result = decode_node(raw)
    assert isinstance(result, Ok), f"{raw} did not decode: {result}"
    return result.value


def test_omitted_form_round_trips() -> None:
    """The canonical form carries no ``activeIndex`` and re-encodes unchanged."""
    assert encode_node(_decoded(_OMITTED)) == _OMITTED


def test_explicit_identity_decodes_identically() -> None:
    """Read-compat: the pre-phase spelling reaches the same tree, and normalises.

    Equality of the re-encoded bytes is the strongest statement available here —
    two models that encode to the same canonical bytes ARE the same document, and
    that is the property every other host is held to as well.
    """
    assert encode_node(_decoded(_EXPLICIT_ZERO)) == encode_node(_decoded(_OMITTED))
    # …and the shared bytes are the SHORT ones: the explicit spelling is a §3.6
    # lenient accept, not a second canonical form.
    assert encode_node(_decoded(_EXPLICIT_ZERO)) == _OMITTED


@pytest.mark.parametrize("raw", [_EXPLICIT_ONE, _STATE_BOUND])
def test_non_identity_bindings_are_still_carried(raw: str) -> None:
    """Anything but the identity rides the wire both ways.

    ``Static 1`` and a ``State`` binding are the two failure modes an omit test
    written on the tag alone would produce: the first drops an authored tab, the
    second drops the write-back destination and makes the control inert.
    """
    assert encode_node(_decoded(raw)) == raw
