"""Phase 750 — `CellKindErased.TonedPill`, the one cell kind that survives the wire.

The corpus pins the canonical round-trip, the three §16 shorthands and the reject's
code + path. This suite pins what the corpus deliberately does not:

* the **third** tone-map alias (`tones` — the corpus fixture exercises `toneMap`, and a
  host that wired only the one it was shown is non-conformant in a way no fixture would
  catch);
* the **didactic** content of the refusal — the corpus asserts `UNKNOWN_DU_CASE` at
  `…map.Delayed`, not that the message names the offending key or teaches the seven
  legal tones, which is the entire reason that fixture exists;
* the omit rule at **both** branches from the decode side;
* the authoring surface's lowering — a host that can decode a case its own typed
  authoring surface can only mis-emit has shipped half the case.
"""

from __future__ import annotations

import json

from fuaran_py.canonical import encode_value
from fuaran_py.schema import decode_node, encode_node
from fuaran_py.schema import types as t


def _column(kind: dict) -> str:
    """A minimal one-column grid document carrying `kind` as its cell kind."""
    return json.dumps(
        {
            "id": "g1",
            "kind": {
                "$type": "DataGrid",
                "columns": [{"field": "status", "kind": kind, "label": "Status"}],
                "source": {"$type": "Static", "value": "<opaque>"},
            },
        }
    )


def _decoded_cell_kind(doc: str) -> dict:
    result = decode_node(doc)
    assert result.ok, result.error
    return json.loads(encode_node(result.value))["kind"]["columns"][0]["kind"]


# ── The tone-map field aliases (WIRE_FORMAT §3.6) ───────────────────────────


def test_every_tone_map_alias_normalises_to_map() -> None:
    for alias in ("map", "toneMap", "tones"):
        kind = _decoded_cell_kind(_column({"$type": "TonedPill", "field": "status", alias: {"Delayed": "Warning"}}))
        assert kind == {"$type": "TonedPill", "field": "status", "map": {"Delayed": "Warning"}}, alias


def test_canonical_map_wins_over_an_alias() -> None:
    kind = _decoded_cell_kind(
        _column(
            {
                "$type": "TonedPill",
                "field": "status",
                "map": {"Delayed": "Warning"},
                "toneMap": {"Delayed": "Critical"},
            }
        )
    )
    assert kind["map"] == {"Delayed": "Warning"}


# ── The §16 `Pill`-tagged shorthand ─────────────────────────────────────────


def test_pill_tag_carrying_a_tone_map_coerces_to_tonedpill() -> None:
    kind = _decoded_cell_kind(_column({"$type": "Pill", "field": "status", "map": {"Delayed": "Warning"}}))
    assert kind["$type"] == "TonedPill"


def test_a_closure_pill_is_untouched() -> None:
    """The coercion keys off the tone map, so an ordinary closure `Pill` — which can
    never carry one — still decodes as `Pill`."""
    kind = _decoded_cell_kind(_column({"$type": "Pill", "labelFn": "<closure>", "toneFn": "<closure>"}))
    assert kind["$type"] == "Pill"


# ── The Phase 460 omit rule on `default` ────────────────────────────────────


def test_default_tone_omits_at_identity_and_survives_otherwise() -> None:
    omitted = _decoded_cell_kind(
        _column({"$type": "TonedPill", "default": "Default", "field": "s", "map": {"a": "Info"}})
    )
    assert "default" not in omitted
    kept = _decoded_cell_kind(_column({"$type": "TonedPill", "default": "Subdued", "field": "s", "map": {"a": "Info"}}))
    assert kept["default"] == "Subdued"


def test_an_aliased_default_normalises_then_omits() -> None:
    """`Neutral` aliases to `Default`, which is the identity — so it normalises first
    and omits second. Two rules composing, in that order."""
    kind = _decoded_cell_kind(_column({"$type": "TonedPill", "default": "Neutral", "field": "s", "map": {"a": "Info"}}))
    assert "default" not in kind


# ── The didactic refusal ────────────────────────────────────────────────────


def test_an_unknown_tone_map_value_is_refused_didactically() -> None:
    result = decode_node(_column({"$type": "TonedPill", "field": "status", "map": {"Delayed": "Urgent"}}))
    assert not result.ok
    error = result.error
    assert error.code == "UNKNOWN_DU_CASE"
    # The offending KEY, not merely the map — "one of your tones is wrong" is not an
    # actionable report when the map has nine entries.
    assert error.path == "$.kind.columns[0].kind.map.Delayed"
    assert "Delayed" in error.message and "Urgent" in error.message
    # All seven legal names, so the author can fix it from the message alone.
    for tone in ("Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info"):
        assert tone in (error.expected_shape or "")


def test_tone_aliases_apply_inside_the_map() -> None:
    kind = _decoded_cell_kind(
        _column(
            {
                "$type": "TonedPill",
                "field": "s",
                "map": {"a": "Danger", "b": "Positive", "c": "Neutral"},
            }
        )
    )
    assert kind["map"] == {"a": "Critical", "b": "Success", "c": "Default"}


def test_the_declarative_fields_are_required() -> None:
    for kind in (
        {"$type": "TonedPill", "map": {"a": "Info"}},
        {"$type": "TonedPill", "field": "s"},
    ):
        result = decode_node(_column(kind))
        assert not result.ok, kind
        assert result.error.code == "MISSING_FIELD"


# ── The authoring surface ───────────────────────────────────────────────────


def test_authoring_kind_lowers_to_the_canonical_bytes() -> None:
    authored = t.TonedPillColumnKind(
        field="status",
        map={"On time": "Success", "Delayed": "Warning", "Cancelled": "Critical"},
        default="Subdued",
    )
    assert encode_value(authored.to_wire()) == (
        '{"$type":"TonedPill","default":"Subdued","field":"status",'
        '"map":{"Cancelled":"Critical","Delayed":"Warning","On time":"Success"}}'
    )


def test_authoring_kind_omits_an_identity_default() -> None:
    authored = t.TonedPillColumnKind(field="carrier", map={"Meridian": "Info"})
    assert encode_value(authored.to_wire()) == ('{"$type":"TonedPill","field":"carrier","map":{"Meridian":"Info"}}')
