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


def test_protocol_relative_urls_are_rejected() -> None:
    # A protocol-relative URL carries no scheme, so the schemeless branch would
    # admit it — but the browser resolves it against the current page's scheme
    # and lands OFF-ORIGIN. `\` is WHATWG's lenient normalisation of `/` for
    # special schemes, so all four two-separator forms resolve identically.
    for url in ["//evil.example/x", "/\\evil.example/x", "\\\\evil.example/x", "\\/evil.example/x", "//"]:
        assert sanitize_url(url) is None, url
        assert sanitize_url_or_blank(url) == "about:blank", url


def test_protocol_relative_rejection_survives_whitespace_trimming() -> None:
    assert sanitize_url("  //evil.example/x") is None


def test_single_slash_relative_paths_still_pass() -> None:
    for url in ["/", "/a", "/foo//bar", "./rel", "page", "#frag"]:
        assert sanitize_url(url) == url, url
    # An absolute URL whose authority legitimately uses `//` is unaffected.
    assert sanitize_url("https://ok.example/x") == "https://ok.example/x"


def test_url_floor_normalises_as_the_url_parser_does() -> None:
    """§19 rule 1 — the WHATWG basic URL parser's own pre-parse normalisation (Phase 795).

    Control characters are written as escapes throughout: a raw C0 byte in source is
    invisible in review and does not survive a copy-paste, which is the wrong property
    for the payloads a security pin is made of.
    """
    # V1 — an interior TAB / LF / CR BETWEEN the two slash-ish characters. Before rule 1
    # normalised, `/<TAB>/host/x` had first two characters `/` and `<TAB>`, so the
    # protocol-relative test read an ordinary relative reference and accepted, while the
    # browser removed the tab by the URL Standard's step 2 and resolved `//host/x`
    # OFF-ORIGIN. Verified against the WHATWG parser: all twelve spellings resolve to
    # `https://evil.example/x`.
    for c in ("\t", "\n", "\r"):
        for a in ("/", "\\"):
            for b in ("/", "\\"):
                url = f"{a}{c}{b}evil.example/x"
                assert sanitize_url(url) is None, repr(url)
    assert sanitize_url("/\t\r/\nevil.example/x") is None

    # V2 — a LEADING C0 control that is not whitespace. No native trim removes U+0001 or
    # NUL, so the two slashes sat at positions 1 and 2 and the protocol-relative test
    # never saw them; the parser removes them by step 1 and resolves off-origin.
    for c in ("\x01", "\x00", "\x1f"):
        assert sanitize_url(f"{c}//evil.example/x") is None, repr(c)

    # Step 1 is the whole C0-or-space range, at both ends.
    assert sanitize_url("https://good.example/x\x01") == "https://good.example/x"

    # Rule 1's output is the EMITTED value.
    assert sanitize_url("https://good.ex\tample/x") == "https://good.example/x"

    # U+000B and U+000C are removed at the EDGES by step 1 and KEPT in the interior — the
    # parser treats `/<VT>/host/x` as a same-origin path, and so must the floor. Pinned
    # because widening step 2 to "all C0" would silently over-reject here.
    for c in ("\x0b", "\x0c"):
        assert sanitize_url(f"/{c}/evil.example/x") == f"/{c}/evil.example/x", repr(c)

    # ASCII-exact LOOSENS these, correctly: the parser keeps them and resolves an ordinary
    # same-origin path, where `str.strip` removed them and the floor then saw `//` and
    # rejected. U+001C–U+001F is where Python diverged from the other four hosts, and
    # U+0085 is where JS diverged from Python; ASCII-exact ends both.
    for c in ("\xa0", "\x85"):
        assert sanitize_url(f"{c}//evil.example/x") == f"{c}//evil.example/x", repr(c)

    # Rule 2 is UNCHANGED and still stricter than the browser, which is why V1 and V2 are
    # off-origin navigation rather than script execution.
    assert sanitize_url("java\tscript:alert(1)") is None
    assert sanitize_url("java\x0bscript:alert(1)") is None


# The two `unsafeUrl` cases below changed shape when the renderer's `href`
# emission became AMBIENTLY policy-checked: the floor still refuses these URLs at
# exactly the same point, but the seam the call site now goes through renders
# EVERY refusal — the floor's included — as the marked
# `about:blank#fuaran-egress-refused` rather than a bare `about:blank`. The
# marker value is `unsafe-url`, which distinguishes it from a policy refusal (a
# `<class>:<host>` value), so nothing is lost by the change and the "why" that
# was previously only in the logs is now in the document.
#
# The MARKDOWN seam deliberately keeps the bare `about:blank` for this verdict —
# those bytes are pinned by the shared corpus and re-spelling them would churn a
# conformance corpus inside a change about egress. The two are different seams
# with different pinned outputs, and that asymmetry is the reference host's too.


def test_link_node_with_protocol_relative_href_renders_the_refusal() -> None:
    html = render_link('{"$type":"Static","value":"//evil.example/x"}')
    assert 'href="about:blank#fuaran-egress-refused"' in html
    assert 'data-fuaran-egress-refused="unsafe-url"' in html
    assert "evil.example" not in html


def test_link_node_with_javascript_href_renders_the_refusal() -> None:
    html = render_link('{"$type":"Static","value":"javascript:alert(1)"}')
    assert 'href="about:blank#fuaran-egress-refused"' in html
    assert 'data-fuaran-egress-refused="unsafe-url"' in html
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


def test_markdown_element_sweep_is_index_aligned_under_case_folding() -> None:
    # The dangerous-element sweep searches a case-folded COPY and splices the
    # resulting indices into the ORIGINAL. `str.lower()` is not length-preserving
    # ("İ".lower() is two code points), so a single such character before the tag
    # used to shift the removal window and leave a fragment of it behind.
    assert "İ".lower() != "i", "guard: this test is meaningless if the fold is length-preserving"
    assert sanitize_markdown_html("İ<script>alert(1)</script>") == "İ"
    assert sanitize_markdown_html("<p>İİ</p><SCRIPT>x</SCRIPT><p>b</p>") == "<p>İİ</p><p>b</p>"
    assert sanitize_markdown_html("İ<iframe src='x'></iframe>b") == "İb"


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
