"""Class-name vocabulary — the parity contract with the F#/TS reference renderers.

The whole value of this renderer is that its output is *visually consistent*
with the F# and TypeScript hosts: it emits the identical ``fuaran-*`` class
vocabulary, so the byte-copied ``content/fuaran-reference.css`` styles it
unchanged. This module owns the projection from a decoded node's wire
discriminator + style section to that vocabulary, mirroring
``Fuaran.UI.Renderer.Core/Theme.fs`` (``kindClass`` / ``className`` /
``nodeClassName``).

The wire ``kind`` discriminator string IS the ``fuaran-kind-*`` suffix source
(end-to-end consistency, §4d), so a structurally-decoded node that this
renderer does not give a typed body still gets the correct wrapper class.
"""

from __future__ import annotations

import re

from ..model import Node, Obj

# ── Style-enum → class-fragment projection (lowercase the wire enum) ─────────
#
# The wire stores tone/weight/emphasis/role/voice as bare PascalCase strings
# (WIRE_FORMAT.md §3.5); the class fragment is the lowercased name. Role.None
# and Voice.Default contribute no fragment (the default tree renders identically
# to one authored before those fields existed).

_NO_ROLE = frozenset({"None"})
_NO_VOICE = frozenset({"Default"})

# ── Wire kind discriminator → `fuaran-kind-*` class ─────────────────────────
#
# Mirrors Theme.kindClass. Note the two deliberately-divergent grid names:
# layout GridLayout → `fuaran-kind-grid-layout`; visualisation DataGrid →
# `fuaran-kind-grid`.
KIND_CLASS: dict[str, str] = {
    # Layout
    # `Box` (Phase 390) is NOT in this flat map — its `fuaran-kind-*` hook is
    # derived from role + layout mode by `_box_kind_class` (mirrors F# kindClass),
    # so the reference CSS (`.fuaran-kind-stack` / `-grid-layout` / `-dashboard` /
    # `-card`) is unchanged and rendered output stays byte-identical.
    "SplitPanel": "fuaran-kind-split-panel",
    "Tabs": "fuaran-kind-tabs",
    "Stepper": "fuaran-kind-stepper",
    "SummaryList": "fuaran-kind-summary-list",
    "Disclosure": "fuaran-kind-disclosure",
    "Modal": "fuaran-kind-modal",
    "ScrollArea": "fuaran-kind-scroll-area",
    # Display
    "Heading": "fuaran-kind-heading",
    "LabelValueRow": "fuaran-kind-label-value-row",
    "Link": "fuaran-kind-link",
    "Image": "fuaran-kind-image",
    "Media": "fuaran-kind-media",
    "List": "fuaran-kind-list",
    "Toast": "fuaran-kind-toast",
    "CodeBlock": "fuaran-kind-code-block",
    "Math": "fuaran-kind-math",
    "Markdown": "fuaran-kind-markdown",
    "Metric": "fuaran-kind-metric",
    "Badge": "fuaran-kind-badge",
    "Sparkline": "fuaran-kind-sparkline",
    "Callout": "fuaran-kind-callout",
    "Progress": "fuaran-kind-progress",
    "Skeleton": "fuaran-kind-skeleton",
    "Icon": "fuaran-kind-icon",
    # Input
    "Form": "fuaran-kind-form",
    "Filters": "fuaran-kind-filters",
    "Button": "fuaran-kind-button",
    "FileUpload": "fuaran-kind-file-upload",
    "Select": "fuaran-kind-select",
    # Visualisation
    "DataGrid": "fuaran-kind-grid",
    "Chart": "fuaran-kind-chart",
    "Table": "fuaran-kind-table",
    "Map": "fuaran-kind-map",
    # Structural
    "ErrorBoundary": "fuaran-kind-error-boundary",
    "Switch": "fuaran-kind-switch",
    "FragmentDecl": "fuaran-kind-fragment-decl",
    "FragmentRef": "fuaran-kind-fragment-ref",
}

_CLASS_FRAGMENT = re.compile(r"[^a-zA-Z0-9_-]")


def sanitise_class_fragment(raw: str) -> str:
    """Replace any char outside ``[a-zA-Z0-9_-]`` with ``-`` (mirrors Theme)."""
    return _CLASS_FRAGMENT.sub("-", raw)


def _box_kind_class(kind: Obj) -> str:
    """The `fuaran-kind-*` hook for a Box, derived from role + layout mode.

    Mirrors F# `Theme.kindClass`: Dashboard→dashboard, Card→card,
    Separator→divider, Group+Grid→grid-layout, Group+Masonry→masonry,
    Group+(Flex|Auto)→stack.
    """
    role = kind.fields.get("role")
    layout = kind.fields.get("layout")
    layout_mode = layout.tag if isinstance(layout, Obj) else None
    if role == "Dashboard":
        return "fuaran-kind-dashboard"
    if role == "Card":
        return "fuaran-kind-card"
    if role == "Separator":
        return "fuaran-kind-divider"
    if role == "Group" and layout_mode == "Grid":
        return "fuaran-kind-grid-layout"
    if role == "Group" and layout_mode == "Masonry":
        # WIRE_FORMAT §3.6.7 — `Masonry` has no retired kind to inherit a hook
        # from, and deliberately does NOT share the grid's: the two modes fill
        # differently, so a host styling "the grid container" must not catch both.
        return "fuaran-kind-masonry"
    # Group + (Flex | Auto), and any unexpected role, fall to stack.
    return "fuaran-kind-stack"


