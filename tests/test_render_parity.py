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
