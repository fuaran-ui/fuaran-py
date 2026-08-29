"""``WIRE_FORMAT.md`` §16 — a bare ``{"$type":"State","key":k}`` in a
``Transform``'s ``source`` slot is a LIVE source over the EMPTY initial snapshot.

This host refused it until now: ``_decode_transform_binding`` dropped out of the
preserved path when no ``defaultValue`` was carried, and the columnar codec then
surfaced its missing-field didactic. That was correct while nothing else could
fill the slot; under §24.4 a SIBLING reader's declaration fills it, so the
refusal was rejecting the most direct spelling of "I read this key and carry no
data of my own" — the one ``FUARAN106``'s remedy text tells an author to write.

No corpus fixture spells the bare form yet: the corpus is a shared gate and keeps
the ``"defaultValue": []`` spelling deliberately, so respelling it there would
redden a host that has not adopted this. The pin therefore lives here.
"""

from __future__ import annotations

import pytest

from fuaran_py import decode_node, encode_node

BARE = '{"$type":"State","key":"members"}'
EMPTY_ARRAY = '{"$type":"State","defaultValue":[],"key":"members"}'


def badge_with_source(source: str) -> str:
    """A ``Badge`` whose label is a ``Transform`` over the given canonical
    source, in the member order the encoder emits."""
    return (
        '{"id":"member-count","kind":{"$type":"Badge","label":{"$type":"Bound","binding":'
        '{"$type":"Transform","pipeline":[{"$type":"groupBy","aggs":'
        '[{"fn":"count","name":"n","of":"team"}],"keys":[]}],'
        f'"source":{source}}}}},"variant":"Info"}}}}'
    )


@pytest.mark.parametrize(
    "source",
    [pytest.param(BARE, id="bare-wrapper"), pytest.param(EMPTY_ARRAY, id="empty-array")],
)
def test_a_data_less_state_source_decodes_and_re_encodes_verbatim(source: str) -> None:
    """The acceptance pin. Both spellings decode, and — because the binding is
    PRESERVED rather than normalised — each re-encodes to the bytes it arrived
    as. The round-trip is what proves the bare form's decoded binding kept its
    ``defaultValue`` ABSENT rather than gaining an empty-list placeholder, which
    would silently respell a source that declares NOTHING as a declaration of the
    empty table."""
    doc = badge_with_source(source)
    result = decode_node(doc)
    assert result.ok, f"decode refused a §16 live source: {result.error}"
    assert encode_node(result.value) == doc, "re-encode is not byte-identical"


@pytest.mark.parametrize(
    "source",
    [
        # A non-binding object with neither `columns` nor `ref`: still the
        # missing-field didactic the 815 posture raises.
        pytest.param('{"schema":[]}', id="columnar-without-columns"),
        # A `Static` envelope carrying no payload is NOT what §16 widened — the
        # widening names the State wrapper, whose `key` IS the live slot. A
        # `Static` with nothing in it names nothing at all.
        pytest.param('{"$type":"Static"}', id="empty-static-envelope"),
        # Carried data that is not row objects still fails its snapshot decode.
        pytest.param('{"$type":"State","defaultValue":[1,2],"key":"members"}', id="ragged-carried-rows"),
    ],
)
def test_the_widening_is_scoped_to_the_bare_state_wrapper(source: str) -> None:
    """The go-red half. An assertion that only ever passes cannot tell a decoder
    that ACCEPTS the bare wrapper from one that stopped reading the slot at all,
    so the same slot is handed shapes §16 does not sanction. Each must still
    refuse, at a ``$``-rooted path."""
    result = decode_node(badge_with_source(source))
    assert not result.ok, "the source was accepted — the widening is not scoped to the bare wrapper"
    assert result.error.path.startswith("$"), f"refusal path {result.error.path!r} is not $-rooted"
