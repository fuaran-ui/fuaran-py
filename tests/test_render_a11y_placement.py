"""WHERE the a11y projection lands.

A node's accessibility projection is emitted on the node's SEMANTIC ELEMENT —
the single element the kind body renders, when that element rather than the
wrapper carries the node's semantics: ``Link`` (``<a>``), ``Button``
(``<button>``), ``Image`` (``<img>``). Every other kind keeps the projection on
the wrapper ``<div>``, which always keeps the node's address
(``data-fuaran-node-id``).

These assertions are placement-sensitive on purpose. Every other renderer check
in this suite asserts that a substring appears SOMEWHERE in the emitted HTML,
which cannot tell a ``role="link"`` on the wrapper from one on the anchor — and
that difference is the entire point: assistive technology does not associate a
role on a non-interactive container with the interactive element inside it.
"""

from __future__ import annotations

from fuaran_py import decode_node
from fuaran_py.renderer import render_html

A11Y = '"accessibility":{"label":"Home","role":"Link"}'


def _render(wire: str) -> str:
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    return render_html(result.value)


def _wrapper_tag(html: str) -> str:
    """The wrapper's own open tag — everything up to its first ``>``."""
    return html[: html.index(">") + 1]


def _open_tag(html: str, tag: str) -> str:
    """The open tag of the first ``<tag …>`` in the markup."""
    frm = html[html.index(f"<{tag}") :]
    return frm[: frm.index(">") + 1]


def test_link_projection_lands_on_the_anchor() -> None:
    html = _render(
        '{"id":"lk","kind":{"$type":"Link","download":false,'
        '"href":{"$type":"Static","value":"/home"},'
        '"label":{"$type":"Literal","text":"Home"}},' + A11Y + "}"
    )
    wrapper = _wrapper_tag(html)
    assert "role=" not in wrapper
    assert "aria-label" not in wrapper
    assert 'data-fuaran-node-id="lk"' in wrapper

    anchor = _open_tag(html, "a")
    assert 'role="link"' in anchor
    assert 'aria-label="Home"' in anchor


def test_button_projection_lands_on_the_button() -> None:
    html = _render(
        '{"id":"btn","kind":{"$type":"Button","label":{"$type":"Literal","text":"Go"},'
        '"onClick":{"$type":"Navigate","route":"/x"},"variant":"Primary"},' + A11Y + "}"
    )
    assert "aria-label" not in _wrapper_tag(html)
    assert 'aria-label="Home"' in _open_tag(html, "button")


def test_image_projection_lands_on_the_img() -> None:
    html = _render(
        '{"id":"img","kind":{"$type":"Image","alt":{"$type":"Literal","text":"Alt"},'
        '"src":{"$type":"Static","value":"/a.png"},"variant":"Default"},' + A11Y + "}"
    )
    assert "aria-label" not in _wrapper_tag(html)
    assert 'aria-label="Home"' in _open_tag(html, "img")


def test_non_forwarding_kind_keeps_the_projection_on_the_wrapper() -> None:
    """The other half of the rule — and what makes the three above non-vacuous.

    No single uniform placement can satisfy both this test and those.
    """
    html = _render('{"id":"md","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"x"}},' + A11Y + "}")
    wrapper = _wrapper_tag(html)
    assert 'role="link"' in wrapper
    assert 'aria-label="Home"' in wrapper
