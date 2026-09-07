"""Phase 1579 — the Python host authors ``Drawing``, ``Fact`` and ``Mount``.

The decoder has recognised all three since the parity pass; what it did not have
was a SPELLING. ``fuaran_py.ui.encode`` requires a ``.to_wire()`` root, so a node
kind the typed model omits cannot be written at all from Python — not narrowly,
not clumsily, not at all — and thirteen corpus fixtures were therefore reachable
by decode and unreachable by authoring.

The per-fixture byte-identity assertions live where this host already keeps them,
in ``tests/test_ui_authoring.py``'s single parity table, because a second table is
how two of them end up disagreeing about what the corpus says. What lives here is
everything a fixture set cannot pin on its own:

* the CENSUS — the phase's set is computed from the corpus, so a fourteenth
  fixture carrying one of these kinds reddens this file rather than sitting
  silently unauthored;
* the OMISSION rule each record obeys, so a later default that starts emitting is
  caught by something other than a fixture that happens to cover it;
* the §7 non-finite sentinels reaching the typed float slots, which is the one
  place a typed geometry record could quietly refuse a document the wire allows;
* the ROUND TRIP through this host's own codec — the acceptance asks for decode,
  not only encode;
* the RENDER of an authored ``Drawing``, against the same reference class
  vocabulary the corpus arm measures.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.renderer import render_html
from fuaran_py.schema import encode_node
from fuaran_py.schema import types as t
from fuaran_py.ui import encode, fuaran
from test_render_parity import _emitted_classes, _reference_vocabulary, reference_renderer_required
from test_ui_authoring import _authored

#: The kinds this phase adds to the authoring surface, and the fixture count each
#: is expected to carry. The counts are ASSERTED against the corpus below rather
#: than trusted — a number in a docstring is wrong out loud the first time the
#: corpus moves and nobody re-reads the paragraph.
PHASE_KINDS = {"Drawing": 5, "Fact": 6, "Mount": 2}


def _fixtures_carrying(kind: str) -> list[str]:
    """Every node fixture whose canonical bytes carry ``kind`` ANYWHERE in the tree.

    A substring match on the canonical form, which is exact here because the
    corpus files are canonical JSON: ``"$type":"Fact"`` has one spelling, no
    whitespace, and the token cannot occur in a string value without its own
    escaping. Matching the whole tree rather than the root is what pulls in the
    five composite fixtures — a ``Fact`` in a detail pane is as unreachable as a
    ``Fact`` at the root.
    """
    out = []
    for path in sorted((CORPUS_ROOT / "nodes").glob("*.json")):
        if f'"$type":"{kind}"' in path.read_text(encoding="utf-8"):
            out.append(path.stem)
    return out


def _phase_fixtures() -> list[str]:
    ids: set[str] = set()
    for kind in PHASE_KINDS:
        ids.update(_fixtures_carrying(kind))
    return sorted(ids)


# ── The census ───────────────────────────────────────────────────────────────


@corpus_required
@pytest.mark.parametrize("kind,expected", sorted(PHASE_KINDS.items()))
def test_the_per_kind_fixture_count_is_the_one_the_phase_measured(kind: str, expected: int) -> None:
    carried = _fixtures_carrying(kind)
    assert len(carried) == expected, (
        f"the corpus now carries {len(carried)} {kind} fixtures, not {expected} ({carried}). "
        f"Author the new one in test_ui_authoring's parity table and move the count here — "
        f"the §11 forward-coupling rule means a kind's corpus growth is this host's work too."
    )


@corpus_required
def test_every_fixture_carrying_one_of_these_kinds_is_authored() -> None:
    """The falsifier for the whole phase, and the reason the set is computed.

    A fixture that reaches one of these kinds and is NOT in the parity table is
    exactly the state the phase existed to end: decodable, unauthorable, and
    invisible to every gate that reads the parity table alone.
    """
    missing = sorted(set(_phase_fixtures()) - set(_authored()))
    assert not missing, f"corpus fixtures carrying Drawing / Fact / Mount with no authored tree: {missing}"


# ── Round trip through this host's own codec ─────────────────────────────────


@corpus_required
@pytest.mark.parametrize("fixture_id", _phase_fixtures())
def test_the_authored_tree_round_trips_through_the_codec(fixture_id: str) -> None:
    """``encode(decode(encode authored)) == encode authored``, per fixture.

    The parity table proves the authored bytes ARE the corpus's; this proves this
    host reads back what it wrote. The two are different claims: a slot the
    encoder emits and the decoder silently drops would pass the first and fail
    here.
    """
    wire = encode(_authored()[fixture_id])
    decoded = decode_node(wire)
    assert decoded.ok, f"{fixture_id}: decode rejected an authored tree: {getattr(decoded, 'error', decoded)}"
    assert encode_node(decoded.value) == wire


# ── The omission rules, per record ───────────────────────────────────────────


def test_an_all_default_draw_style_is_the_empty_object_not_eleven_nulls() -> None:
    assert t.DrawStyle().to_wire().fields == {}


def test_a_drawings_three_required_slots_are_emitted_even_when_empty() -> None:
    """An empty shape list and an all-default style are the ``drawing-empty``
    DOCUMENT, so they are written; ``title`` / ``description`` are absent."""
    fields = t.Drawing(view_box=t.ViewBox(0, 0, 1, 1)).to_wire().fields
    assert sorted(fields) == ["shapes", "style", "viewBox"]


@pytest.mark.parametrize("rotation,present", [(None, False), (0.0, True), (-0.5, True), (90.0, True)])
def test_only_an_absent_rotation_omits_the_key(rotation: float | None, present: bool) -> None:
    """An explicit zero is an upright label the author WROTE; an absent rotation
    is a label that was never asked. ``drawing-rotated-labels`` carries both."""
    assert ("rotation" in t.DrawStyle(rotation=rotation).to_wire().fields) is present


def test_a_shape_that_declares_no_style_still_carries_the_style_key() -> None:
    """``style`` is required on every ``Shape`` member — the wire has no shape
    without one, so the default is the empty object rather than an omission."""
    for shape in (
        t.Line(0, 0, 1, 1),
        t.Circle(0, 0, 1),
        t.Ellipse(0, 0, 1, 1),
        t.Rectangle(0, 0, 1, 1),
        t.Polyline(),
        t.Polygon(),
        t.Curve(),
        t.Group(),
        t.Label(0, 0, t.LiteralText("x")),
    ):
        assert shape.to_wire().fields.get("style") == t.DrawStyle().to_wire()


def test_a_rectangle_omits_its_corner_radius_rather_than_nulling_it() -> None:
    assert "cornerRadius" not in t.Rectangle(0, 0, 1, 1).to_wire().fields
    assert t.Rectangle(0, 0, 1, 1, corner_radius=0).to_wire().fields["cornerRadius"] == 0


def test_close_is_the_tag_only_curve_command() -> None:
    assert t.Close().to_wire().fields == {}


def test_a_minimal_fact_is_the_two_key_document() -> None:
    """``emphasis`` omits at ``False`` and ``tone`` at ``"Default"`` — both on
    BOTH boundaries, so a Fact authored before either slot existed is unchanged."""
    fields = t.Fact(label=t.LiteralText("Patient"), value=t.LiteralText("Alice")).to_wire().fields
    assert sorted(fields) == ["label", "value"]


@pytest.mark.parametrize(
    "kwargs,key",
    [
        ({"emphasis": True}, "emphasis"),
        ({"tone": "Brand"}, "tone"),
        ({"help": t.LiteralText("Primary insured")}, "help"),
        ({"icon": "user"}, "icon"),
    ],
)
def test_a_declared_fact_slot_rides(kwargs: dict[str, object], key: str) -> None:
    fields = t.Fact(label=t.LiteralText("l"), value=t.LiteralText("v"), **kwargs).to_wire().fields  # type: ignore[arg-type]
    assert key in fields


def test_a_mounts_empty_capability_list_is_written_not_omitted() -> None:
    """Default-deny is the posture the boundary exists for, so the empty grant is
    a DOCUMENT. Omitting it would read as "unspecified" to a host that has to
    decide what the guest may do."""
    fields = t.Mount(scope_id="guest").to_wire().fields
    assert fields["capabilities"].items == []
    assert sorted(fields) == ["capabilities", "channel", "onBubble", "scopeId"]


def test_a_mount_declaring_no_bubble_handler_omits_the_key() -> None:
    assert "onBubble" not in t.Mount(scope_id="guest", on_bubble=False).to_wire().fields


def test_a_guest_channel_omits_an_undeclared_message_shape() -> None:
    assert t.GuestChannel().to_wire().fields == {"direction": "OutOnly"}


def test_a_mount_with_no_bubble_handler_decodes() -> None:
    """The decoder had ``onBubble`` REQUIRED; the IDL declares it optional.

    Both corpus fixtures carry the closure sentinel, so no fixture could see it —
    the divergence surfaced the moment the authoring surface could spell the
    absence and the generative floor generated one. A mount that declines the
    handler is a document every other host reads, and this one refused it with
    ``MISSING_FIELD`` at ``$.kind.onBubble``.
    """
    wire = encode(fuaran.mount("m", scope_id="guest", on_bubble=False))
    assert "onBubble" not in wire
    decoded = decode_node(wire)
    assert decoded.ok, f"a handler-less Mount was refused: {getattr(decoded, 'error', decoded)}"
    assert encode_node(decoded.value) == wire


# ── The §7 non-finite sentinels at typed float slots ─────────────────────────


@pytest.mark.parametrize(
    "value,token",
    [(float("nan"), '"NaN"'), (float("inf"), '"Infinity"'), (float("-inf"), '"-Infinity"')],
)
def test_a_non_finite_reaches_every_typed_geometry_slot(value: float, token: str) -> None:
    """Nothing in the typed records refuses a non-finite coordinate.

    A degenerate box is a document a conformant host must be able to CARRY and
    refuse for itself; a geometry record that raised here would make this host
    the one tier unable to read a fixture the corpus ships. The chart-annotation
    slots are the deliberate contrast — those the pre-emit validator grounds by
    name (FUARAN137), because an annotation address is a claim about the data.
    """
    wire = encode(
        fuaran.drawing(
            "nf",
            view_box=t.ViewBox(value, value, value, value),
            shapes=[t.Circle(value, value, value, style=t.DrawStyle(font_size=value, rotation=value))],
        )
    )
    # Four viewBox slots, three circle coordinates, fontSize and rotation.
    assert wire.count(token) == 9
    decoded = decode_node(wire)
    assert decoded.ok, f"the host refused its own non-finite emission: {getattr(decoded, 'error', decoded)}"
    assert encode_node(decoded.value) == wire


# ── Render ───────────────────────────────────────────────────────────────────


def _authored_drawing() -> t.UiNode:
    return _authored()["drawing-1"]


@reference_renderer_required
def test_an_authored_drawing_renders_inside_the_reference_class_vocabulary() -> None:
    """The phase's second acceptance clause, taken on the AUTHORED path.

    ``test_render_parity`` measures the same vocabulary over DECODED corpus trees,
    so it cannot see a lowering that produces a structure the renderer reads
    differently. This renders ``UiNode.to_wire()`` directly — no decode hop — which
    is the path a Python author actually takes.
    """
    exact, prefixes = _reference_vocabulary()
    html = render_html(_authored_drawing().to_wire())
    emitted = _emitted_classes(html)
    assert emitted, "an authored Drawing emitted no fuaran-* classes at all"
    for cls in emitted:
        assert cls in exact or any(cls.startswith(p) for p in prefixes), (
            f"authored Drawing emitted class {cls!r}, which the reference renderer does not spell"
        )


@corpus_required
@pytest.mark.parametrize("fixture_id", _phase_fixtures())
def test_the_authored_and_decoded_trees_render_identically(fixture_id: str) -> None:
    """The authoring lowering and the decoder must hand the renderer the same tree.

    They are two different producers of the structural model — ``to_wire`` builds
    it, ``decode_node`` normalises it — and only the byte assertion couples them
    at the wire. This couples them at the RENDER, where an int/float or an
    ``Obj``/``Node`` difference would show up as markup rather than as bytes.
    """
    decoded = decode_node(encode(_authored()[fixture_id]))
    assert decoded.ok
    assert render_html(_authored()[fixture_id].to_wire()) == render_html(decoded.value)
