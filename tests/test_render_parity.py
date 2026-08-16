"""Class-name vocabulary parity with the reference renderer (Phase 239).

The renderer's value is that its output is visually consistent with the F# and
TypeScript hosts — it must emit the **same ``fuaran-*`` class vocabulary** the
reference renderer does. This test pins that: it extracts the class vocabulary
straight from the F# reference renderer source (the literal class strings in
``Render.fs`` + ``Theme.fs``, plus the ``sprintf "...-%s"`` composition prefixes
such as ``fuaran-metric-`` / ``fuaran-custom-``), then asserts every class this
renderer emits over the whole node corpus is in that vocabulary.

It is a cross-host parity lock, the rendering analogue of the wire-format corpus:
the F# host's vocabulary is the authority, and a pass proves this host does not
drift from it. When the F# sibling is not checked out alongside (standalone
``fuaran-py`` clone), the test skips — mirroring the corpus skip in ``_corpus``.
"""

from __future__ import annotations

import re

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py import decode_node
from fuaran_py.renderer import render_html

# tests/_corpus.py resolves CORPUS_ROOT under the Fuaran-UI estate root; the F#
# reference renderer sources live in the `fuaran` sibling next to it.
_ESTATE_ROOT = CORPUS_ROOT.parent
_REFERENCE_RENDERER_FILES = [
    _ESTATE_ROOT / "fuaran" / "src" / "Fuaran.UI.Renderer.Server" / "Render.fs",
    _ESTATE_ROOT / "fuaran" / "src" / "Fuaran.UI.Renderer" / "Render.fs",
    _ESTATE_ROOT / "fuaran" / "src" / "Fuaran.UI.Renderer.Core" / "Theme.fs",
    # Phase 525 — the Drawing SVG class vocabulary (fuaran-drawing*) lives here.
    _ESTATE_ROOT / "fuaran" / "src" / "Fuaran.UI.Renderer.Core" / "DrawingSvg.fs",
]

_CLASS_TOKEN = re.compile(r"fuaran-[a-zA-Z0-9-]*")


def _reference_vocabulary() -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(exact, prefixes)`` class vocabulary from the F# reference source.

    A token ending in ``-`` is a ``sprintf "...-%s"`` composition prefix (e.g.
    ``fuaran-metric-`` styles ``fuaran-metric-brand``); the rest are exact class
    literals.
    """
    exact: set[str] = set()
    prefixes: set[str] = set()
    for path in _REFERENCE_RENDERER_FILES:
        for token in _CLASS_TOKEN.findall(path.read_text(encoding="utf-8")):
            (prefixes if token.endswith("-") else exact).add(token)
    return frozenset(exact), frozenset(prefixes)


def _reference_renderer_available() -> bool:
    return all(p.is_file() for p in _REFERENCE_RENDERER_FILES)


reference_renderer_required = pytest.mark.skipif(
    not _reference_renderer_available(),
    reason="F# reference renderer source not found alongside fuaran-py",
)


def _emitted_classes(html: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r'class="([^"]*)"', html):
        out.update(tok for tok in m.group(1).split() if tok.startswith("fuaran-"))
    return out


@corpus_required
@reference_renderer_required
@pytest.mark.parametrize("fixture", fixtures_of("node-round-trip"), ids=lambda fx: fx["id"])
def test_emitted_classes_are_in_reference_vocabulary(fixture: dict) -> None:
    exact, prefixes = _reference_vocabulary()
    decoded = decode_node((CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8"))
    assert decoded.ok, f"{fixture['id']}: decode failed: {getattr(decoded, 'error', decoded)}"

    html = render_html(decoded.value)
    for cls in _emitted_classes(html):
        in_vocab = cls in exact or any(cls.startswith(p) for p in prefixes)
        assert in_vocab, f"{fixture['id']}: emitted class {cls!r} is not in the reference renderer vocabulary"


@corpus_required
@reference_renderer_required
def test_reference_vocabulary_is_non_trivial() -> None:
    # Guard against an extraction regression silently emptying the oracle (which
    # would make the parametrized test vacuously pass).
    exact, prefixes = _reference_vocabulary()
    assert len(exact) > 50
    assert "fuaran-node" in exact
    assert "fuaran-custom-" in prefixes


def test_drawing_label_rotation_anchors_at_the_label_position() -> None:
    """Phase 877 — the rotation transform pivots on the label's OWN (x, y).

    That anchoring is what makes rotation compose with ``textAnchor`` rather
    than fight it: the text turns about the point it is aligned to, so a
    ``Middle``-anchored tilted category label stays centred under its band.
    The strings below are byte-for-byte what the F# reference emitter produces
    for the same shapes — the corpus is the oracle for the codec, and this is
    the emission half it does not cover.
    """
    decoded = decode_node(
        '{"id":"d","kind":{"$type":"Drawing","shapes":['
        '{"$type":"Label","style":{"rotation":-30},"text":"Q1","x":30,"y":100},'
        '{"$type":"Label","style":{"rotation":12.34},"text":"F","x":110,"y":100},'
        '{"$type":"Label","style":{"rotation":0},"text":"Z","x":150,"y":100},'
        '{"$type":"Label","style":{},"text":"U","x":100,"y":20}'
        '],"style":{},"viewBox":{"height":120,"minX":0,"minY":0,"width":200}}}'
    )
    assert decoded.ok, f"decode failed: {getattr(decoded, 'error', decoded)}"
    html = render_html(decoded.value)

    assert '<text class="fuaran-drawing-label" x="30" y="100" transform="rotate(-30 30 100)"' in html
    assert 'transform="rotate(12.34 110 100)"' in html
    # An explicit 0 is a PRESENT value and must still emit: absent and zero are
    # different wire shapes, and a renderer that conflates them re-introduces
    # downstream the distinction the codec is careful to preserve.
    assert 'transform="rotate(0 150 100)"' in html
    # The unrotated label carries no transform at all — the byte-unchanged
    # guarantee for every pre-877 drawing.
    assert '<text class="fuaran-drawing-label" x="100" y="20">U</text>' in html
    assert html.count('transform="rotate(') == 3


def test_drawing_rotation_is_inert_off_label() -> None:
    """Rotation is ignored on non-text shapes — load-bearing, not cosmetic.

    Unlike the other text-only ``DrawStyle`` fields, an SVG ``transform`` on a
    ``<rect>`` would MOVE GEOMETRY rather than be ignored, so a renderer that
    emitted it uniformly would silently distort drawings.
    """
    decoded = decode_node(
        '{"id":"d","kind":{"$type":"Drawing","shapes":['
        '{"$type":"Rectangle","height":10,"style":{"rotation":45},"width":10,"x":0,"y":0},'
        '{"$type":"Circle","cx":5,"cy":5,"r":2,"style":{"rotation":45}}'
        '],"style":{},"viewBox":{"height":100,"minX":0,"minY":0,"width":100}}}'
    )
    assert decoded.ok, f"decode failed: {getattr(decoded, 'error', decoded)}"
    assert "transform=" not in render_html(decoded.value)
