"""The notebook display protocol (fuaran#1161).

Three things are pinned here, and they are different questions.

**The bundle's shape.** Which representations a front end is offered, and that
``include`` / ``exclude`` are honoured — the protocol's own contract.

**That the HTML is the RENDERER's, byte for byte.** The display path must not be
a second renderer, a post-processing pass, or a re-escape of the first one: the
fragment inside the wrapper is exactly what
:func:`~fuaran_py.renderer.render_html` returns for the same tree. That is what
carries the sanitiser and the ambient destination policy into notebook output —
so it is asserted as byte-identity rather than as a claim in a docstring.

**That the inlined stylesheet is confined and inert.** The reference stylesheet
is written for a whole document; injected raw it would restyle the page around
the output. Every rule is asserted to sit under the wrapper, the document-level
subjects are asserted to have BECOME the wrapper (or the custom properties would
not reach the tree), and the scoper is asserted to refuse — not silently copy —
a construct it cannot show to be both confined and fetch-free.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fuaran_py import decode_node, encode_node
from fuaran_py.renderer import render_html
from fuaran_py.renderer.egress import EGRESS_REFUSAL_ATTRIBUTE, EGRESS_REFUSAL_URL
from fuaran_py.renderer.notebook import (
    FUARAN_UI_MIME,
    NOTEBOOK_OUTPUT_ATTR,
    UnscopableCss,
    display_html,
    mimebundle,
    scope_css,
    scoped_reference_css,
)
from fuaran_py.ui import UiNode, fuaran, quick

_SCOPE = f"[{NOTEBOOK_OUTPUT_ATTR}]"

_ROWS = [
    {"region": "North", "revenue": 1284.5},
    {"region": "South", "revenue": 918.0},
]


def _tree() -> UiNode:
    return quick.dashboard(
        "Regional revenue",
        quick.metric("Revenue", 2202.5),
        quick.grid(_ROWS),
    )


# ── The bundle's shape ───────────────────────────────────────────────────────


def test_the_bundle_offers_the_three_declared_representations() -> None:
    bundle = _tree()._repr_mimebundle_()
    assert set(bundle) == {"text/html", FUARAN_UI_MIME, "text/plain"}
    assert all(isinstance(value, str) for value in bundle.values())


def test_include_narrows_the_bundle_and_exclude_removes_from_it() -> None:
    tree = _tree()
    assert set(tree._repr_mimebundle_(include={"text/plain"})) == {"text/plain"}
    assert set(tree._repr_mimebundle_(exclude={"text/html"})) == {FUARAN_UI_MIME, "text/plain"}
    # Both filters, in the protocol's order: include first, then exclude.
    assert set(tree._repr_mimebundle_(include={"text/html", "text/plain"}, exclude={"text/html"})) == {"text/plain"}


def test_a_narrowed_bundle_omits_the_expensive_representation() -> None:
    # The 100 kB stylesheet rides on `text/html` alone, so a front end that asked
    # for `text/plain` in a terminal is neither handed it nor charged for it.
    bundle = _tree()._repr_mimebundle_(include={"text/plain"})
    assert "<style>" not in "".join(bundle.values())


def test_the_plain_text_representation_is_one_line() -> None:
    plain = _tree()._repr_mimebundle_()["text/plain"]
    assert "\n" not in plain
    assert "Fuaran UI tree" in plain
    # It names the tree; `repr()` is still the structural view, and saying so is
    # the point — a summary that reads as the whole value is a worse summary.
    assert "repr()" in plain


# ── The HTML is the renderer's own output ────────────────────────────────────


def test_the_html_carries_the_renderers_output_byte_for_byte() -> None:
    tree = _tree()
    wire_node = tree.to_wire()
    html = tree._repr_mimebundle_()["text/html"]
    fragment = render_html(wire_node)
    assert fragment in html
    # The whole assembly, so a future rewrite of the wrapper is a decision rather
    # than a drift: wrapper, one inlined stylesheet, the fragment, close.
    assert html == f"<div {NOTEBOOK_OUTPUT_ATTR}><style>{scoped_reference_css()}</style>{fragment}</div>"


def test_the_stylesheet_is_inlined_once_per_output() -> None:
    html = _tree()._repr_mimebundle_()["text/html"]
    assert html.count("<style>") == 1
    assert html.count("</style>") == 1


def test_the_wire_representation_is_the_canonical_encoding() -> None:
    tree = _tree()
    bundle = tree._repr_mimebundle_()
    assert bundle[FUARAN_UI_MIME] == encode_node(tree.to_wire())
    # And it is genuinely canonical: it decodes, and re-encodes to the same bytes.
    decoded = decode_node(bundle[FUARAN_UI_MIME])
    assert decoded.ok
    assert encode_node(decoded.value) == bundle[FUARAN_UI_MIME]


def test_a_decoded_node_displays_identically_to_the_tree_it_came_from() -> None:
    # The phase's second subject: any decoded `Node`, not only an authored tree.
    # One display path, so the two cannot drift.
    tree = _tree()
    authored = tree._repr_mimebundle_()
    decoded = decode_node(authored[FUARAN_UI_MIME])
    assert decoded.ok
    assert decoded.value._repr_mimebundle_() == authored


# ── Nothing scripted, nothing fetched ────────────────────────────────────────


def test_the_output_carries_no_script_and_no_inline_handler() -> None:
    html = _tree()._repr_mimebundle_()["text/html"].lower()
    assert "<script" not in html
    assert "javascript:" not in html
    # The markup half only. The stylesheet's COMMENTS discuss the elements it
    # styles (`<iframe>` among them), and a substring search over the whole
    # output would read that prose as markup — a check that fails on a comment is
    # not measuring what it claims to.
    markup = html.split("</style>", 1)[1]
    assert "<iframe" not in markup
    assert "srcdoc" not in markup
    # An inline event-handler attribute is the other way script enters markup.
    assert re.search(r"\son[a-z]+\s*=", markup) is None


def test_the_inlined_stylesheet_fetches_nothing() -> None:
    css = scoped_reference_css()
    assert "@import" not in css
    # No `url(...)` at all, so no font, image or cursor is fetched when the cell
    # renders — which is what makes the output safe to open offline and unable to
    # phone home from a notebook someone else executed.
    assert "url(" not in css.replace(" ", "")


def test_a_non_local_destination_is_denied_by_the_ambient_default() -> None:
    # The display path declares no policy, so it gets the renderer's default —
    # deny-non-local. Asserted through the bundle rather than through the policy,
    # because the question is whether the display path REACHES the policy.
    tree = fuaran.link("l", href="https://evil.example/x", label="click")
    html = tree._repr_mimebundle_()["text/html"]
    assert 'href="https://evil.example/x"' not in html
    assert EGRESS_REFUSAL_URL in html
    # The refused host is still NAMED, in the refusal marker — the output records
    # what it declined to contact rather than pretending the destination was never
    # there. So "the host string is absent" is the wrong assertion, and this test
    # would have passed for the wrong reason had it been written that way.
    assert f'{EGRESS_REFUSAL_ATTRIBUTE}="hyperlink:evil.example"' in html


# ── The stylesheet is confined to the wrapper ────────────────────────────────


def _top_level_preludes(css: str) -> list[str]:
    """Every top-level selector list / at-rule prelude in ``css``."""
    preludes: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(css):
        if css.startswith("/*", i):
            i = css.index("*/", i) + 2
            if depth == 0:
                start = i
            continue
        char = css[i]
        if char == "{":
            if depth == 0:
                preludes.append(css[start:i].strip())
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                start = i + 1
        i += 1
    return preludes


def test_every_rule_of_the_reference_stylesheet_is_confined_to_the_wrapper() -> None:
    preludes = _top_level_preludes(scoped_reference_css())
    assert preludes, "the scoped stylesheet parsed to no rules at all"
    unconfined = [p for p in preludes if _SCOPE not in p and not p.startswith("@")]
    assert unconfined == [], f"selectors that can match outside the output cell: {unconfined[:5]}"


def test_the_document_level_subjects_become_the_wrapper() -> None:
    # `:root` carries the whole custom-property palette and `body` the background
    # and type scale. Prefixing them (`[scope] :root`) would match nothing and the
    # rendered tree would lose every token; substituting the wrapper is what makes
    # them cascade into it.
    preludes = _top_level_preludes(scoped_reference_css())
    assert _SCOPE in preludes
    assert ":root" not in preludes
    assert "body" not in preludes


def test_an_ancestor_context_selector_keeps_its_context() -> None:
    # `[dir="rtl"]` is a document-level flag read as CONTEXT, not as the rule's
    # subject. Prefixing it would move the flag inside the output cell, where it
    # never appears, and silently kill the rule.
    scoped = scope_css('[dir="rtl"] .fuaran-x::after { content: "a"; }', _SCOPE)
    assert scoped.strip() == f'[dir="rtl"] {_SCOPE} .fuaran-x::after {{ content: "a"; }}'


def test_a_keyframes_body_is_copied_rather_than_scoped() -> None:
    # `0%` / `100%` are keyframe selectors, not page selectors: scoping them would
    # produce `[scope] 0%`, which is not a selector at all.
    scoped = scope_css("@keyframes slide { 0% { left: 0; } 100% { left: 1px; } }", _SCOPE)
    assert scoped.strip() == "@keyframes slide { 0% { left: 0; } 100% { left: 1px; } }"


def test_rules_inside_a_media_query_are_scoped() -> None:
    scoped = scope_css("@media (max-width: 640px) { .fuaran-x { left: 0; } }", _SCOPE)
    assert f"{_SCOPE} .fuaran-x" in scoped
    assert scoped.startswith("@media (max-width: 640px)")


def test_scoping_rewrites_selectors_and_nothing_else() -> None:
    # The declarations must survive untouched — the transform is a selector
    # rewrite, and a scoper that also edited declarations would be re-styling the
    # reference host's output rather than confining it.
    from fuaran_py.renderer import reference_css

    original = reference_css()
    scoped = scope_css(original, _SCOPE)
    for char in "{};":
        assert scoped.count(char) == original.count(char), f"the {char!r} count moved"


@pytest.mark.parametrize(
    "css",
    [
        '@import url("https://fonts.example/x.css");',  # fetches
        "@nonsense { .x { left: 0; } }",  # cannot be shown to be confined
    ],
)
def test_the_scoper_refuses_a_construct_it_cannot_confine(css: str) -> None:
    # The go-red half. The reference stylesheet is a byte-for-byte copy of the
    # canonical one, so a new construct in it is a deliberate cross-host change —
    # and a scoper that quietly copied it would either leak styles onto the page
    # or reinstate the fetch this output promises not to make.
    with pytest.raises(UnscopableCss):
        scope_css(css, _SCOPE)


def test_the_reference_stylesheet_scopes_without_refusal() -> None:
    # The inverse pin: the refusals above must not be so broad that the real
    # stylesheet trips one. A scoper that refused everything would look strict and
    # test nothing.
    assert scoped_reference_css().strip()
    assert "</style" not in scoped_reference_css().lower()


# ── The recorded notebook ────────────────────────────────────────────────────

_NOTEBOOK = Path(__file__).resolve().parent.parent / "examples" / "notebook_display.ipynb"


def _recorded_bundles() -> list[dict[str, object]]:
    document = json.loads(_NOTEBOOK.read_text(encoding="utf-8"))
    return [
        output["data"]
        for cell in document["cells"]
        for output in cell.get("outputs", [])
        if output.get("output_type") in {"display_data", "execute_result"}
    ]


def test_the_example_notebook_carries_outputs_recorded_through_the_protocol() -> None:
    # The verification artefact: a notebook executed by a real Jupyter-protocol
    # front end, committed WITH its outputs. What is asserted is that the
    # recording is genuine — the front end really asked for a mime bundle and
    # really got these three keys — not that its pixels match today's stylesheet.
    # Pinning the recorded bytes to a live render would turn every cosmetic
    # renderer change into a notebook re-record, and staleness of a demo's pixels
    # is a documentation question, not a correctness one.
    bundles = _recorded_bundles()
    rich = [b for b in bundles if FUARAN_UI_MIME in b]
    assert rich, "no output in the example notebook carries a Fuaran mime bundle"
    for bundle in rich:
        assert set(bundle) == {"text/html", FUARAN_UI_MIME, "text/plain"}


def test_the_notebooks_recorded_wire_is_genuine_canonical_wire() -> None:
    for bundle in _recorded_bundles():
        if FUARAN_UI_MIME not in bundle:
            continue
        wire = bundle[FUARAN_UI_MIME]
        assert isinstance(wire, str), "the wire representation was recorded as something other than the bytes"
        decoded = decode_node(wire)
        assert decoded.ok, f"the recorded wire does not decode: {decoded}"
        assert encode_node(decoded.value) == wire


def test_the_notebooks_recorded_html_is_wrapped_and_carries_no_script() -> None:
    for bundle in _recorded_bundles():
        html = bundle.get("text/html")
        if not isinstance(html, str):
            continue
        assert html.startswith(f"<div {NOTEBOOK_OUTPUT_ATTR}><style>")
        assert html.endswith("</div>")
        assert html.count("<style>") == 1
        assert "<script" not in html.lower()


def test_the_display_helper_and_the_bundle_agree() -> None:
    tree = _tree()
    assert display_html(tree.to_wire()) == tree._repr_mimebundle_()["text/html"]
    assert mimebundle(tree.to_wire()) == tree._repr_mimebundle_()
