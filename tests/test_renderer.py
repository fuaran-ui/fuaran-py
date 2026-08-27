"""Server-HTML renderer unit tests — structure, class vocabulary, escaping."""

from __future__ import annotations

import re

import pytest

from _reference_host import reference_host_root
from fuaran_py import decode_node
from fuaran_py.renderer import (
    DENY_NON_LOCAL_EGRESS,
    EgressClass,
    ExactHost,
    allow_origin,
    reference_css_path,
    render_html,
)


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


_CRAWLABLE_LINK = (
    '{"id":"l","kind":{"$type":"Link","download":false,"href":{"$type":"Static","value":"https://example.com/x"},'
    '"label":{"$type":"Literal","text":"Go"}}}'
)


def test_link_renders_crawlable_anchor_under_a_declared_policy() -> None:
    """A `Link` is a real crawlable `<a href>`, not an inert span — the server
    semantics this host has kept since it shipped.

    The destination is now checked against the render's ambient policy, so the
    off-origin host is DECLARED by name here rather than assumed. That is the
    posture, not a workaround: the default denies leaving, and a host that means
    to link out says so in its own source.
    """
    result = decode_node(_CRAWLABLE_LINK)
    assert result.ok, getattr(result, "error", result)
    policy = allow_origin(ExactHost("example.com"), [EgressClass.HYPERLINK], DENY_NON_LOCAL_EGRESS)
    html = render_html(result.value, egress_policy=policy)
    assert 'href="https://example.com/x"' in html
    assert ">Go</a>" in html
    assert "data-fuaran-egress-refused" not in html


def test_link_to_an_undeclared_host_is_refused_by_default() -> None:
    """The same tree through the DEFAULT entry point, with no policy named at
    all — the ambient half of the guarantee. The marker names the class and the
    host, never the path or the query."""
    html = _render(_CRAWLABLE_LINK)
    assert 'href="about:blank#fuaran-egress-refused"' in html
    assert 'data-fuaran-egress-refused="hyperlink:example.com"' in html
    assert "example.com/x" not in html


# ── The media wave's NORMATIVE render obligations (WIRE_FORMAT §3.6.2–§3.6.6) ──
#
# Every assertion below pins a claim the wire spec states NORMATIVELY, and each is
# a claim a host could violate while round-tripping the bytes perfectly — which is
# exactly why they are tested at the renderer rather than assumed from the codec.


def test_image_presentation_tokens_map_to_classes_and_never_to_style() -> None:
    """§3.6.2 — the tokens are CLASSES, never CSS values. `Natural` emits no class
    on either axis, so the pre-phase emission is untouched."""
    html = _render(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","aspectRatio":"SixteenNine","fit":"Cover",'
        '"loading":"Lazy","src":{"$type":"Static","value":"/hero.jpg"},"variant":"Default"}}'
    )
    assert {"fuaran-image", "fuaran-image-fit-cover", "fuaran-image-aspect-sixteen-nine"} <= _classes(html)
    assert 'loading="lazy"' in html
    assert "style=" not in html
    # `Eager` / `Natural` are the identity: no attribute, no class, byte-identical
    # to what a pre-1077 document always emitted.
    plain = _render(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","src":{"$type":"Static","value":"/hero.jpg"},"variant":"Default"}}'
    )
    assert "loading=" not in plain
    assert "fuaran-image-fit" not in plain and "fuaran-image-aspect" not in plain


def test_image_srcset_is_emitted_ascending_by_width_whatever_the_wire_order() -> None:
    """§3.6.4 — the wire preserves the AUTHOR's array order; ascending-by-width is
    the RENDERER's canonicalisation. The fixture is authored DESCENDING precisely
    so a renderer that emitted wire order fails this."""
    result = decode_node(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","src":{"$type":"Static","value":"/harbour.jpg"},'
        '"srcSet":[{"src":{"$type":"Static","value":"/h-1600.jpg"},"width":1600},'
        '{"src":{"$type":"Static","value":"/h-800.jpg"},"width":800},'
        '{"src":{"$type":"Static","value":"/h-400.jpg"},"width":400}],"variant":"Default"}}'
    )
    assert result.ok, getattr(result, "error", result)
    # The codec kept the author's order — the rule the renderer's sort must not
    # be allowed to hide.
    assert [e.fields["width"] for e in result.value.kind.fields["srcSet"].items] == [1600, 800, 400]
    html = render_html(result.value)
    assert 'srcset="/h-400.jpg 400w, /h-800.jpg 800w, /h-1600.jpg 1600w"' in html
    assert 'sizes="100vw"' in html