def kind_class(kind: Obj) -> str:
    """The ``fuaran-kind-*`` class for a decoded node ``kind`` object."""
    tag = kind.tag or ""
    if tag == "Box":
        return _box_kind_class(kind)
    if tag == "Custom":
        module_id = sanitise_class_fragment(str(kind.fields.get("moduleId", "")))
        component_id = sanitise_class_fragment(str(kind.fields.get("componentId", "")))
        return f"fuaran-kind-custom fuaran-custom-{module_id}-{component_id}"
    # A recognised-but-unmapped kind still keys off the wire discriminator so the
    # vocabulary stays consistent end-to-end.
    return KIND_CLASS.get(tag, f"fuaran-kind-{sanitise_class_fragment(tag.lower())}")


def _style_fragment(value: object) -> str:
    return str(value).lower()


def style_class(style: Obj | None) -> str:
    """Project a decoded ``style`` section (or the default) to the BEM-style class.

    Default (no style section): tone=Default, weight=Standard, emphasis=Normal,
    role=None, voice=Default → ``fuaran-node fuaran-tone-default
    fuaran-weight-standard fuaran-emphasis-normal``.
    """
    fields = style.fields if style is not None else {}
    tone = _style_fragment(fields.get("tone", "Default"))
    weight = _style_fragment(fields.get("weight", "Standard"))
    emphasis = _style_fragment(fields.get("emphasis", "Normal"))
    base = f"fuaran-node fuaran-tone-{tone} fuaran-weight-{weight} fuaran-emphasis-{emphasis}"

    role = fields.get("role")
    voice = fields.get("voice")
    parts = [base]
    if role is not None and str(role) not in _NO_ROLE:
        parts.append(f"fuaran-role-{_style_fragment(role)}")
    if voice is not None and str(voice) not in _NO_VOICE:
        parts.append(f"fuaran-voice-{_style_fragment(voice)}")
    return " ".join(parts)


def node_class_name(node: Node) -> str:
    """The full wrapper className: kind class + semantic-style class."""
    style = node.extras.get("style")
    style_obj = style if isinstance(style, Obj) else None
    return kind_class(node.kind) + " " + style_class(style_obj)


# ── Trend sentiment (fuaran#867, WIRE_FORMAT §3.6.1) ────────────────────────


def trend_sentiment(polarity: object, trend: float) -> tuple[str, str]:
    """``(sentiment, glyph)`` for a resolved trend under a declared polarity.

    Mirrors the reference host's ``Theme.trendSentiment``. ``sentiment =
    sign(trend) x polarity``, where ``HigherIsBetter`` is ``+1`` and
    ``LowerIsBetter`` is ``-1``: a positive product is an improvement, a negative
    product a regression, a zero trend neither. An ABSENT polarity is
    ``HigherIsBetter`` (§3.6's omit-when-default table), which is why the
    parameter is typed loosely — the decoded model omits the field at its
    default, so ``None`` is the ordinary case and not a missing value.

    Two things this deliberately does NOT do, each ruling out a spelling someone
    will otherwise propose. It never negates the value: the numeric text, its
    sign included, is identical under either declaration, so polarity changes how
    the number READS and never what it SAYS. And it never writes to ``tone``: a
    renderer that inferred "improving => tile is Success" would re-create in the
    render the exact conflation the wire slot exists to remove, and would
    override an emitter's deliberate ``Critical`` on a metric improving from a
    bad place.

    The glyphs are U+25B2 BLACK UP-POINTING TRIANGLE, U+25BC BLACK DOWN-POINTING
    TRIANGLE and U+2192 RIGHTWARDS ARROW — named here so a mojibake in this file
    is a diff a reviewer can catch rather than a rendered byte nobody pinned.
    They carry the sentiment on a NON-COLOUR channel (WCAG 1.4.1 — colour alone
    fails), and the renderer hangs the fragment on the glyph as an ``aria-label``
    so assistive technology hears the sentiment without the numeric text being
    replaced by it. The glyph tracks SENTIMENT, not the number's direction: under
    an inverted polarity the triangle deliberately disagrees with the sign, and
    that disagreement is the visible evidence the declaration was honoured.
    """
    direction = -1.0 if str(polarity) == "LowerIsBetter" else 1.0
    sentiment = trend * direction
    if sentiment > 0.0:
        return "improving", "▲"
    if sentiment < 0.0:
        return "regressing", "▼"
    return "unchanged", "→"
