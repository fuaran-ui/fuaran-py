"""``fuaran_py.ui`` — the ergonomic, typed authoring surface.

The Python analogue of ``@fuaran-ui/ui`` / ``Fuaran.UI``: smart constructors over
the typed per-kind model (:mod:`fuaran_py.schema.types`) that inject per-kind
defaults + ARIA, so a Python developer authors a Fuaran tree the same way an F# or
TypeScript developer does — and :func:`encode` serialises it to canonical JSON
byte-identically to the wire-format corpus.

This is a **human-developer** authoring surface. The LLM's emission surface is the
canonical JSON wire format itself, for every host; these constructors are what
humans write app shells, fragment libraries, fixtures, and golden trees in::

    from fuaran_py.ui import fuaran, binding, action, format, encode

    tree = fuaran.dashboard(
        "root",
        children=[
            fuaran.metric("rev", label="Revenue", value=1234.5, format=format.currency("GBP")),
            fuaran.markdown("note", "Updated hourly."),
        ],
    )
    wire = encode(tree)   # canonical JSON, byte-identical to the corpus

Namespaces mirror the cross-tier vocabulary: ``fuaran.*`` element constructors;
``binding`` / ``action`` / ``format`` cross-cutting helpers; ``node`` postfix
modifiers; ``accessibility`` the per-kind ARIA defaults.
"""

from __future__ import annotations

from dataclasses import replace as _replace

from ..canonical import encode_value
from ..schema import types as t
from ..schema.types import (
    Accessibility,
    Action,
    Binding,
    CellFormat,
    Kind,
    SemanticStyle,
    StateBehaviour,
    TextSource,
    UiNode,
)

# ── Ergonomic input coercions (the Pythonic analogue of the TS options object) ─


def _text(value: t.TextInput) -> TextSource:
    """A bare ``str`` becomes a ``TextSource.Literal``."""
    return t.LiteralText(value) if isinstance(value, str) else value


def _num_binding(value: t.NumberInput) -> Binding:
    """A bare number becomes a ``Binding.Static``."""
    return t.Static(value) if isinstance(value, (int, float)) else value


def _str_binding(value: t.StringInput) -> Binding:
    """A bare ``str`` becomes a ``Binding.Static``."""
    return t.Static(value) if isinstance(value, str) else value


def _metric_value(value: str | t.NumberInput) -> Binding:
    """KPI value coercion: a number → ``Static``; a display string is leniently
    parsed (non-numeric characters stripped) into a ``Static`` — a convenience for
    prototypes like ``value="£42k"``; pass a number or a binding for precision."""
    if isinstance(value, bool):  # guard: bool is an int subclass
        return t.Static(value)
    if isinstance(value, (int, float)):
        return t.Static(value)
    if isinstance(value, str):
        cleaned = "".join(c for c in value if c in "0123456789.eE+-")
        try:
            parsed = float(cleaned)
        except ValueError:
            parsed = 0.0
        return t.Static(parsed)
    return value


# ── Typed binding entry points (the ``binding`` namespace) ───────────────────


class binding:  # noqa: N801 — namespace object, mirrors the cross-tier `binding.*` vocabulary
    """Typed ``Binding`` constructors."""

    @staticmethod
    def static(value: t.Value) -> Binding:
        return t.Static(value)

    @staticmethod
    def state(key: str, default_value: t.Value) -> Binding:
        return t.State(key, default_value)

    @staticmethod
    def filter(name: str) -> Binding:  # noqa: A003 — wire case name
        return t.Filter(name)

    @staticmethod
    def opaque() -> Binding:
        """A ``Static`` whose value the encoder cannot decompose (``"<opaque>"``)."""
        return t.Static(t.OPAQUE)


# ── Typed action entry points (the ``action`` namespace) ─────────────────────


