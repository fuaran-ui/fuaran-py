"""The decoder reads the generated structural tables (Phase 2296).

Every enum set, union discriminator set and the kind set the decoder dispatches on is a projection
of ``idl.json`` through ``fuaran_ui.schema.generated``. These tests pin the CONSEQUENCES rather than
re-listing any vocabulary: a host deviation is a declared table, and a hand mirror that used to
disagree with the IDL no longer can.
"""

from __future__ import annotations

import json

from fuaran_ui.result import Err, Ok
from fuaran_ui.schema import decode, encode_node, generated
from fuaran_ui.schema import types as t


def _modal(**extra: object) -> str:
    kind = {"$type": "Modal", "children": [], "dismissable": False, "open": {"$type": "Static", "value": True}}
    kind.update(extra)
    return json.dumps({"id": "m", "kind": kind})


def test_dispatch_sets_are_the_generated_ones_plus_declared_extras() -> None:
    assert frozenset(generated.KINDS) == decode.KNOWN_KINDS
    assert decode.BINDING_CASES - frozenset(generated.UNION_CASES["Binding"]) == decode._BINDING_HOST_EXTRAS
    assert (
        decode.CELL_FORMAT_CASES - frozenset(generated.UNION_CASES["CellFormat"]) == decode._CELL_FORMAT_LENIENT_ALIASES
    )
    assert decode.ACTION_CASES == frozenset(generated.UNION_CASES["Action"])
    assert decode.TONE_NAMES == generated.ENUM_CASES["ToneVariant"]


def test_the_modal_identity_is_the_wire_token_modal() -> None:
    """The hand mirror spelled the identity ``Blocking``, a token the wire never had: an explicit
    ``"modality":"Modal"`` was REFUSED and ``"Blocking"`` accepted. The generated set inverts both."""
    explicit = decode.decode_node(_modal(modality="Modal"))
    assert isinstance(explicit, Ok)
    assert '"modality"' not in encode_node(explicit.value), "the identity omits at its default"

    invented = decode.decode_node(_modal(modality="Blocking"))
    assert isinstance(invented, Err)
    assert invented.error.path == "$.kind.modality"

    assert t.Modal(open=t.Static(True)).to_wire().fields.get("modality") is None


def _grid(**extra: object) -> str:
    kind = {"$type": "DataGrid", "columns": [], "source": {"$type": "Static", "value": []}}
    kind.update(extra)
    return json.dumps({"id": "g", "kind": kind})


def test_an_omit_at_default_member_drops_at_its_idl_default() -> None:
    """``DataGrid.reorderable`` passed through structurally, so ``false`` re-emitted as a second
    canonical form; its default now comes from the IDL like every other omit-at-default member."""
    explicit = decode.decode_node(_grid(reorderable=False))
    assert isinstance(explicit, Ok)
    assert '"reorderable"' not in encode_node(explicit.value)
    kept = decode.decode_node(_grid(reorderable=True))
    assert isinstance(kept, Ok)
    assert '"reorderable":true' in encode_node(kept.value)


def test_a_member_the_idl_requires_is_required() -> None:
    """Members the hand schema never listed (Map's centre and zoom) are required as the IDL says."""
    source = {"$type": "Static", "value": []}
    doc = {"id": "m", "kind": {"$type": "Map", "centreLatitude": 0.0, "centreLongitude": 0.0, "source": source}}
    refused = decode.decode_node(json.dumps(doc))
    assert isinstance(refused, Err)
    assert (refused.error.code, refused.error.path) == ("MISSING_FIELD", "$.kind.zoom")


def test_every_schema_is_its_kinds_generated_member_set() -> None:
    """The assembled schemas cover exactly the IDL's on-wire members, in declaration order, and every
    generated kind has one but the declared relational decoder."""
    assert set(decode.KIND_SCHEMAS) == set(generated.KINDS) - decode._RELATIONAL_KINDS
    for kind, entries in decode.KIND_SCHEMAS.items():
        expected = [f.name for f in generated.KIND_FIELDS[kind] if f.on_wire]
        assert [e[0] for e in entries] == expected, kind
        required = {e[0] for e in entries if e[1]}
        assert required == generated.REQUIRED_FIELDS[kind], kind