def test_image_srcset_candidate_failing_the_url_floor_is_dropped_not_neutered() -> None:
    """§3.6.4 — the primary `src` must exist so it collapses to the refusal URL; a
    candidate has no such obligation, and offering a rendition guaranteed to fail
    is worse than offering one fewer."""
    html = _render(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","src":{"$type":"Static","value":"/local.jpg"},'
        '"srcSet":[{"src":{"$type":"Static","value":"/local-400.jpg"},"width":400},'
        '{"src":{"$type":"Static","value":"https://cdn.example/x-800.jpg"},"width":800}],"variant":"Default"}}'
    )
    assert 'srcset="/local-400.jpg 400w"' in html
    assert "cdn.example" not in html
    assert "about:blank" not in html  # the dropped candidate is absent, never neutered


def test_expandable_wraps_the_image_in_a_real_anchor_to_the_resolved_src() -> None:
    """§3.6.5 — the rendered baseline is a REAL LINK, and the marker attribute is
    VALUELESS. A scripted control, or a marked-up element with no navigable
    target, would be conformant to nothing."""
    html = _render(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","expandable":true,'
        '"src":{"$type":"Static","value":"/harbour.jpg"},"variant":"Default"}}'
    )
    assert '<a class="fuaran-image-expand" href="/harbour.jpg" data-fuaran-expandable=""><img' in html
    assert "</a>" in html
    assert "onclick" not in html


def test_expandable_over_a_refused_src_emits_no_anchor_and_no_marker() -> None:
    """§3.6.5 — a link to the refusal URL is exactly the dead affordance the rule
    forbids. The image still renders, carrying its refusal marker."""
    html = _render(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","expandable":true,'
        '"src":{"$type":"Static","value":"https://cdn.example/harbour.jpg"},"variant":"Default"}}'
    )
    assert "fuaran-image-expand" not in html
    assert "data-fuaran-expandable" not in html
    assert "<a " not in html
    assert "data-fuaran-egress-refused" in html  # the image itself still says why


def test_expandable_with_a_caption_nests_figure_anchor_img() -> None:
    """§3.6.5 — `figure > a > img`, with the `<figcaption>` as the ANCHOR'S
    SIBLING: the caption is deliberately OUTSIDE the link target."""
    html = _render(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","caption":"The harbour at dawn","expandable":true,'
        '"src":{"$type":"Static","value":"/harbour.jpg"},"variant":"Default"}}'
    )
    assert '<figure class="fuaran-image-figure"><a class="fuaran-image-expand"' in html
    assert '</a><figcaption class="fuaran-image-figure-caption">The harbour at dawn</figcaption></figure>' in html


def test_an_image_without_a_caption_emits_no_wrapper_at_all() -> None:
    """§3.6.3 — not an empty `<figure>`, not a wrapper with an empty caption: the
    emission is the bare `<img>` a pre-1078 document always produced."""
    html = _render(
        '{"id":"i","kind":{"$type":"Image","alt":"Hero","src":{"$type":"Static","value":"/h.jpg"},"variant":"Default"}}'
    )
    assert "figure" not in html
    assert "figcaption" not in html


def test_media_always_carries_an_aria_label() -> None:
    """§3.6.6 — the label is mandatory and has no decorative case, so unlike
    `Image`'s `alt` there is no branch: the attribute is emitted whatever the
    label resolves to."""
    for wire, tag in (
        (
            '{"id":"m","kind":{"$type":"Media","kind":{"$type":"Video"},"label":"Studio walkthrough",'
            '"src":{"$type":"Static","value":"/w.mp4"}}}',
            "video",
        ),
        (
            '{"id":"m","kind":{"$type":"Media","kind":{"$type":"Audio"},"label":"Commentary",'
            '"src":{"$type":"Static","value":"/c.mp3"}}}',
            "audio",
        ),
    ):
        html = _render(wire)
        assert f"<{tag} " in html and f"</{tag}>" in html, "media elements are NOT void elements"
        assert "aria-label=" in html
        # `controls` is omit-at-TRUE on the wire: an absent key is the ACCESSIBLE
        # value, so the transport is present unless the document spends a key.
        assert 'controls=""' in html


