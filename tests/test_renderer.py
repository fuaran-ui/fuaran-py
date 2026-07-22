"""Server-HTML renderer unit tests — structure, class vocabulary, escaping."""

from __future__ import annotations

import re

from fuaran_py import decode_node
from fuaran_py.renderer import reference_css_path, render_html


def _render(wire: str) -> str:
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    return render_html(result.value)


def _classes(html: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r'class="([^"]*)"', html):
        out.update(m.group(1).split())
    return out


def test_node_wrapper_carries_id_and_kind_and_style_classes() -> None:
    html = _render(
        '{"id":"badge-1","kind":{"$type":"Badge","label":{"$type":"Literal","text":"Beta"},"variant":"Info"}}'
    )
    assert 'id="badge-1"' in html
    assert 'data-fuaran-node-id="badge-1"' in html
    cls = _classes(html)
    # kind hook + default semantic-style vocabulary on the wrapper
    assert {
        "fuaran-kind-badge",
        "fuaran-node",
        "fuaran-tone-default",
        "fuaran-weight-standard",
        "fuaran-emphasis-normal",
    } <= cls
    # the badge body carries its variant modifier
    assert {"fuaran-badge", "fuaran-badge-info"} <= cls


def test_heading_renders_correct_level_and_escapes_text() -> None:
    html = _render(
        '{"id":"h","kind":{"$type":"Heading","level":2,'
        '"text":{"$type":"Literal","text":"a <b>x</b> & y"},"variant":"Standard"}}'
    )
    assert "<h2 " in html and "</h2>" in html
    # text content is escaped — no raw markup, no raw ampersand
    assert "<b>" not in html
    assert "&lt;b&gt;" in html
    assert "&amp; y" in html


def test_metric_static_binding_resolves_and_formats_currency() -> None:
    html = _render(
        '{"id":"m","kind":{"$type":"Metric","emphasis":"Normal","format":{"$type":"Currency","code":"GBP"},'
        '"label":"Revenue","value":{"$type":"Static","value":1234.5},'
        '"tone":"Brand","weight":"Standard"}}'
    )
    assert "fuaran-metric-brand" in html
    assert "GBP 1234.50" in html
    assert ">Revenue<" in html


def test_metric_unresolved_binding_falls_back_to_em_dash() -> None:
    html = _render(
        '{"id":"m","kind":{"$type":"Metric","emphasis":"Normal","format":{"$type":"None"},'
        '"label":"x","value":{"$type":"Query","name":"sales"},'
        '"tone":"Default","weight":"Standard"}}'
    )
    assert "—" in html  # em-dash placeholder for the unresolved Query binding


def test_metric_query_binding_resolves_from_sources() -> None:
    result = decode_node(
        '{"id":"m","kind":{"$type":"Metric","emphasis":"Normal","format":{"$type":"None"},'
        '"label":"x","value":{"$type":"Query","name":"sales"},'
        '"tone":"Default","weight":"Standard"}}'
    )
    assert result.ok
    html = render_html(result.value, sources={"sales": 42})
    assert ">42<" in html


def test_nested_layout_recurses_and_wraps_each_child() -> None:
    # Box with role=Card (Phase 390 — the retired Card).
    html = _render(
        '{"id":"card","kind":{"$type":"Box","children":['
        '{"id":"kid","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"hi"}}}],'
        '"heading":{"$type":"Literal","text":"Insights"},'
        '"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"role":"Card"}}'
    )
    assert "fuaran-layout-card" in html
    assert "fuaran-card-heading" in html
    assert "fuaran-card-body" in html
    # the child node gets its own wrapper with its own id
    assert 'data-fuaran-node-id="kid"' in html
    assert "fuaran-kind-markdown" in html


def test_stack_orientation_and_wrap_classes() -> None:
    # Box with role=Group + Flex layout (Phase 390 — the retired Stack).
    html = _render(
        '{"id":"s","kind":{"$type":"Box","children":[],'
        '"layout":{"$type":"Flex","direction":"Horizontal","wrap":true},"role":"Group"}}'
    )
    assert "fuaran-layout-stack" in html
    assert "fuaran-stack-horizontal" in html
    assert "fuaran-stack-wrap" in html


def test_link_renders_crawlable_anchor() -> None:
    html = _render(
        '{"id":"l","kind":{"$type":"Link","download":false,"href":{"$type":"Static","value":"https://example.com/x"},'
        '"label":{"$type":"Literal","text":"Go"}}}'
    )
    assert 'href="https://example.com/x"' in html
    assert ">Go</a>" in html


def test_custom_renders_inert_labelled_placeholder() -> None:
    html = _render('{"id":"c","kind":{"$type":"Custom","moduleId":"deal-flow","componentId":"TrendCard"}}')
    assert "fuaran-kind-custom-placeholder" in html
    assert "fuaran-custom-deal-flow-TrendCard" in html
    assert 'data-fuaran-custom-module="deal-flow"' in html
    assert "[fuaran:custom deal-flow.TrendCard]" in html


def test_style_section_projects_role_and_voice_fragments() -> None:
    html = _render(
        '{"id":"m","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"x"}},'
        '"style":{"emphasis":"Normal","role":"Data","tone":"Default","voice":"Display","weight":"Standard"}}'
    )
    cls = _classes(html)
    assert "fuaran-role-data" in cls
    assert "fuaran-voice-display" in cls


def test_reference_css_is_byte_identical_to_the_f_sharp_canonical() -> None:
    # The reference CSS ships as a byte-copy; when the F# sibling is checked out
    # alongside, assert the copy has not drifted (the operator-discipline sync).
    css = reference_css_path()
    assert css.is_file()
    fsharp = css.resolve().parents[5] / "fuaran" / "src" / "Fuaran.UI.Renderer" / "content" / "fuaran-reference.css"
    if fsharp.is_file():
        assert css.read_bytes() == fsharp.read_bytes(), "reference CSS copy has drifted from the F# canonical"
