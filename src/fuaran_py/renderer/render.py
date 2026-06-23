"""Server-HTML renderer over the decoded ``Node`` tree (Phase 239 baseline).

A pure string-HTML renderer: it walks the structural decoded tree
(:mod:`fuaran_py.model`) and emits the reference ``fuaran-*`` class vocabulary so
the byte-copied ``content/fuaran-reference.css`` styles the output exactly as it
styles the F# and TypeScript hosts. It is the no-dependency baseline that makes
a Python web host (e.g. FastAPI) render Fuaran chrome end-to-end with no
client runtime — the analogue of the F# SSR renderer (Phase 140).

Server semantics mirror that precedent: no runtime, no dispatch. ``Action``-
bearing nodes render inert (a ``Button`` is dead until a client hydrates it; a
``Link`` is a real crawlable ``<a href>``). ``Static`` bindings resolve to their
value; other bindings resolve from a host-supplied ``sources`` map or fall back
to the em-dash placeholder. Client-library visualisations (``Chart`` / ``Map`` /
``DataGrid``) render a deterministic placeholder, never a blank.

The renderer emits the **body fragment** only — the host owns ``<html>`` /
``<head>`` / the ``<link>`` to :func:`fuaran_py.renderer.reference_css_path`.
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import Arr, Node, Obj, Value
from . import markdown
from .bindings import BindingSources, format_number, render_text, resolve_binding
from .html import element, escape_text, text_element, void_element
from .sanitize import sanitize_url_or_blank
from .theme import node_class_name

# Unresolved-binding placeholder — matches the F# SSR renderer's em-dash.
_EM_DASH = "—"


# ── Node coercion ───────────────────────────────────────────────────────────
#
# Typed-decoded layout children (Dashboard / Stack / Card) arrive as `Node`s.
# Structurally-decoded layouts (Tabs / SplitPanel / …) keep their children as
# raw `Obj`s (the decoder did not give them a typed schema). `_as_node` accepts
# either, so every layout body renders uniformly.


def _as_node(value: Value) -> Node | None:
    if isinstance(value, Node):
        return value
    if isinstance(value, Obj) and isinstance(value.fields.get("kind"), Obj):
        node_id = value.fields.get("id")
        kind = value.fields["kind"]
        if isinstance(node_id, str) and isinstance(kind, Obj):
            extras = {k: value.fields[k] for k in ("state", "style", "accessibility") if k in value.fields}
            return Node(node_id, kind, extras)
    return None


def _child_nodes(fields: dict[str, Value], key: str = "children") -> list[Node]:
    raw = fields.get(key)
    if not isinstance(raw, Arr):
        return []
    return [n for item in raw.items if (n := _as_node(item)) is not None]


# ── Fragment registry (mirrors the F# `collectFragments`) ───────────────────


def _collect_fragments(node: Node, acc: dict[str, Node]) -> None:
    kind = node.kind
    if kind.tag == "FragmentDecl":
        name = kind.fields.get("name")
        body = _as_node(kind.fields.get("body"))
        if isinstance(name, str) and body is not None:
            acc[name] = body
    for child in _child_nodes(kind.fields):
        _collect_fragments(child, acc)
    boundary_child = _as_node(kind.fields.get("child"))
    if boundary_child is not None:
        _collect_fragments(boundary_child, acc)


class Renderer:
    """Holds the per-render context: host binding sources + the fragment registry."""

    def __init__(self, sources: BindingSources | None, fragments: dict[str, Node]) -> None:
        self.sources = sources
        self.fragments = fragments

    # ── text / value helpers ────────────────────────────────────────────────

    def _text(self, ts: Value) -> str:
        return render_text(ts, self.sources)

    def _state_loading(self, node: Node) -> Node | None:
        state = node.extras.get("state")
        if isinstance(state, Obj):
            return _as_node(state.fields.get("onLoading"))
        return None

    # ── accessibility projection (best-effort over the structural section) ───

    def _a11y_attrs(self, node: Node) -> list[tuple[str, str]]:
        a11y = node.extras.get("accessibility")
        if not isinstance(a11y, Obj):
            return []
        out: list[tuple[str, str]] = []
        label = a11y.fields.get("label")
        if isinstance(label, str) and label != "":
            out.append(("aria-label", label))
        labelled_by = a11y.fields.get("labelledBy")
        if isinstance(labelled_by, str):
            out.append(("aria-labelledby", labelled_by))
        described_by = a11y.fields.get("describedBy")
        if isinstance(described_by, str):
            out.append(("aria-describedby", described_by))
        role = a11y.fields.get("role")
        if isinstance(role, str):
            out.append(("role", role.lower()))
        live = a11y.fields.get("liveRegion")
        if isinstance(live, str):
            out.append(("aria-live", live.lower()))
        return out

    # ── the node wrapper ─────────────────────────────────────────────────────

    def render_node(self, node: Node) -> str:
        attrs: list[tuple[str, str]] = [
            ("id", node.id),
            ("data-fuaran-node-id", node.id),
            ("class", node_class_name(node)),
        ]
        attrs.extend(self._a11y_attrs(node))
        return element("div", attrs, self.render_kind(node))

    # ── kind dispatch ─────────────────────────────────────────────────────────

    def render_kind(self, node: Node) -> str:
        kind = node.kind
        tag = kind.tag
        fields = kind.fields
        handler = _DISPATCH.get(tag or "")
        if handler is not None:
            return handler(self, node, fields)
        # Recognised-but-unhandled kind: render any children so the subtree is
        # never silently dropped (the wrapper already carries the kind class).
        children = _child_nodes(fields)
        return "".join(self.render_node(c) for c in children)

    # ── layouts ──────────────────────────────────────────────────────────────

    def _children_html(self, fields: dict[str, Value]) -> str:
        return "".join(self.render_node(c) for c in _child_nodes(fields))

    def _dashboard(self, node: Node, fields: dict[str, Value]) -> str:
        return element("div", [("class", "fuaran-layout-dashboard")], self._children_html(fields))

    def _stack(self, node: Node, fields: dict[str, Value]) -> str:
        orientation = fields.get("orientation")
        dir_class = "fuaran-stack-horizontal" if orientation == "Horizontal" else "fuaran-stack-vertical"
        wrap = " fuaran-stack-wrap" if fields.get("wrap") is True else ""
        return element(
            "div",
            [("class", f"fuaran-layout-stack {dir_class}{wrap}")],
            self._children_html(fields),
        )

    def _card(self, node: Node, fields: dict[str, Value]) -> str:
        heading = fields.get("heading")
        header = (
            text_element("header", [("class", "fuaran-card-heading")], self._text(heading))
            if heading is not None
            else ""
        )
        body = element("div", [("class", "fuaran-card-body")], self._children_html(fields))
        return element("section", [("class", "fuaran-layout-card")], header + body)

    def _grid_layout(self, node: Node, fields: dict[str, Value]) -> str:
        template = fields.get("templateColumns")
        if not isinstance(template, str):
            cols = fields.get("cols")
            cols = cols if isinstance(cols, int) else 1
            template = f"repeat({cols}, 1fr)"
        return element(
            "div",
            [("class", "fuaran-layout-grid"), ("style", f"grid-template-columns:{template}")],
            self._children_html(fields),
        )

    def _split_panel(self, node: Node, fields: dict[str, Value]) -> str:
        weight = fields.get("weight")
        left_w = max(0.0, min(1.0, float(weight))) if isinstance(weight, (int, float)) else 0.5
        right_w = 1.0 - left_w
        children = _child_nodes(fields)
        left = children[:1]
        right = children[1:]
        left_html = element(
            "div",
            [("class", "fuaran-split-pane fuaran-split-pane-left"), ("style", f"flex:{left_w:f} 1 0")],
            "".join(self.render_node(c) for c in left),
        )
        right_html = element(
            "div",
            [("class", "fuaran-split-pane fuaran-split-pane-right"), ("style", f"flex:{right_w:f} 1 0")],
            "".join(self.render_node(c) for c in right),
        )
        return element("div", [("class", "fuaran-layout-split-panel")], left_html + right_html)

    def _tab_label(self, child: Node) -> str:
        if child.kind.tag == "Card":
            heading = child.kind.fields.get("heading")
            if heading is not None:
                return self._text(heading)
        return child.id

    def _tabs(self, node: Node, fields: dict[str, Value]) -> str:
        children = _child_nodes(fields)
        vertical = fields.get("orientation") == "Vertical"
        orientation_class = "fuaran-tabs-vertical" if vertical else "fuaran-tabs-horizontal"
        active = resolve_binding(fields.get("activeIndex"), self.sources)
        active_index = active if isinstance(active, int) else 0
        active_index = max(0, min(active_index, max(0, len(children) - 1)))

        tabs: list[str] = []
        for i, child in enumerate(children):
            is_active = i == active_index
            cls = "fuaran-tab" + (" fuaran-tab-active" if is_active else "")
            tabs.append(
                element(
                    "button",
                    [
                        ("id", f"{node.id}-tab-{i}"),
                        ("class", cls),
                        ("role", "tab"),
                        ("aria-selected", "true" if is_active else "false"),
                        ("aria-controls", f"{node.id}-panel-{i}"),
                        ("data-tab-index", str(i)),
                    ],
                    element("span", [("class", "fuaran-tab-label")], escape_text(self._tab_label(child))),
                )
            )
        bar = element(
            "div",
            [
                ("class", "fuaran-tabs-bar"),
                ("role", "tablist"),
                ("aria-orientation", "vertical" if vertical else "horizontal"),
            ],
            "".join(tabs),
        )
        panel = ""
        if children:
            active_child = children[active_index]
            panel = element(
                "div",
                [
                    ("id", f"{node.id}-panel-{active_index}"),
                    ("role", "tabpanel"),
                    ("aria-labelledby", f"{node.id}-tab-{active_index}"),
                    ("class", "fuaran-tabs-panel"),
                ],
                self.render_node(active_child),
            )
        panels = element("div", [("class", "fuaran-tabs-panels")], panel)
        return element("div", [("class", f"fuaran-layout-tabs {orientation_class}")], bar + panels)

    def _summary_list(self, node: Node, fields: dict[str, Value]) -> str:
        heading = fields.get("heading")
        header = (
            text_element("header", [("class", "fuaran-summary-list-heading")], self._text(heading))
            if heading is not None
            else ""
        )
        body = element("div", [("class", "fuaran-summary-list-body")], self._children_html(fields))
        return element("section", [("class", "fuaran-layout-summary-list")], header + body)

    def _disclosure(self, node: Node, fields: dict[str, Value]) -> str:
        resolved_open = resolve_binding(fields.get("open"), self.sources)
        is_open = resolved_open if isinstance(resolved_open, bool) else (fields.get("defaultOpen") is True)
        attrs: list[tuple[str, str]] = [("class", "fuaran-layout-disclosure")]
        if is_open:
            attrs.append(("open", ""))
        summary = text_element("summary", [("class", "fuaran-disclosure-summary")], self._text(fields.get("heading")))
        body = element("div", [("class", "fuaran-disclosure-body")], self._children_html(fields))
        return element("details", attrs, summary + body)

    def _stepper(self, node: Node, fields: dict[str, Value]) -> str:
        children = _child_nodes(fields)
        active = resolve_binding(fields.get("activeStep"), self.sources)
        active_index = active if isinstance(active, int) else 0
        steps = []
        for i in range(len(children)):
            cls = "fuaran-stepper-step" + (" fuaran-stepper-step-active" if i == active_index else "")
            steps.append(element("li", [("class", cls), ("data-step-index", str(i))], escape_text(str(i + 1))))
        numbers = element("ol", [("class", "fuaran-stepper-numbers")], "".join(steps))
        body_child = children[active_index] if 0 <= active_index < len(children) else None
        body = element(
            "div",
            [("class", "fuaran-stepper-body")],
            self.render_node(body_child) if body_child is not None else "",
        )
        return element("div", [("class", "fuaran-layout-stepper")], numbers + body)

    def _modal(self, node: Node, fields: dict[str, Value]) -> str:
        # Overlay render-fidelity contract (server half): the overlay is ALWAYS
        # emitted (no portal), positioned + z-indexed by CSS; closed = the
        # `hidden` attribute. role="dialog" + aria-modal — byte-identical structure
        # to the client renderer so hydration finds the DOM it expects.
        is_open = resolve_binding(fields.get("open"), self.sources) is True
        parts: list[str] = []
        heading = fields.get("heading")
        if heading is not None:
            parts.append(text_element("h2", [("class", "fuaran-modal-heading")], self._text(heading)))
        if fields.get("dismissable") is True:
            parts.append(
                text_element(
                    "button",
                    [("class", "fuaran-modal-dismiss"), ("type", "button"), ("aria-label", "Close")],
                    "×",
                )
            )
        parts.append(element("div", [("class", "fuaran-modal-body")], self._children_html(fields)))
        dialog = element(
            "div",
            [("class", "fuaran-modal-dialog"), ("role", "dialog"), ("aria-modal", "true")],
            "".join(parts),
        )
        overlay_attrs: list[tuple[str, str]] = [("class", "fuaran-modal-overlay")]
        if not is_open:
            overlay_attrs.append(("hidden", ""))
        return element("div", overlay_attrs, dialog)

    def _scroll_area(self, node: Node, fields: dict[str, Value]) -> str:
        axis = {"Horizontal": "horizontal", "Both": "both"}.get(str(fields.get("orientation")), "vertical")
        attrs: list[tuple[str, str]] = [
            ("class", f"fuaran-scrollarea fuaran-scrollarea-{axis}"),
            ("tabindex", "0"),
        ]
        style_parts: list[str] = []
        max_height = fields.get("maxHeight")
        if isinstance(max_height, int):
            style_parts.append(f"max-height:{max_height}px")
        max_width = fields.get("maxWidth")
        if isinstance(max_width, int):
            style_parts.append(f"max-width:{max_width}px")
        if style_parts:
            attrs.append(("style", ";".join(style_parts)))
        return element("div", attrs, self._children_html(fields))

    # ── displays ─────────────────────────────────────────────────────────────

    def _heading(self, node: Node, fields: dict[str, Value]) -> str:
        variant = fields.get("variant")
        suffix = {
            "Eyebrow": " fuaran-heading-eyebrow",
            "Caption": " fuaran-heading-caption",
            "Lead": " fuaran-heading-lead",
        }.get(str(variant), "")
        level = fields.get("level")
        level = level if isinstance(level, int) and 1 <= level <= 6 else 6
        return text_element(f"h{level}", [("class", f"fuaran-heading{suffix}")], self._text(fields.get("text")))

    def _markdown(self, node: Node, fields: dict[str, Value]) -> str:
        html = markdown.to_html(self._text(fields.get("text")))
        return element("div", [("class", "fuaran-markdown")], html)

    def _metric(self, node: Node, fields: dict[str, Value]) -> str:
        value = resolve_binding(fields.get("source"), self.sources)
        if value is None:
            loading = self._state_loading(node)
            if loading is not None:
                return self.render_node(loading)
        tone = str(fields.get("tone", "Default")).lower()
        value_text = format_number(fields.get("format"), value) if value is not None else _EM_DASH
        parts = [
            text_element("div", [("class", "fuaran-metric-label")], self._text(fields.get("label"))),
            text_element("div", [("class", "fuaran-metric-value")], value_text),
        ]
        subtext = fields.get("subtext")
        if subtext is not None:
            parts.append(text_element("div", [("class", "fuaran-metric-subtext")], self._text(subtext)))
        return element("div", [("class", f"fuaran-metric fuaran-metric-{tone}")], "".join(parts))

    def _badge(self, node: Node, fields: dict[str, Value]) -> str:
        variant = str(fields.get("variant", "Neutral")).lower()
        return text_element(
            "span", [("class", f"fuaran-badge fuaran-badge-{variant}")], self._text(fields.get("label"))
        )

    def _callout(self, node: Node, fields: dict[str, Value]) -> str:
        tone = str(fields.get("tone", "Default")).lower()
        parts = []
        heading = fields.get("heading")
        if heading is not None:
            parts.append(text_element("div", [("class", "fuaran-callout-heading")], self._text(heading)))
        parts.append(text_element("div", [("class", "fuaran-callout-body")], self._text(fields.get("body"))))
        return element("div", [("class", f"fuaran-callout fuaran-callout-{tone}")], "".join(parts))

    def _progress(self, node: Node, fields: dict[str, Value]) -> str:
        resolved = resolve_binding(fields.get("fraction"), self.sources)
        if resolved is None:
            loading = self._state_loading(node)
            if loading is not None:
                return self.render_node(loading)
        fraction = float(resolved) if isinstance(resolved, (int, float)) else 0.0
        tone = str(fields.get("tone", "Default")).lower()
        indeterminate = " fuaran-progress-indeterminate" if fields.get("indeterminate") is True else ""
        parts = []
        label = fields.get("label")
        if label is not None:
            parts.append(text_element("div", [("class", "fuaran-progress-label")], self._text(label)))
        fill = element(
            "div",
            [("class", "fuaran-progress-fill"), ("style", f"width:{fraction * 100.0:f}%")],
            "",
        )
        parts.append(element("div", [("class", "fuaran-progress-bar")], fill))
        return element("div", [("class", f"fuaran-progress fuaran-progress-{tone}{indeterminate}")], "".join(parts))

    def _spacer(self, node: Node, fields: dict[str, Value]) -> str:
        size = str(fields.get("size", "Medium")).lower()
        return element("div", [("class", f"fuaran-spacer fuaran-spacer-{size}")], "")

    def _skeleton(self, node: Node, fields: dict[str, Value]) -> str:
        rows = fields.get("rows")
        rows = rows if isinstance(rows, int) and rows > 0 else 1
        body = "".join(element("div", [("class", "fuaran-skeleton-row")], "") for _ in range(rows))
        return element("div", [("class", "fuaran-skeleton")], body)

    def _sparkline(self, node: Node, fields: dict[str, Value]) -> str:
        return text_element("div", [("class", "fuaran-sparkline fuaran-sparkline-empty")], _EM_DASH)

    def _label_value_row(self, node: Node, fields: dict[str, Value]) -> str:
        emphasis = " fuaran-label-value-row-emphasis" if fields.get("emphasis") is True else ""
        value = resolve_binding(fields.get("source"), self.sources)
        value_text = format_number(fields.get("format"), value) if value is not None else _EM_DASH
        label = text_element("span", [("class", "fuaran-label-value-row-label")], self._text(fields.get("label")))
        val = text_element("span", [("class", "fuaran-label-value-row-value")], value_text)
        return element("div", [("class", f"fuaran-label-value-row{emphasis}")], label + val)

    def _link(self, node: Node, fields: dict[str, Value]) -> str:
        href_value = resolve_binding(fields.get("href"), self.sources)
        href = sanitize_url_or_blank(href_value if isinstance(href_value, str) else "")
        attrs: list[tuple[str, str]] = [("class", "fuaran-link"), ("href", href)]
        rel = fields.get("rel")
        if isinstance(rel, str):
            attrs.append(("rel", rel))
        target = fields.get("target")
        if isinstance(target, str):
            attrs.append(("target", target))
        if fields.get("download") is True:
            attrs.append(("download", ""))
        return text_element("a", attrs, self._text(fields.get("label")))

    def _image(self, node: Node, fields: dict[str, Value]) -> str:
        src_value = resolve_binding(fields.get("src"), self.sources)
        src = sanitize_url_or_blank(src_value if isinstance(src_value, str) else "")
        variant = fields.get("variant")
        cls = {
            "Avatar": "fuaran-image fuaran-image-avatar",
            "Rounded": "fuaran-image fuaran-image-rounded",
        }.get(str(variant), "fuaran-image")
        return void_element(
            "img",
            [("class", cls), ("src", src), ("alt", self._text(fields.get("alt")))],
        )

    def _list(self, node: Node, fields: dict[str, Value]) -> str:
        raw = fields.get("items")
        items_html = ""
        if isinstance(raw, Arr):
            items_html = "".join(
                text_element("li", [("class", "fuaran-list-item")], self._text(item)) for item in raw.items
            )
        if fields.get("ordered") is True:
            return element("ol", [("class", "fuaran-list fuaran-list-ordered")], items_html)
        return element("ul", [("class", "fuaran-list fuaran-list-unordered")], items_html)

    def _divider(self, node: Node, fields: dict[str, Value]) -> str:
        vertical = fields.get("orientation") == "Vertical"
        label = fields.get("label")
        if vertical:
            return element(
                "div",
                [
                    ("class", "fuaran-divider fuaran-divider-vertical"),
                    ("role", "separator"),
                    ("aria-orientation", "vertical"),
                ],
                "",
            )
        if label is not None:
            inner = text_element("span", [("class", "fuaran-divider-label")], self._text(label))
            return element(
                "div",
                [("class", "fuaran-divider fuaran-divider-labelled"), ("role", "separator")],
                inner,
            )
        return void_element("hr", [("class", "fuaran-divider fuaran-divider-horizontal")])

    def _toast(self, node: Node, fields: dict[str, Value]) -> str:
        # Overlay render-fidelity contract (server half): ALWAYS emitted; closed =
        # the `hidden` attribute. role="status" + aria-live="polite".
        is_open = resolve_binding(fields.get("open"), self.sources) is True
        tone = str(fields.get("tone", "Default")).lower()
        parts = [text_element("span", [("class", "fuaran-toast-message")], self._text(fields.get("message")))]
        if fields.get("dismissable") is True:
            parts.append(
                text_element(
                    "button",
                    [("class", "fuaran-toast-dismiss"), ("type", "button"), ("aria-label", "Dismiss")],
                    "×",
                )
            )
        attrs: list[tuple[str, str]] = [
            ("class", f"fuaran-toast fuaran-toast-{tone}"),
            ("role", "status"),
            ("aria-live", "polite"),
        ]
        if not is_open:
            attrs.append(("hidden", ""))
        return element("div", attrs, "".join(parts))

    # ── inputs (inert — no dispatch server-side) ─────────────────────────────

    def _button(self, node: Node, fields: dict[str, Value]) -> str:
        variant = str(fields.get("variant", "Primary")).lower()
        disabled = resolve_binding(fields.get("disabled"), self.sources)
        attrs: list[tuple[str, str]] = [("class", f"fuaran-button fuaran-button-{variant}")]
        tooltip = fields.get("tooltip")
        if tooltip is not None:
            attrs.append(("title", self._text(tooltip)))
        if disabled is True:
            attrs.append(("disabled", ""))
        return text_element("button", attrs, self._text(fields.get("label")))

    def _select(self, node: Node, fields: dict[str, Value]) -> str:
        label = element("span", [("class", "fuaran-select-label")], escape_text(self._text(fields.get("label"))))
        options_html = self._render_options(fields.get("source"), fields.get("placeholder"))
        disabled = resolve_binding(fields.get("disabled"), self.sources)
        select_attrs: list[tuple[str, str]] = [("class", "fuaran-select-control")]
        if disabled is True:
            select_attrs.append(("disabled", ""))
        control = element("select", select_attrs, options_html)
        return element("label", [("class", "fuaran-select")], label + control)

    def _render_options(self, source: Value, placeholder: Value) -> str:
        items: list[str] = []
        if placeholder is not None:
            items.append(text_element("option", [("value", "")], self._text(placeholder)))
        resolved = resolve_binding(source, self.sources)
        if isinstance(resolved, Arr):
            for opt in resolved.items:
                if isinstance(opt, Obj):
                    value = opt.fields.get("value", "")
                    items.append(
                        text_element(
                            "option",
                            [("value", str(value))],
                            self._text(opt.fields.get("label", value)),
                        )
                    )
        return "".join(items)

    def _form(self, node: Node, fields: dict[str, Value]) -> str:
        field_html = ""
        raw_fields = fields.get("fields")
        if isinstance(raw_fields, Arr):
            field_html = "".join(self._form_field(f) for f in raw_fields.items if isinstance(f, Obj))
        submit = text_element(
            "button",
            [("class", "fuaran-form-submit"), ("type", "submit")],
            self._text(fields.get("submitLabel")),
        )
        return element("form", [("class", "fuaran-form")], field_html + submit)

    def _form_field(self, field: Obj) -> str:
        field_id = field.fields.get("id")
        field_id = field_id if isinstance(field_id, str) else ""
        label = element(
            "span", [("class", "fuaran-form-field-label")], escape_text(self._text(field.fields.get("label")))
        )
        control = void_element(
            "input",
            [("class", "fuaran-form-field-control"), ("data-fuaran-field", field_id)],
        )
        return element("label", [("class", "fuaran-form-field")], label + control)

    def _filters(self, node: Node, fields: dict[str, Value]) -> str:
        # The structural decode does not give Filters a typed schema; render the
        # wrapper with the correct class so the host CSS hooks, and project any
        # nested filter specs best-effort (rare in the baseline corpus).
        return element("div", [("class", "fuaran-filters")], "")

    def _file_upload(self, node: Node, fields: dict[str, Value]) -> str:
        label = element("span", [("class", "fuaran-file-upload-label")], escape_text(self._text(fields.get("label"))))
        control = void_element("input", [("class", "fuaran-file-upload-control"), ("type", "file")])
        return element("label", [("class", "fuaran-file-upload")], label + control)

    # ── visualisations ───────────────────────────────────────────────────────

    def _table(self, node: Node, fields: dict[str, Value]) -> str:
        headers = fields.get("headers")
        header_cells = ""
        if isinstance(headers, Arr):
            header_cells = "".join(
                text_element("th", [("class", "fuaran-table-header")], self._text(h)) for h in headers.items
            )
        rows = fields.get("rows")
        body_rows = ""
        if isinstance(rows, Arr):
            for row in rows.items:
                if isinstance(row, Arr):
                    cells = "".join(
                        text_element("td", [("class", "fuaran-table-cell")], self._text(c)) for c in row.items
                    )
                    body_rows += element("tr", [("class", "fuaran-table-row")], cells)
        thead = element("thead", [], element("tr", [], header_cells))
        tbody = element("tbody", [], body_rows)
        return element("table", [("class", "fuaran-table")], thead + tbody)

    def _make_vis_placeholder(self, css: str, name: str, count: int, text: str) -> str:
        return text_element(
            "div",
            [
                ("class", css),
                ("data-fuaran-ssr-placeholder", name),
                ("data-fuaran-row-count", str(count)),
            ],
            text,
        )

    def _data_grid(self, node: Node, fields: dict[str, Value]) -> str:
        count = _seq_len(resolve_binding(fields.get("source"), self.sources))
        return self._make_vis_placeholder(
            "fuaran-grid fuaran-grid-ssr-placeholder",
            "DataGrid",
            count,
            f"[Grid: {count} rows {_EM_DASH} hydrates client-side]",
        )

    def _chart(self, node: Node, fields: dict[str, Value]) -> str:
        count = _seq_len(resolve_binding(fields.get("source"), self.sources))
        title = fields.get("title")
        title_html = (
            text_element("div", [("class", "fuaran-chart-title")], self._text(title)) if title is not None else ""
        )
        body = element(
            "div",
            [("class", "fuaran-chart-placeholder")],
            escape_text(f"[Chart: {count} rows {_EM_DASH} hydrates client-side]"),
        )
        return element(
            "div",
            [
                ("class", "fuaran-chart fuaran-chart-ssr-placeholder"),
                ("data-fuaran-ssr-placeholder", "Chart"),
                ("data-fuaran-row-count", str(count)),
            ],
            title_html + body,
        )

    def _map(self, node: Node, fields: dict[str, Value]) -> str:
        count = _seq_len(resolve_binding(fields.get("source"), self.sources))
        return text_element(
            "div",
            [
                ("class", "fuaran-map fuaran-map-ssr-placeholder"),
                ("data-fuaran-ssr-placeholder", "Map"),
                ("data-fuaran-marker-count", str(count)),
            ],
            f"[Map: {count} markers {_EM_DASH} hydrates client-side]",
        )

    # ── structural ───────────────────────────────────────────────────────────

    def _error_boundary(self, node: Node, fields: dict[str, Value]) -> str:
        child = _as_node(fields.get("child"))
        return self.render_node(child) if child is not None else ""

    def _fragment_decl(self, node: Node, fields: dict[str, Value]) -> str:
        return ""  # zero-paint — the decl is a template, not visible output.

    def _fragment_ref(self, node: Node, fields: dict[str, Value]) -> str:
        name = fields.get("name")
        if isinstance(name, str):
            body = self.fragments.get(name)
            if body is not None:
                return self.render_node(body)
            return text_element(
                "div",
                [
                    ("class", "fuaran-fragment-unresolved-placeholder"),
                    ("data-fuaran-fragment-unresolved", name),
                ],
                f"[fuaran:fragment unresolved '{name}']",
            )
        return ""

    def _custom(self, node: Node, fields: dict[str, Value]) -> str:
        module_id = str(fields.get("moduleId", ""))
        component_id = str(fields.get("componentId", ""))
        return text_element(
            "div",
            [
                ("class", f"fuaran-kind-custom-placeholder fuaran-custom-{module_id}-{component_id}"),
                ("data-fuaran-custom-module", module_id),
                ("data-fuaran-custom-component", component_id),
            ],
            f"[fuaran:custom {module_id}.{component_id}]",
        )


def _seq_len(value: object) -> int:
    if isinstance(value, Arr):
        return len(value.items)
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


# Kind discriminator → renderer method. Built once at import time. The values
# are unbound methods invoked as `handler(self, node, fields)` in `render_kind`.
_KindHandler = Callable[["Renderer", Node, dict[str, Value]], str]

_DISPATCH: dict[str, _KindHandler] = {
    "Dashboard": Renderer._dashboard,
    "Stack": Renderer._stack,
    "Card": Renderer._card,
    "GridLayout": Renderer._grid_layout,
    "SplitPanel": Renderer._split_panel,
    "Tabs": Renderer._tabs,
    "SummaryList": Renderer._summary_list,
    "Disclosure": Renderer._disclosure,
    "Stepper": Renderer._stepper,
    "Modal": Renderer._modal,
    "ScrollArea": Renderer._scroll_area,
    "Heading": Renderer._heading,
    "Markdown": Renderer._markdown,
    "Metric": Renderer._metric,
    "Badge": Renderer._badge,
    "Callout": Renderer._callout,
    "Progress": Renderer._progress,
    "Spacer": Renderer._spacer,
    "Skeleton": Renderer._skeleton,
    "Sparkline": Renderer._sparkline,
    "LabelValueRow": Renderer._label_value_row,
    "Link": Renderer._link,
    "Image": Renderer._image,
    "List": Renderer._list,
    "Divider": Renderer._divider,
    "Toast": Renderer._toast,
    "Button": Renderer._button,
    "Select": Renderer._select,
    "Form": Renderer._form,
    "Filters": Renderer._filters,
    "FileUpload": Renderer._file_upload,
    "Table": Renderer._table,
    "DataGrid": Renderer._data_grid,
    "Chart": Renderer._chart,
    "Map": Renderer._map,
    "ErrorBoundary": Renderer._error_boundary,
    "FragmentDecl": Renderer._fragment_decl,
    "FragmentRef": Renderer._fragment_ref,
    "Custom": Renderer._custom,
}


def render_html(node: Node, sources: BindingSources | None = None) -> str:
    """Render a decoded :class:`~fuaran_py.model.Node` tree to a body-fragment HTML string.

    ``sources`` is an optional host-supplied binding map (binding key → value)
    used to resolve non-``Static`` bindings; the headless baseline works with no
    sources, resolving ``Static`` bindings and placeholdering the rest.
    """
    fragments: dict[str, Node] = {}
    _collect_fragments(node, fragments)
    return Renderer(sources, fragments).render_node(node)
