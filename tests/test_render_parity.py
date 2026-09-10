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

**That skip is load-bearing and was silently mis-firing.** The reference host is
located through ``tests/_reference_host.py``, which accepts every spelling the
sibling has shipped under and reports a cross-host checkout that cannot find one
as a failure rather than a skip — see that module, and
``test_reference_host_resolves_in_a_cross_host_checkout`` below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from _reference_host import reference_host_root, vacuous_gate_diagnosis
from fuaran_py import decode_node
from fuaran_py.renderer import render_html

# The reference renderer sources, relative to whichever spelling of the F# host
# is checked out. Resolution walks up from THIS repo's root (not from the corpus,
# whose own location varies with the snapshot fallback) — see _reference_host.
_REFERENCE_HOST_ROOT = reference_host_root()

#: The reference renderer PROJECTS. The vocabulary is DERIVED from every ``.fs``
#: under each — never a hand-kept file list (Phase 1654, fuaran#1139).
#:
#: A hand-kept list is the thing that failed. It named four files; the reference
#: then factored its class spellings into ``Renderer.Core/Css.fs`` (whose own
#: header says it exists so a class spelled inline in several places can no
#: longer drift — the anti-drift refactor broke the drift detector), into
#: ``Renderer/Runtime.fs`` and ``Renderer.Server/Registry.fs`` for the
#: ``fuaran-custom-`` composition, and into ``Renderer/RatingControl.fs``. Each
#: was appended one incident later, which resets the clock rather than closing
#: the class. A project glob cannot go stale that way: a new module inside these
#: projects is read the day it lands.
#:
#: ``*.Tests`` projects are excluded — a test's expectation string is not the
#: reference's own spelling, and admitting them would let an oracle be satisfied
#: by an assertion about the very drift it is checking for.
_REFERENCE_RENDERER_PROJECT_PREFIX = "Fuaran.UI.Renderer"


def _reference_renderer_files() -> list[Path]:
    if _REFERENCE_HOST_ROOT is None:
        return []
    src = _REFERENCE_HOST_ROOT / "src"
    if not src.is_dir():
        return []
    projects = sorted(
        d
        for d in src.iterdir()
        if d.is_dir() and d.name.startswith(_REFERENCE_RENDERER_PROJECT_PREFIX) and not d.name.endswith(".Tests")
    )
    return sorted(p for project in projects for p in project.rglob("*.fs"))


_REFERENCE_RENDERER_FILES = _reference_renderer_files()

_CLASS_TOKEN = re.compile(r"fuaran-[a-zA-Z0-9-]*")
_FS_BLOCK_COMMENT = re.compile(r"\(\*.*?\*\)", re.S)
_FS_LINE_COMMENT = re.compile(r"//.*?$", re.M)

#: A prefix this short admits everything the emitted-class scan can produce, so
#: extracting one silently turns the whole oracle into a tautology. See
#: :func:`test_reference_vocabulary_admits_no_degenerate_prefix`.
_DEGENERATE_PREFIXES = frozenset({"fuaran-"})


def _strip_fs_comments(text: str) -> str:
    """Drop F# comments before extracting class tokens.

    **This is what stops the oracle being vacuous, and it was not merely tidiness**
    (Phase 1654). The reference's doc comments legitimately contain prose about
    the vocabulary — a markup example spelling ``class="fuaran-icon
    fuaran-{kind}-icon"``, a sentence about "every ``fuaran-``-shaped token" — and
    the bare ``fuaran-`` those yield was extracted as a composition PREFIX. A
    prefix of ``fuaran-`` matches every class the emitted-class scan collects, by
    construction, so the parity assertion below passed for any string whatsoever.

    Stripping comments is the narrow fix and the degenerate-prefix guard below is
    the general one; both are kept, because the guard is what will catch the next
    way a too-short prefix gets in.
    """
    return _FS_LINE_COMMENT.sub("", _FS_BLOCK_COMMENT.sub("", text))


