"""Phase 1654 — `Tabs` and `Stepper` get their FULL typed specs (the 1064 residue).

Phase 1064 typed the one integer slot each kind carries and deliberately left the
rest structural, recording the remainder as residue: ``children``, ``tabHeaders``,
``tabTags``, ``activeTag``, ``orientation`` and the two ``on*`` closure slots all
decoded with their values preserved verbatim, where the reference host types every
one. ``children`` landed in the same phase. This closes the rest.

**Structural preservation is invisible in a round-trip suite, which is why this
needed its own tests.** A structurally-carried member re-encodes byte-perfectly
whatever it holds, so the corpus round-trip is green for a document the reference
host refuses outright — and the divergence surfaces first as two hosts disagreeing
about whether a tree is valid at all, not as a failure anywhere.

The oracle is the corpus IDL's ``kinds`` entry for each tag plus the reference
decoder's own arms; every member's type, requiredness and omit-at-default comes
from there rather than from this host's judgement. The completeness assertion
below is derived from the IDL, so a member added to either kind arrives as a red
test naming it.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui import decode_node
from fuaran_ui.schema.encode import encode_node

_IDL = CORPUS_ROOT / "idl.json"
idl_required = pytest.mark.skipif(not _IDL.is_file(), reason=f"corpus idl.json not found at {_IDL}")


def _decoded_members(tag: str) -> set[str]:
    from fuaran_ui.schema.decode import KIND_SCHEMAS  # noqa: PLC0415 — test-only introspection

    return {entry[0] for entry in KIND_SCHEMAS[tag]}


@idl_required
@pytest.mark.parametrize("tag", ["Tabs", "Stepper"])
def test_the_typed_spec_covers_every_member_the_idl_declares(tag: str) -> None:
    kinds = {k["tag"]: k for k in json.loads(_IDL.read_text(encoding="utf-8"))["kinds"]}
    declared = {f["name"] for f in kinds[tag]["fields"]}
    typed = _decoded_members(tag)
    assert declared - typed == set(), (
        f"{tag} declares {sorted(declared - typed)} in the corpus IDL and this host types neither — those "
        "members decode structurally, so a wrong-typed value round-trips byte-perfectly here and is "
        "refused by the reference host. Add them to KIND_SCHEMAS."
    )
    assert typed - declared == set(), f"{tag} types {sorted(typed - declared)}, which the IDL does not declare"


def _refusal(source: str) -> tuple[str, str]:
    result = decode_node(source)
    assert not result.ok, f"expected a refusal, got a clean decode of {source}"
    return result.error.code, result.error.path


def test_orientation_is_a_closed_enum_omitted_at_horizontal() -> None:
    assert _refusal('{"id":"t","kind":{"$type":"Tabs","children":[],"orientation":"Sideways"}}') == (
        "UNKNOWN_DU_CASE",
        "$.kind.orientation",
    )
    # The §3.6 aliases every other orientation slot in this host accepts, and the
    # identity default is DROPPED rather than carried — an explicit "Horizontal"
    # must not become a second canonical spelling of the same tree.
    aliased = decode_node('{"id":"t","kind":{"$type":"Tabs","children":[],"orientation":"Row"}}')
    assert aliased.ok, aliased
    assert encode_node(aliased.value) == '{"id":"t","kind":{"$type":"Tabs","children":[]}}'


def test_a_tab_header_is_a_record_and_its_label_is_required() -> None:
    assert _refusal('{"id":"t","kind":{"$type":"Tabs","children":[],"tabHeaders":[{"icon":"x"}]}}') == (
        "MISSING_FIELD",
        "$.kind.tabHeaders[0].label",
    )
    # One level INSIDE an array element — the position a host walking elements
    # with a looser walker than its records gets wrong.
    assert _refusal('{"id":"t","kind":{"$type":"Tabs","children":[],"tabHeaders":[{"icon":7,"label":"A"}]}}') == (
        "WRONG_TYPE",
        "$.kind.tabHeaders[0].icon",
    )


def test_tab_tags_are_strings_and_the_index_is_named() -> None:
    assert _refusal('{"id":"t","kind":{"$type":"Tabs","children":[],"tabTags":["a",3]}}') == (
        "WRONG_TYPE",
        "$.kind.tabTags[1]",
    )


def test_active_tag_is_a_string_binding() -> None:
    assert _refusal('{"id":"t","kind":{"$type":"Tabs","activeTag":7,"children":[]}}') == (
        "WRONG_TYPE",
        "$.kind.activeTag",
    )


def test_a_well_formed_tabs_round_trips_byte_identically() -> None:
    """The other direction. A stricter decoder that broke the round trip would be
    a worse defect than the looseness it replaced, so it is asserted rather than
    assumed."""
    source = (
        '{"id":"t","kind":{"$type":"Tabs","activeTag":{"$type":"Static","value":"a"},"children":[],'
        '"onSelect":"<closure>","onSelectTag":"<closure>","orientation":"Vertical",'
        '"tabHeaders":[{"disabled":{"$type":"Static","value":true},"icon":"x","label":"A"}],'
        '"tabTags":["a"]}}'
    )
    decoded = decode_node(source)
    assert decoded.ok, decoded
    assert encode_node(decoded.value) == source


def test_a_well_formed_stepper_round_trips_byte_identically() -> None:
    source = (
        '{"id":"s","kind":{"$type":"Stepper","activeStep":{"$type":"Static","value":1},'
        '"children":[],"onSelect":"<closure>"}}'
    )
    decoded = decode_node(source)
    assert decoded.ok, decoded
    assert encode_node(decoded.value) == source


def test_a_closure_slot_is_presence_only_and_normalises_to_the_sentinel() -> None:
    """Reference parity, and the one place this host used to differ in BYTES.

    A ``fn`` slot's behaviour cannot cross the wire, so the reference reads only
    whether the key is present and re-emits the sentinel regardless. Left
    structural, this host echoed whatever it was handed — so a document spelling
    the slot as anything else round-tripped differently on the two hosts, which
    is a canonical-bytes divergence rather than a validation one.
    """
    decoded = decode_node(
        '{"id":"s","kind":{"$type":"Stepper","activeStep":{"$type":"Static","value":1},"children":[],"onSelect":42}}'
    )
    assert decoded.ok, "presence-only: the reference does not type this slot either"
    assert '"onSelect":"<closure>"' in encode_node(decoded.value)