def test_media_autoplay_is_never_emitted_without_muted() -> None:
    """§3.6.6, the sharpest of the three obligations. The pairing is not a default
    a caller overrides — it is what the declaration MEANS, which is why the wire
    carries no separate `muted` slot to get out of step with it. A host emitting
    `autoplay` alone produces a video that silently never starts."""
    html = _render(
        '{"id":"m","kind":{"$type":"Media","controls":false,"kind":{"$type":"Video","autoplay":true},'
        '"label":"Ambient loop","loop":true,"src":{"$type":"Static","value":"/ambient.mp4"}}}'
    )
    assert 'autoplay=""' in html
    assert 'muted=""' in html
    assert 'loop=""' in html
    assert "controls=" not in html


def test_media_without_autoplay_is_never_muted() -> None:
    """The converse, and a defect of the same family in the other direction:
    muting a video the reader pressed play on."""
    html = _render(
        '{"id":"m","kind":{"$type":"Media","kind":{"$type":"Video"},"label":"Walkthrough",'
        '"src":{"$type":"Static","value":"/w.mp4"}}}'
    )
    assert "autoplay" not in html
    assert "muted" not in html


def test_audio_has_no_autoplay_pathway_at_all() -> None:
    """§3.6.6 — stronger than a default of `false`. The value has nowhere to land
    on the wire, and nothing for this arm to branch on in the emission."""
    result = decode_node(
        '{"id":"m","kind":{"$type":"Media","kind":{"$type":"Audio","autoplay":true},"label":"Commentary",'
        '"src":{"$type":"Static","value":"/c.mp3"}}}'
    )
    assert result.ok, getattr(result, "error", result)
    assert "autoplay" not in result.value.kind.fields["kind"].fields
    html = render_html(result.value)
    assert "autoplay" not in html
    assert "muted" not in html


def test_media_poster_failing_the_url_floor_is_dropped_while_src_collapses() -> None:
    """§3.6.6 — the two URLs differ in what a REFUSAL means. An element must have
    a source; a `<video>` with no poster shows its first frame, which is a working
    rendering, whereas a poster pointing at the refusal URL is a broken image
    painted over the player."""
    html = _render(
        '{"id":"m","kind":{"$type":"Media","kind":{"$type":"Video","poster":{"$type":"Static",'
        '"value":"https://cdn.example/poster.jpg"}},"label":"Walkthrough",'
        '"src":{"$type":"Static","value":"https://cdn.example/w.mp4"}}}'
    )
    assert "poster=" not in html  # dropped
    assert 'src="about:blank#fuaran-egress-refused"' in html  # collapsed, with its marker
    assert "data-fuaran-egress-refused" in html


def test_media_poster_under_a_declared_policy_is_emitted() -> None:
    """The positive half — without it the test above would pass on a renderer that
    never emitted a poster at all."""
    result = decode_node(
        '{"id":"m","kind":{"$type":"Media","kind":{"$type":"Video","poster":{"$type":"Static",'
        '"value":"https://cdn.example/poster.jpg"}},"label":"Walkthrough",'
        '"src":{"$type":"Static","value":"https://cdn.example/w.mp4"}}}'
    )
    assert result.ok, getattr(result, "error", result)
    policy = allow_origin(ExactHost("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS)
    html = render_html(result.value, egress_policy=policy)
    assert 'poster="https://cdn.example/poster.jpg"' in html
    assert "data-fuaran-egress-refused" not in html


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
    """The byte-copy has not drifted from the F# canonical stylesheet.

    The reference host is located through ``_reference_host`` rather than by a
    hard-coded relative path: this check previously walked to a ``fuaran``
    sibling renamed to ``fuaran-dotnet``, and — worse than the parity gate's
    skip — reported a **pass** while comparing nothing, because a missing
    canonical was a silent no-op. An absent host is now an explicit skip and a
    resolved host with no stylesheet is a failure.
    """
    css = reference_css_path()
    assert css.is_file()
    root = reference_host_root()
    if root is None:
        pytest.skip("F# reference host not checked out alongside — nothing to compare the byte-copy against")
    canonical = root / "src" / "Fuaran.UI.Renderer" / "content" / "fuaran-reference.css"
    assert canonical.is_file(), (
        f"the F# reference host resolved at {root} but its canonical stylesheet is missing at {canonical} — "
        "update the path rather than letting this comparison quietly do nothing"
    )
    assert css.read_bytes() == canonical.read_bytes(), "reference CSS copy has drifted from the F# canonical"
