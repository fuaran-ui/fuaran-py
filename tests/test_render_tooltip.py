"""The node-level tooltip trait (fuaran#1112) — WHERE the description lands.

A tooltip is a DESCRIPTION of the node, announced through ``aria-describedby``
and revealed by the reference stylesheet's own hover / focus affordance. The
whole risk is placement, so these assertions are placement-sensitive in the same
way ``test_render_a11y_placement`` is: an ``aria-describedby`` on a wrapper the
keyboard never lands on is announced on no interaction at all, and one on a
control while the wrapper is the focus stop is the same failure with the parts
swapped. A substring-anywhere check cannot tell those apart.

The decoder carried this trait (it round-trips through ``extras``) while the
renderer dropped it entirely, so every corpus round-trip stayed green and the
hint reached nobody. That is the state these tests exist to make impossible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.renderer import render_html


def _render(wire: str) -> str:
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    return render_html(result.value)


def _wrapper_tag(html: str) -> str:
    return html[: html.index(">") + 1]


def _open_tag(html: str, tag: str) -> str:
    frm = html[html.index(f"<{tag}") :]
    return frm[: frm.index(">") + 1]


def _markdown(tooltip: str) -> str:
    return '{"id":"n","kind":{"$type":"Markdown","text":"x"},"tooltip":' + json.dumps(tooltip) + "}"


# ── the hint element itself ──────────────────────────────────────────────────


def test_hint_renders_as_a_role_tooltip_span_after_the_body() -> None:
    html = _render(_markdown("the hint"))
    hint = '<span id="n-tooltip" class="fuaran-tooltip" role="tooltip">the hint</span>'
    assert hint in html
    # A SIBLING of the body inside the wrapper, and AFTER it: the pointer moving
    # from the node onto the hint never leaves the wrapper, so the `:hover` that
    # revealed it still holds (WCAG 1.4.13), and the reading order is
    # thing-then-description.
    assert html.index("fuaran-markdown") < html.index(hint)
    assert html.endswith(hint + "</div>")


def test_hint_id_is_derived_from_the_node_id() -> None:
    # Derived, never carried on the wire — every host computes the same string.
    assert 'id="n-tooltip"' in _render(_markdown("h"))


def test_hint_text_is_escaped() -> None:
    assert "a &lt;b&gt; &amp; c" in _render(_markdown("a <b> & c"))


def test_wrapper_gains_the_has_tooltip_class() -> None:
    assert "fuaran-has-tooltip" in _wrapper_tag(_render(_markdown("h")))


# ── the empty hint emits NOTHING ─────────────────────────────────────────────


@pytest.mark.parametrize("hint", ["", "   ", "\t\n"])
def test_an_empty_hint_emits_nothing_at_all(hint: str) -> None:
    """A declared hint that says nothing is markup that reveals an empty box on
    hover — and the class / focus stop / describedby would then advertise a
    description that is not there. All four must be absent, not just the span."""
    html = _render(_markdown(hint))
    assert "fuaran-tooltip" not in html
    assert "fuaran-has-tooltip" not in html
    assert "aria-describedby" not in html
    assert "tabindex" not in html


def test_a_node_without_a_tooltip_is_untouched() -> None:
    html = _render('{"id":"n","kind":{"$type":"Markdown","text":"x"}}')
    for absent in ("fuaran-tooltip", "fuaran-has-tooltip", "aria-describedby", "tabindex"):
        assert absent not in html


# ── placement: does the description ride the semantic element? ───────────────
#
# True exactly when the projection forwards AND the forwarded-to element is a
# NATIVE focus stop. `Image` is the case that shows why these are two questions:
# it forwards, and `<img>` takes no focus, so it needs the wrapper stop AND the
# wrapper description — the pair, or neither.

_BUTTON = (
    '{"id":"b","kind":{"$type":"Button","label":{"$type":"Literal","text":"Go"},'
    '"onClick":{"$type":"Notify","channel":"c","payload":{}},"variant":"Primary"},"tooltip":"h"}'
)
_IMAGE = (
    '{"id":"i","kind":{"$type":"Image","alt":{"$type":"Literal","text":"A"},'
    '"src":{"$type":"Static","value":"/a.png"},"variant":"Avatar"},"tooltip":"h"}'
)


def test_button_hint_rides_the_semantic_element() -> None:
    html = _render(_BUTTON)
    assert 'aria-describedby="b-tooltip"' in _open_tag(html, "button")
    wrapper = _wrapper_tag(html)
    assert "aria-describedby" not in wrapper
    # The `<button>` is already a focus stop; a wrapper stop would be a second,
    # redundant tab stop on the same thing.
    assert "tabindex" not in wrapper


def test_image_hint_rides_the_wrapper_and_takes_the_focus_stop() -> None:
    html = _render(_IMAGE)
    wrapper = _wrapper_tag(html)
    assert 'aria-describedby="i-tooltip"' in wrapper
    assert 'tabindex="0"' in wrapper
    # `<img>` takes no focus, so the description must NOT ride it.
    assert "aria-describedby" not in _open_tag(html, "img")


def test_non_forwarding_kind_hint_rides_the_wrapper_and_takes_the_focus_stop() -> None:
    wrapper = _wrapper_tag(_render(_markdown("h")))
    assert 'aria-describedby="n-tooltip"' in wrapper
    assert 'tabindex="0"' in wrapper


# ── aria-describedby is an ID LIST, appended and never substituted ───────────


def test_declared_described_by_and_a_hint_are_both_announced() -> None:
    """A node that declares ``accessibility.describedBy`` AND carries a hint has
    said two different things a reader is owed both of. Overwriting would
    silently drop whichever the renderer happened to apply second."""
    html = _render(
        '{"id":"n","kind":{"$type":"Markdown","text":"x"},"accessibility":{"describedBy":"note"},"tooltip":"h"}'
    )
    assert 'aria-describedby="note n-tooltip"' in _wrapper_tag(html)


def test_declared_described_by_merges_on_the_semantic_element_too() -> None:
    html = _render(
        '{"id":"b","kind":{"$type":"Button","label":{"$type":"Literal","text":"Go"},'
        '"onClick":{"$type":"Notify","channel":"c","payload":{}},"variant":"Primary"},'
        '"accessibility":{"describedBy":"note"},"tooltip":"h"}'
    )
    assert 'aria-describedby="note b-tooltip"' in _open_tag(html, "button")


# ── the corpus fixtures ──────────────────────────────────────────────────────


@corpus_required
@pytest.mark.parametrize(
    ("fixture", "rides_semantic"),
    [("tooltip-button-1", True), ("tooltip-metric-1", False), ("tooltip-icon-button-1", True)],
)
def test_corpus_tooltip_fixtures_render_the_hint(fixture: str, rides_semantic: bool) -> None:
    raw = (CORPUS_ROOT / "nodes" / f"{fixture}.json").read_text(encoding="utf-8")
    html = _render(raw)
    assert 'role="tooltip"' in html
    assert "fuaran-has-tooltip" in html
    # Whichever element is described, it is the one that takes focus.
    assert ('tabindex="0"' in html) is not rides_semantic


@corpus_required
def test_every_corpus_tooltip_fixture_emits_a_hint() -> None:
    """The sweep: no fixture declaring the trait renders without one. A named
    list above can go stale as the corpus grows; this cannot."""
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    seen = 0
    for fixture in manifest["fixtures"]:
        if fixture["kind"] != "node-round-trip":
            continue
        path = Path(CORPUS_ROOT / fixture["inputFile"])
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload.get("tooltip"), (str, dict)):
            continue
        seen += 1
        assert 'role="tooltip"' in _render(raw), f"{fixture['id']} declares a tooltip that renders no hint"
    assert seen, "the node corpus carries no tooltip trait at all — the sweep is blind"
