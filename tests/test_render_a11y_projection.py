"""WHAT the a11y projection contains — the companion to
``test_render_a11y_placement.py``, which pins where it lands.

The projection is one function ported to every host, so a slot one host resolves
and another drops is a silent divergence in the emitted DOM: the same tree
renders a decorative subtree hidden from assistive technology on one host and
exposed on another. ``hidden`` was exactly that here until these assertions
landed.

Every fixture below uses a ``Markdown`` node, which does NOT forward to a
semantic element — so the whole projection sits on the wrapper's open tag and a
wrapper assertion is enough. Placement is the other module's subject.
"""

from __future__ import annotations

import pytest

from fuaran_py import decode_node
from fuaran_py.renderer import render_html
from fuaran_py.renderer.bindings import BindingSources


def _wrapper(section: str, sources: BindingSources | None = None) -> str:
    """Render a non-forwarding node carrying ``section`` and return its open tag."""
    result = decode_node('{"id":"md","kind":{"$type":"Markdown","text":"x"},"accessibility":{' + section + "}}")
    assert result.ok, getattr(result, "error", result)
    html = render_html(result.value, sources)
    return html[: html.index(">") + 1]


def test_hidden_static_true_emits_aria_hidden() -> None:
    assert 'aria-hidden="true"' in _wrapper('"hidden":{"$type":"Static","value":true}')


# The author's intent when marking a subtree hidden is REMOVAL, so the failure
# direction that matters is the silent one: no attribute, content exposed.
# ``aria-hidden`` is not a tri-state — false and unresolved both emit nothing,
# because ``aria-hidden="false"`` is a claim neither tree makes.
@pytest.mark.parametrize(
    ("section", "sources"),
    [
        ('"hidden":{"$type":"Static","value":false}', None),
        ('"label":"Decorative"', None),
        ('"hidden":{"$type":"State","key":"decorative"}', None),
        ('"hidden":{"$type":"State","key":"decorative"}', {"decorative": False}),
    ],
    ids=["static-false", "absent", "unresolved-binding", "resolved-false"],
)
def test_hidden_false_absent_or_unresolved_emits_nothing(section: str, sources: BindingSources | None) -> None:
    assert "aria-hidden" not in _wrapper(section, sources)


def test_hidden_resolves_through_host_sources() -> None:
    """``hidden`` is a ``Binding[bool]``, not a bare bool — so a host-resolved
    binding hides the subtree exactly as a ``Static`` one does. Resolving it is
    the whole point: a projection that only understood ``Static`` would silently
    expose every conditionally-decorative subtree."""
    got = _wrapper('"hidden":{"$type":"State","key":"decorative"}', {"decorative": True})
    assert 'aria-hidden="true"' in got


def test_projection_emits_the_six_slots_in_reference_order() -> None:
    """Pinned as a sequence, not as six independent substring checks, because
    the order is part of the byte-level parity the hosts are held to."""
    got = _wrapper(
        '"label":"Home","labelledBy":"lbl","describedBy":"dsc",'
        '"role":"link","liveRegion":"polite","hidden":{"$type":"Static","value":true}'
    )
    want = (
        'aria-label="Home" aria-labelledby="lbl" aria-describedby="dsc" '
        'role="link" aria-live="polite" aria-hidden="true"'
    )
    assert want in got


def test_label_static_binding_emits_aria_label() -> None:
    """``label`` is a ``Binding[str]``, so the CANONICAL authoring — the only form
    an encoder emits — is a ``Static`` envelope. This host tested for a bare
    string, so a canonical tree emitted no ``aria-label`` at all: not a subtle
    degradation, since the node is then announced by its content or not at all.
    Nothing went red because the fixtures authored the bare form the renderer
    happened to accept, which is why both directions are pinned here."""
    assert 'aria-label="Home"' in _wrapper('"label":{"$type":"Static","value":"Home"}')


def test_label_resolves_through_host_sources() -> None:
    """The other half: a keyed binding resolved from the host sources. A
    projection that understood only ``Static`` would still silently drop every
    conditionally-named node."""
    got = _wrapper('"label":{"$type":"State","key":"navLabel"}', {"navLabel": "Primary navigation"})
    assert 'aria-label="Primary navigation"' in got


def test_label_accepts_the_bare_string_shorthand() -> None:
    """The bare string stays accepted as a lenient, non-canonical shorthand (see
    ``_a11y_name``). Pinned so a later reader cannot mistake it for an accident —
    and so dropping it becomes a deliberate act with a red test, not a silent
    regression in the fixtures that already author it."""
    assert 'aria-label="Home"' in _wrapper('"label":"Home"')


# An unresolved binding and an empty name both emit nothing: ``aria-label=""``
# suppresses the content that would otherwise have named the node, so it is
# strictly worse than leaving the slot off.
@pytest.mark.parametrize(
    ("section", "sources"),
    [
        ('"label":""', None),
        ('"label":{"$type":"Static","value":""}', None),
        ('"label":{"$type":"State","key":"navLabel"}', None),
        ('"label":{"$type":"State","key":"navLabel"}', {"navLabel": ""}),
    ],
    ids=["empty-bare", "empty-static", "unresolved-binding", "resolved-empty"],
)
def test_label_empty_or_unresolved_emits_nothing(section: str, sources: BindingSources | None) -> None:
    assert "aria-label" not in _wrapper(section, sources)


def test_custom_role_is_emitted_verbatim() -> None:
    """``role`` is an open vocabulary: the named ARIA roles and the custom escape
    both travel as the raw string, so no host can tell them apart and every
    reference host emits what it was handed. This host case-folded it, which
    silently rewrote a custom role the author had cased deliberately."""
    assert 'role="doc-pageFooter"' in _wrapper('"role":"doc-pageFooter"')