def _reference_vocabulary() -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(exact, prefixes)`` class vocabulary from the F# reference source.

    A token ending in ``-`` is a ``sprintf "...-%s"`` composition prefix (e.g.
    ``fuaran-metric-`` styles ``fuaran-metric-brand``); the rest are exact class
    literals.
    """
    exact: set[str] = set()
    prefixes: set[str] = set()
    for path in _REFERENCE_RENDERER_FILES:
        for token in _CLASS_TOKEN.findall(_strip_fs_comments(path.read_text(encoding="utf-8"))):
            (prefixes if token.endswith("-") else exact).add(token)
    return frozenset(exact), frozenset(prefixes)


def _reference_renderer_available() -> bool:
    return bool(_REFERENCE_RENDERER_FILES) and all(p.is_file() for p in _REFERENCE_RENDERER_FILES)


reference_renderer_required = pytest.mark.skipif(
    not _reference_renderer_available(),
    reason="F# reference renderer source not found alongside fuaran-py",
)


def test_reference_host_resolves_in_a_cross_host_checkout() -> None:
    """The parity gate is not allowed to go quiet in a cross-host checkout.

    Every other test in this file skips when the reference host is missing, which
    is right for a standalone clone and catastrophic in a workspace checkout: it
    is exactly how this gate spent months green while checking nothing after the
    ``fuaran`` → ``fuaran-dotnet`` rename. This one never skips, so a future
    rename surfaces as a red test rather than a silent skip.
    """
    diagnosis = vacuous_gate_diagnosis()
    assert diagnosis is None, diagnosis


def test_reference_renderer_projects_are_found_when_the_host_is() -> None:
    """A resolved host that yields NO renderer sources must fail, not skip.

    The source set is derived, so a moved FILE can no longer take the oracle
    offline — that whole failure mode is gone with the literal. What is still
    reachable is a moved or renamed PROJECT: rename ``Fuaran.UI.Renderer.Core``
    and the glob quietly returns a smaller set, and rename every one of them and
    it returns nothing at all, at which point ``reference_renderer_required``
    skips the entire gate. This test never skips on a resolved host.
    """
    if _REFERENCE_HOST_ROOT is None:
        pytest.skip("no F# reference host checked out (the cross-host guard covers the wrong-path case)")
    assert _REFERENCE_RENDERER_FILES, (
        f"the F# reference host resolved at {_REFERENCE_HOST_ROOT} but no `.fs` sources were found under any "
        f"src/{_REFERENCE_RENDERER_PROJECT_PREFIX}* project. If the renderer projects were renamed, update "
        "_REFERENCE_RENDERER_PROJECT_PREFIX — an empty source set silently disables the whole "
        "class-vocabulary parity gate."
    )
    # A floor on the project COUNT, so losing one project to a rename is a red
    # rather than a quietly narrower oracle. Four ship today
    # (Renderer, Renderer.Core, Renderer.Server, Renderer.Web).
    projects = {p.relative_to(_REFERENCE_HOST_ROOT / "src").parts[0] for p in _REFERENCE_RENDERER_FILES}
    assert len(projects) >= 4, (
        f"only {sorted(projects)} resolved as reference renderer projects; the oracle reads fewer sources "
        "than it did. A project rename narrows the vocabulary silently — that is how this gate drifted before."
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
    # Still spelled — at `Fuaran.UI.Renderer/Runtime.fs`, which the retired
    # hand-kept file list did not name. Do NOT relax this to accommodate an
    # extractor that stopped finding it: that reading was tried on the 1139 py
    # leg and was wrong, and relaxing it deletes a correct check.
    assert "fuaran-custom-" in prefixes


@reference_renderer_required
def test_reference_vocabulary_admits_no_degenerate_prefix() -> None:
    """The oracle must not contain a prefix that admits everything.

    **This is the assertion that would have caught the vacuity, and nothing else
    did** (Phase 1654). Every guard around it measured the vocabulary's SIZE —
    ">50 exact classes", "``fuaran-node`` is present", "``fuaran-custom-`` is
    present" — and a vocabulary can be large, correct in every named member, and
    still admit every possible input, because one over-broad prefix subsumes the
    lot. The bare ``fuaran-`` was extracted from the reference's own doc comments
    for as long as those comments have existed, and under it
    ``test_emitted_classes_are_in_reference_vocabulary`` below passed for any
    string beginning ``fuaran-``: the parity lock was reporting success while
    checking nothing.

    A size guard cannot see that, in principle and not just in this instance —
    which is why this test asks the opposite question: not "is the oracle big
    enough" but "can the oracle still say no".
    """
    _, prefixes = _reference_vocabulary()
    degenerate = sorted(prefixes & _DEGENERATE_PREFIXES)
    assert not degenerate, (
        f"the extracted prefix set contains {degenerate}, which admits every class this host can emit — "
        "the parity assertion is a tautology while it is there. It comes from prose (a doc-comment markup "
        "example, or a sentence about the vocabulary) leaking into the extraction; check _strip_fs_comments "
        "still covers the comment form the reference used."
    )


@reference_renderer_required
def test_the_parity_oracle_can_refuse_a_class_the_reference_never_spells() -> None:
    """The go-red proof for the assertion below, run in-process.

    A parity lock that has silently gone vacuous looks exactly like one that is
    passing, so the falsifier is worth a test of its own rather than a comment
    claiming the check works.
    """
    exact, prefixes = _reference_vocabulary()
    invented = "fuaran-a-class-the-reference-host-does-not-spell"
    assert invented not in exact and not any(invented.startswith(p) for p in prefixes), (
        "the oracle admits an invented class, so it admits anything — see "
        "test_reference_vocabulary_admits_no_degenerate_prefix for the mechanism."
    )


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