class action:  # noqa: N801 — namespace object
    """Typed ``Action`` constructors."""

    @staticmethod
    def chain(actions: tuple[Action, ...] | list[Action] = ()) -> Action:
        return t.Chain(tuple(actions))

    @staticmethod
    def dispatch(msg: object = None) -> Action:
        return t.Dispatch(msg)

    @staticmethod
    def navigate(route: str) -> Action:
        return t.Navigate(route)

    @staticmethod
    def set_state(key: str, value: t.Value) -> Action:
        return t.SetState(key, value)

    @staticmethod
    def notify(channel: str, payload: t.Value) -> Action:
        return t.Notify(channel, payload)

    @staticmethod
    def write_to_clipboard(text: str) -> Action:
        return t.WriteToClipboard(text)


# ── Typed cell-format entry points (the ``format`` namespace) ────────────────


class format:  # noqa: N801, A001 — namespace object, mirrors the cross-tier `format.*` vocabulary
    """Typed ``CellFormat`` constructors (KPI / grid-column value formatting)."""

    @staticmethod
    def none() -> CellFormat:
        return t.FormatNone()

    @staticmethod
    def currency(code: str) -> CellFormat:
        return t.Currency(code)

    @staticmethod
    def number(decimals: int | None = None) -> CellFormat:
        return t.NumberFormat(decimals)

    @staticmethod
    def percent(decimals: int | None = None) -> CellFormat:
        return t.PercentFormat(decimals)

    @staticmethod
    def significant_digits(digits: int) -> CellFormat:
        return t.SignificantDigits(digits)

    @staticmethod
    def date(fmt: str) -> CellFormat:
        return t.DateFormat(fmt)


# ── Per-kind ARIA defaults (the ``accessibility`` namespace) ─────────────────
#
# Mirrors F# ``Defaults.Accessibility`` / TS ``defaults.accessibility``: decorative
# and structural kinds default to no ARIA (``None``); interactive and notification
# kinds carry a role / live-region so the smart-ctor output is accessible by default.


class accessibility:  # noqa: N801 — namespace object
    """Per-kind ARIA defaults injected by the smart constructors."""

    none: Accessibility | None = None
    button = Accessibility(role="button")
    select = Accessibility(role="combobox")
    form = Accessibility(role="form")
    file_upload = Accessibility(role="button")
    callout = Accessibility(role="alert", live_region="assertive")
    progress = Accessibility(role="progressbar", live_region="polite")
    metric = Accessibility(live_region="polite")
    dashboard = Accessibility(role="main")
    card = Accessibility(role="region")
    summary_list = Accessibility(role="region")
    disclosure = Accessibility(role="region")
    tabs = Accessibility(role="tablist")
    grid = Accessibility(role="region")
    chart = Accessibility(role="region", live_region="polite")
    map = Accessibility(role="region")
    table: Accessibility | None = None


# ── Per-node postfix modifiers (the ``node`` namespace) ──────────────────────


class node:  # noqa: N801 — namespace object
    """Immutable postfix modifiers — each returns a new :class:`UiNode`."""

    @staticmethod
    def with_accessibility(a11y: Accessibility | None, n: UiNode) -> UiNode:
        return n.replace(accessibility=a11y)

    @staticmethod
    def bare(n: UiNode) -> UiNode:
        """Strip the injected ARIA trait (e.g. to match an ARIA-free fixture)."""
        return n.replace(accessibility=None)

    @staticmethod
    def with_tone(tone: t.Tone, n: UiNode) -> UiNode:
        return n.replace(style=_replace(_style(n), tone=tone))

    @staticmethod
    def with_weight(weight: t.Weight, n: UiNode) -> UiNode:
        return n.replace(style=_replace(_style(n), weight=weight))

    @staticmethod
    def with_emphasis(emphasis: t.Emphasis, n: UiNode) -> UiNode:
        return n.replace(style=_replace(_style(n), emphasis=emphasis))

    @staticmethod
    def with_role(role: t.StyleRole, n: UiNode) -> UiNode:
        return n.replace(style=_replace(_style(n), role=role))

    @staticmethod
    def with_voice(voice: t.FontVoice, n: UiNode) -> UiNode:
        return n.replace(style=_replace(_style(n), voice=voice))

    @staticmethod
    def on_loading(placeholder: UiNode, n: UiNode) -> UiNode:
        return n.replace(state=_replace(_state(n), on_loading=placeholder))

    @staticmethod
    def on_empty(placeholder: UiNode, n: UiNode) -> UiNode:
        return n.replace(state=_replace(_state(n), on_empty=placeholder))


