"""`Masonry` — the column-fill layout mode (WIRE_FORMAT §3.6.7), Python leg.

The corpus pins the three cases every host must agree on: the two round-trips
(`masonry-1`, `masonry-gap`) and the zero-column refusal
(`reject-box-masonry-nonpositive-cols`). Those run from the shared fixtures and
need nothing here.

What this module pins is the part of §3.6.7 the corpus does **not** carry a
fixture for, and which is precisely where a host is most likely to drift into
being helpful:

* **the deliberate absence of `Grid`'s auto-column leniency.** §16 canonicalises
  a column-less `Grid` to `Auto`; a column-less `Masonry` is a `MISSING_FIELD`.
  The asymmetry is a decision, not an omission — `Auto` is a ROW-fill mode, so
  rewriting a masonry into it would discard the author's whole intent rather
  than recover it — and a host that "fixes" the missing field by copying the
  `Grid` arm would pass every corpus fixture while silently changing the layout.
* **the `columns` alias**, which §3.6 grants and which the reject path must
  still refuse through.
* **`Cols` is addressable by the apply engine on a masonry box**, not only on a
  grid one: the two modes carry the same column count under the same field name.
* **the kind-class hook is `fuaran-kind-masonry`, not the grid's.** The two
  modes fill differently, so a host styling "the grid container" must not catch
  both.

There is deliberately no test asserting a rendered `column-count` here — that is
the corpus render-parity leg's job, and duplicating it would put a second copy
of the normative CSS in a place nothing regenerates.
"""

from __future__ import annotations

from fuaran_py import decode_node, encode_node
from fuaran_py.model import Obj
from fuaran_py.ops import apply
from fuaran_py.ops.apply import ApplyErr
from fuaran_py.renderer.theme import kind_class
from fuaran_py.ui import encode, fuaran


def _box(layout_json: str) -> str:
    return '{"id":"m","kind":{"$type":"Box","children":[],"layout":' + layout_json + ',"role":"Group"}}'


def _decoded(layout_json: str):  # noqa: ANN202 - the codec's own Result union
    return decode_node(_box(layout_json))


# ── The no-leniency asymmetry with `Grid` ────────────────────────────────────


def test_column_less_grid_canonicalises_to_auto() -> None:
    """The §16 leniency this mode does NOT share — asserted so the contrast is
    executable rather than asserted in prose."""
    result = _decoded('{"$type":"Grid"}')
    assert result.ok, result
    assert '"layout":{"$type":"Auto"}' in encode_node(result.value)


def test_column_less_masonry_is_a_missing_field_not_an_auto() -> None:
    result = _decoded('{"$type":"Masonry"}')
    assert not result.ok
    assert result.error.code == "MISSING_FIELD"
    assert result.error.path == "$.kind.layout.cols"


# ── The positivity floor, through both spellings ─────────────────────────────


def test_negative_cols_is_refused_at_the_pinned_path() -> None:
    result = _decoded('{"$type":"Masonry","cols":-2}')
    assert not result.ok
    assert result.error.code == "WRONG_TYPE"
    assert result.error.path == "$.kind.layout.cols"


def test_the_columns_alias_decodes_and_still_refuses_a_non_positive_count() -> None:
    accepted = _decoded('{"$type":"Masonry","columns":3}')
    assert accepted.ok, accepted
    assert '"layout":{"$type":"Masonry","cols":3}' in encode_node(accepted.value)

    refused = _decoded('{"$type":"Masonry","columns":0}')
    assert not refused.ok
    assert refused.error.code == "WRONG_TYPE"
    assert refused.error.path == "$.kind.layout.cols"


# ── The apply surface + the kind-class hook ──────────────────────────────────


def _tree(cols: int, gap: int | None = None):  # noqa: ANN202 - the codec's own Node
    result = decode_node(encode(fuaran.masonry_layout("m", cols=cols, gap=gap)))
    assert result.ok, result
    return result.value


def test_cols_is_addressable_on_a_masonry_box() -> None:
    result = apply(Obj("UpdateProp", {"path": "Cols", "target": "m", "value": 5}), _tree(3))
    assert getattr(result, "ok", False), result
    assert encode_node(result.value) == encode(fuaran.masonry_layout("m", cols=5))


def test_template_columns_stays_a_grid_only_field() -> None:
    """`Masonry` carries no `templateColumns` twin — the multi-column model has
    no track list for one to name, and that absence is what keeps the case
    bounded (no route for arbitrary CSS into the stack)."""
    result = apply(Obj("UpdateProp", {"path": "TemplateColumns", "target": "m", "value": "1fr 2fr"}), _tree(3))
    assert isinstance(result, ApplyErr), result
    assert result.error.code == "FieldNotFound"


def test_updating_cols_leaves_an_explicit_gap_alone() -> None:
    result = apply(Obj("UpdateProp", {"path": "Cols", "target": "m", "value": 2}), _tree(4, gap=16))
    assert getattr(result, "ok", False), result
    assert encode_node(result.value) == encode(fuaran.masonry_layout("m", cols=2, gap=16))


def test_masonry_takes_its_own_kind_class_not_the_grids() -> None:
    assert kind_class(_tree(3).kind) == "fuaran-kind-masonry"
