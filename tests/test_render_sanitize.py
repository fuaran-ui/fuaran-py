"""Render-time sanitisation seam tests — the XSS-payload floor (Phase 239).

Mirrors the F#/TS ``SanitizeTests`` posture: the renderer is the last line of
defence before AI-emitted bytes reach a browser's HTML parser. These pin the
URL-scheme, ExtraAttributes, and markdown raw-HTML seams.
"""

from __future__ import annotations

from fuaran_py import decode_node
from fuaran_py.renderer import render_html
from fuaran_py.renderer.markdown import to_html
from fuaran_py.renderer.sanitize import (
    sanitize_extra_attributes,
    sanitize_markdown_html,
    sanitize_url,
    sanitize_url_or_blank,
)

# ── URL-scheme seam ─────────────────────────────────────────────────────────


def test_safe_url_schemes_pass_through() -> None:
    for url in ["https://example.com/x", "http://a.b", "mailto:a@b.c", "tel:+1", "/relative/path", "#frag", "foo/bar"]:
        assert sanitize_url(url) == url


def test_dangerous_url_schemes_are_rejected() -> None:
    for url in ["javascript:alert(1)", "vbscript:msgbox", "file:///etc/passwd", "data:text/html,<script>"]:
        assert sanitize_url(url) is None
        assert sanitize_url_or_blank(url) == "about:blank"


def test_obfuscated_javascript_scheme_is_rejected() -> None:
    # Whitespace / control chars inside the scheme region must not defeat the check.
    assert sanitize_url("java\tscript:alert(1)") is None
    assert sanitize_url("  javascript:alert(1)") is None
    assert sanitize_url("JAVASCRIPT:alert(1)") is None


def test_link_node_with_javascript_href_resolves_to_about_blank() -> None:
    html = render_link('{"$type":"Static","value":"javascript:alert(1)"}')
    assert 'href="about:blank"' in html
    assert "javascript:" not in html


def render_link(href_binding: str) -> str:
    wire = (
        '{"id":"l","kind":{"$type":"Link","download":false,"href":'
        + href_binding
        + ',"label":{"$type":"Literal","text":"x"}}}'
    )
    result = decode_node(wire)
    assert result.ok
    return render_html(result.value)


# ── ExtraAttributes seam ─────────────────────────────────────────────────────


def test_extra_attribute_allowlist() -> None:
    attrs = {
        "data-cy": "ok",
        "aria-describedby": "node-7",
        "onclick": "steal()",
        "onerror": "x",
        "style": "color:red",
        "href": "javascript:1",
        "data-x": "has<angle>",
    }
    filtered = sanitize_extra_attributes(attrs)
    assert filtered == {"data-cy": "ok", "aria-describedby": "node-7"}


# ── Markdown raw-HTML seam ───────────────────────────────────────────────────


def test_markdown_escapes_source_so_script_is_inert_text() -> None:
    # `to_html` escapes the source first, so an author-supplied `<script>` can
    # never become an executable tag — it renders as inert escaped text.
    html = to_html("Hello <script>steal()</script> world")
    assert "<script" not in html
    assert "&lt;script&gt;" in html


def test_markdown_neutralises_event_handlers_and_js_urls() -> None:
    dirty = '<a href="javascript:alert(1)" onclick="x()">click</a>'
    cleaned = sanitize_markdown_html(dirty)
    assert "javascript:" not in cleaned
    assert "onclick" not in cleaned
    assert "about:blank" in cleaned


def test_markdown_preserves_body_words_beginning_with_on() -> None:
    # Regression: the ` on<letter>` sweep must be anchored to tag interiors, else
    # it deletes ordinary English words from prose (one/only/once/onto/…).
    prose = "<p>they are one design decision, only once, back onto the path, and ongoing</p>"
    assert sanitize_markdown_html(prose) == prose


def test_markdown_strips_genuine_handler_next_to_prose_on_words() -> None:
    dirty = '<p>only one</p><a href="https://x" onclick="steal()">once</a>'
    cleaned = sanitize_markdown_html(dirty)
    assert "onclick" not in cleaned.lower()
    assert "steal()" not in cleaned
    assert "only one" in cleaned
    assert ">once<" in cleaned
    assert 'href="https://x"' in cleaned


def test_markdown_node_renders_escaped_then_sanitised() -> None:
    wire = (
        '{"id":"md","kind":{"$type":"Markdown","text":{"$type":"Literal",'
        '"text":"intro <script>evil()</script> **bold** [x](javascript:alert(1))"}}}'
    )
    result = decode_node(wire)
    assert result.ok
    html = render_html(result.value)
    assert "fuaran-markdown" in html
    assert "<script" not in html  # no executable tag — the source was escaped first
    assert "javascript:" not in html
    assert "<strong>bold</strong>" in html  # the safe inline markup survives