def _style(n: UiNode) -> SemanticStyle:
    return n.style if n.style is not None else SemanticStyle()


def _state(n: UiNode) -> StateBehaviour:
    return n.state if n.state is not None else StateBehaviour()


# ── Components — the ``fuaran`` author surface ───────────────────────────────


def _node(id: str, kind: Kind, a11y: Accessibility | None = None) -> UiNode:  # noqa: A002
    return UiNode(id=id, kind=kind, accessibility=a11y)


class fuaran:  # noqa: N801 — namespace object, mirrors the cross-tier `fuaran.*` vocabulary
    """Element constructors. Each injects per-kind defaults + ARIA, exactly as the
    F#/TS smart constructors do; pass ``node.bare(...)`` to drop the ARIA trait."""

    # ── Layout ───────────────────────────────────────────────────────────────
    @staticmethod
    def dashboard(id: str, *, children: list[UiNode] | None = None) -> UiNode:  # noqa: A002
        return _node(id, t.Dashboard(tuple(children or ())), accessibility.dashboard)

    @staticmethod
    def stack(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        orientation: t.Orientation = "Vertical",
        wrap: bool = False,
    ) -> UiNode:
        return _node(id, t.Stack(tuple(children or ()), orientation, wrap), accessibility.none)

    @staticmethod
    def grid_layout(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        cols: int = 12,
        template_columns: str | None = None,
    ) -> UiNode:
        return _node(id, t.GridLayout(tuple(children or ()), cols, template_columns), accessibility.none)

    @staticmethod
    def split_panel(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        weight: float = 0.5,
    ) -> UiNode:
        return _node(id, t.SplitPanel(tuple(children or ()), weight), accessibility.none)

    @staticmethod
    def tabs(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        active_index: Binding | int = 0,
        orientation: t.Orientation = "Horizontal",
        active_tag: Binding | None = None,
        tab_headers: list[t.TabHeader] | None = None,
        tab_tags: list[str] | None = None,
    ) -> UiNode:
        idx = t.Static(active_index) if isinstance(active_index, int) else active_index
        kind = t.Tabs(
            tuple(children or ()),
            idx,
            orientation,
            active_tag,
            tuple(tab_headers) if tab_headers is not None else None,
            tuple(tab_tags) if tab_tags is not None else None,
        )
        return _node(id, kind, accessibility.tabs)

    @staticmethod
    def card(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        heading: t.TextInput | None = None,
    ) -> UiNode:
        return _node(
            id, t.Card(tuple(children or ()), _text(heading) if heading is not None else None), accessibility.card
        )

    @staticmethod
    def stepper(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        active_step: Binding | int = 0,
    ) -> UiNode:
        step = t.Static(active_step) if isinstance(active_step, int) else active_step
        return _node(id, t.Stepper(tuple(children or ()), step), accessibility.none)

    @staticmethod
    def summary_list(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        heading: t.TextInput | None = None,
    ) -> UiNode:
        return _node(
            id,
            t.SummaryList(tuple(children or ()), _text(heading) if heading is not None else None),
            accessibility.summary_list,
        )

    @staticmethod
    def disclosure(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        heading: t.TextInput = "",
        open: Binding | bool = False,  # noqa: A002
        default_open: bool = False,
    ) -> UiNode:
        op = t.Static(open) if isinstance(open, bool) else open
        return _node(
            id, t.Disclosure(tuple(children or ()), _text(heading), op, default_open), accessibility.disclosure
        )

    # ── Display ──────────────────────────────────────────────────────────────
    @staticmethod
    def heading(id: str, text: t.TextInput, *, level: int = 2, variant: t.HeadingVariant = "Standard") -> UiNode:  # noqa: A002
        return _node(id, t.Heading(_text(text), level, variant), accessibility.none)

    @staticmethod
    def markdown(id: str, body: t.TextInput) -> UiNode:  # noqa: A002
        return _node(id, t.Markdown(_text(body)), accessibility.none)

    @staticmethod
    def metric(
        id: str,  # noqa: A002
        *,
        label: t.TextInput,
        value: str | t.NumberInput,
        format: CellFormat | None = None,  # noqa: A002
        tone: t.Tone = "Default",
        weight: t.Weight = "Standard",
        emphasis: t.Emphasis = "Normal",
        icon: str | None = None,
        subtext: t.TextInput | None = None,
        trend: t.NumberInput | None = None,
        trend_format: CellFormat | None = None,
    ) -> UiNode:
        kind = t.Metric(
            label=_text(label),
            source=_metric_value(value),
            format=format if format is not None else t.FormatNone(),
            tone=tone,
            weight=weight,
            emphasis=emphasis,
            icon=icon,
            subtext=_text(subtext) if subtext is not None else None,
            trend=_num_binding(trend) if trend is not None else None,
            trend_format=trend_format,
        )
        return _node(id, kind, accessibility.metric)

    @staticmethod
    def label_value_row(
        id: str,  # noqa: A002
        *,
        label: t.TextInput,
        value: t.NumberInput,
        format: CellFormat | None = None,  # noqa: A002
        emphasis: bool = False,
        help: t.TextInput | None = None,  # noqa: A002
    ) -> UiNode:
        kind = t.LabelValueRow(
            label=_text(label),
            source=_num_binding(value),
            format=format if format is not None else t.FormatNone(),
            emphasis=emphasis,
            help=_text(help) if help is not None else None,
        )
        return _node(id, kind, accessibility.none)

    @staticmethod
    def badge(id: str, *, label: t.TextInput, variant: t.BadgeVariant = "Neutral") -> UiNode:  # noqa: A002
        return _node(id, t.Badge(_text(label), variant), accessibility.none)

    @staticmethod
    def link(
        id: str,  # noqa: A002
        *,
        href: t.StringInput,
        label: t.TextInput,
        rel: str | None = None,
        target: str | None = None,
        download: bool = False,
    ) -> UiNode:
        return _node(id, t.Link(_str_binding(href), _text(label), download, rel, target), accessibility.none)

    @staticmethod
    def sparkline(id: str, *, source: Binding) -> UiNode:  # noqa: A002
        return _node(id, t.Sparkline(source), accessibility.none)

    @staticmethod
    def spacer(id: str, *, size: t.SpacerSize = "Medium") -> UiNode:  # noqa: A002
        return _node(id, t.Spacer(size), accessibility.none)

    @staticmethod
    def callout(
        id: str,  # noqa: A002
        *,
        body: t.TextInput,
        tone: t.Tone = "Info",
        heading: t.TextInput | None = None,
        icon: str | None = None,
        dismissable: bool = False,
    ) -> UiNode:
        kind = t.Callout(_text(body), tone, dismissable, _text(heading) if heading is not None else None, icon)
        return _node(id, kind, accessibility.callout)

    @staticmethod
    def progress(
        id: str,  # noqa: A002
        *,
        fraction: t.NumberInput,
        label: t.TextInput | None = None,
        caveat: t.TextInput | None = None,
        indeterminate: bool = False,
        tone: t.Tone = "Default",
    ) -> UiNode:
        kind = t.Progress(
            _num_binding(fraction),
            indeterminate,
            tone,
            _text(label) if label is not None else None,
            _text(caveat) if caveat is not None else None,
        )
        return _node(id, kind, accessibility.progress)

    @staticmethod
    def skeleton(id: str, rows: int) -> UiNode:  # noqa: A002
        return _node(id, t.Skeleton(rows), accessibility.none)

    # ── Input ────────────────────────────────────────────────────────────────
    @staticmethod
    def button(
        id: str,  # noqa: A002
        *,
        label: t.TextInput,
        on_click: Action | None = None,
        variant: t.ButtonVariant = "Secondary",
        disabled: Binding | None = None,
        icon: str | None = None,
    ) -> UiNode:
        kind = t.Button(_text(label), on_click if on_click is not None else t.Chain(), variant, disabled, icon)
        return _node(id, kind, accessibility.button)

    @staticmethod
    def select(
        id: str,  # noqa: A002
        *,
        label: t.TextInput,
        source: Binding,
        value: Binding,
        placeholder: t.TextInput | None = None,
        disabled: Binding | None = None,
    ) -> UiNode:
        kind = t.Select(_text(label), source, value, _text(placeholder) if placeholder is not None else None, disabled)
        return _node(id, kind, accessibility.select)

    @staticmethod
    def file_upload(
        id: str,  # noqa: A002
        *,
        label: t.TextInput,
        accept: list[str] | None = None,
        multiple: bool = False,
    ) -> UiNode:
        return _node(id, t.FileUpload(_text(label), tuple(accept or ()), multiple), accessibility.file_upload)

    # ── Visualisation ─────────────────────────────────────────────────────────
    @staticmethod
    def chart(
        id: str,  # noqa: A002
        *,
        source: Binding,
        x_field: str,
        y_fields: list[str],
        kind: t.ChartKind = "Line",
        title: t.TextInput | None = None,
        stacked: bool = False,
    ) -> UiNode:
        spec = t.Chart(source, x_field, tuple(y_fields), kind, stacked, _text(title) if title is not None else None)
        return _node(id, spec, accessibility.chart)

    @staticmethod
    def table(
        id: str,  # noqa: A002
        *,
        headers: list[t.TextInput],
        rows: list[list[t.TextInput]],
    ) -> UiNode:
        spec = t.Table(
            tuple(_text(h) for h in headers),
            tuple(tuple(_text(c) for c in row) for row in rows),
        )
        return _node(id, spec, accessibility.table)

    @staticmethod
    def map(
        id: str,  # noqa: A002
        *,
        source: Binding,
        centre_latitude: float = 0.0,
        centre_longitude: float = 0.0,
        zoom: float = 4.0,
    ) -> UiNode:
        return _node(id, t.Map(source, centre_latitude, centre_longitude, zoom), accessibility.map)

    # ── Structural ─────────────────────────────────────────────────────────────
    @staticmethod
    def custom(
        id: str,  # noqa: A002
        *,
        module_id: str,
        component_id: str,
        props: dict[str, t.Value] | None = None,
        exposed_node_ids: list[str] | None = None,
    ) -> UiNode:
        kind = t.Custom(
            module_id,
            component_id,
            props if props is not None else {},
            tuple(exposed_node_ids) if exposed_node_ids is not None else None,
        )
        return _node(id, kind, accessibility.none)

    @staticmethod
    def error_boundary(id: str, *, child: UiNode, fallback: UiNode) -> UiNode:  # noqa: A002
        return _node(id, t.ErrorBoundary(child, fallback), accessibility.none)

    @staticmethod
    def fragment_decl(id: str, *, name: str, body: UiNode) -> UiNode:  # noqa: A002
        return _node(id, t.FragmentDecl(name, body), accessibility.none)

    @staticmethod
    def fragment_ref(id: str, *, name: str) -> UiNode:  # noqa: A002
        return _node(id, t.FragmentRef(name), accessibility.none)


# ── Encoding ─────────────────────────────────────────────────────────────────


def encode(n: UiNode) -> str:
    """Serialise a typed :class:`UiNode` to canonical wire JSON.

    Lowers the typed tree to the generic structural model and runs the proven
    canonical encoder — byte-identical to :func:`fuaran_py.encode_node` over the
    same tree, and to the wire-format corpus for trees that match it.
    """
    return encode_value(n.to_wire())


__all__ = [
    "fuaran",
    "binding",
    "action",
    "format",
    "node",
    "accessibility",
    "encode",
    "UiNode",
    "Accessibility",
    "SemanticStyle",
    "StateBehaviour",
]
