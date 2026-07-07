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
    "List": "fuaran-kind-list",
    "Divider": "fuaran-kind-divider",
    "Toast": "fuaran-kind-toast",
    "CodeBlock": "fuaran-kind-code-block",
    "Math": "fuaran-kind-math",
    "Markdown": "fuaran-kind-markdown",
    "Metric": "fuaran-kind-metric",
    "Badge": "fuaran-kind-badge",
    "Sparkline": "fuaran-kind-sparkline",
    "Spacer": "fuaran-kind-spacer",
    "Callout": "fuaran-kind-callout",
    "Progress": "fuaran-kind-progress",
    "Skeleton": "fuaran-kind-skeleton",
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
    Separator→divider, Group+Grid→grid-layout, Group+(Flex|Auto)→stack.
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
