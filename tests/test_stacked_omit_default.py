"""fuaran#1585 — ``Chart.stacked`` is omit-at-default, and the explicit form is still read.

Until 1585 the encoder always emitted ``stacked`` while every host's decoder
already restored ``False`` on absence — a tolerance five hosts happened to share
rather than a stated rule. The IDL now declares the member ``omitDefault false``,
so the omission is the CONTRACT: the encoder omits at ``False``, the decoder
restores ``False``, and the corpus's chart fixtures carry the shorter bytes.

The half worth testing here is the one a corpus of re-emitted fixtures cannot
state, because every fixture in it now omits the member: that a document carrying
the OLD explicit ``"stacked": false`` still decodes to exactly the same tree. This
is the read-compat leg, and it is in its own module rather than the corpus suites
because it is about bytes the corpus deliberately no longer contains.

The normalisation half matters more in this host than in the others. ``Chart``
decodes through its own structural function rather than the schema-driven path,
so an explicit ``false`` would be carried into the model and re-emitted verbatim
unless the decoder drops it — which would make the pre-phase spelling a SECOND
canonical form rather than a lenient accept.
"""

from __future__ import annotations

import pytest

from fuaran_py.result import Ok
from fuaran_py.schema import decode_node, encode_node

_OMITTED = (
    '{"id":"c1","kind":{"$type":"Chart","kind":"Bar",'
    '"source":{"$type":"Static","value":[]},"xField":"quarter","yFields":["revenue"]}}'
)

_EXPLICIT_FALSE = (
    '{"id":"c1","kind":{"$type":"Chart","kind":"Bar",'
    '"source":{"$type":"Static","value":[]},"stacked":false,'
    '"xField":"quarter","yFields":["revenue"]}}'
)

_EXPLICIT_TRUE = (
    '{"id":"c1","kind":{"$type":"Chart","kind":"Bar",'
    '"source":{"$type":"Static","value":[]},"stacked":true,'
    '"xField":"quarter","yFields":["revenue"]}}'
)


def _decoded(raw: str):
    result = decode_node(raw)
    assert isinstance(result, Ok), f"{raw} did not decode: {result}"
    return result.value


def test_omitted_form_round_trips() -> None:
    """The canonical form carries no ``stacked`` and re-encodes unchanged."""
    assert encode_node(_decoded(_OMITTED)) == _OMITTED


def test_explicit_false_decodes_identically() -> None:
    """Read-compat: the pre-phase spelling reaches the same tree, and normalises.

    Equality of the re-encoded bytes is the strongest statement available here —
    two models that encode to the same canonical bytes ARE the same document, and
    that is the property every other host is held to as well.
    """
    assert encode_node(_decoded(_EXPLICIT_FALSE)) == encode_node(_decoded(_OMITTED))
    # …and the shared bytes are the SHORT ones: the explicit spelling is a §3.6
    # lenient accept, not a second canonical form.
    assert encode_node(_decoded(_EXPLICIT_FALSE)) == _OMITTED


@pytest.mark.parametrize("raw", [_EXPLICIT_TRUE])
def test_true_is_still_carried(raw: str) -> None:
    """`True` differs from the identity default, so it rides the wire both ways."""
    assert encode_node(_decoded(raw)) == raw
