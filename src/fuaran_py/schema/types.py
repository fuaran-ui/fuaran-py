"""The typed per-kind authoring model (the ``model.py`` enrichment, Phase 278).

The codec floor (:mod:`fuaran_py.model`) is a deliberately *generic*
``Node`` / ``Obj`` / ``Arr`` structure — enough to round-trip the wire byte-for-byte,
but not a surface a human authors against. This module is the **authoring** shape:
typed per-kind dataclasses (a ``NodeKind`` union, typed specs, typed ``Binding`` /
``Action`` / ``CellFormat`` / ``Accessibility``), the direct analogue of the typed
trees the F# (``Fuaran.UI``) and TypeScript (``@fuaran-ui/ui``) tiers author against.

The split is deliberate and load-bearing:

* **Decode** keeps producing the generic structural form — no conformance regression.
* **Authoring** uses these typed dataclasses; every one **lowers** to the generic
  :class:`~fuaran_py.model.Node` / :class:`~fuaran_py.model.Obj` via :func:`_lower`,
  and the proven canonical encoder (:func:`fuaran_py.canonical.encode_value`) does the
  serialisation. So a typed-authored tree is byte-identical to the corpus *by
  construction* — there is no second encoder to drift.

The ergonomic smart constructors that build these dataclasses (with per-kind
defaults + ARIA injection) live in :mod:`fuaran_py.ui`; this module is the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, runtime_checkable

from ..model import Arr, Obj, Value
from ..model import Node as WireNode

# ── Bare-string enum vocabularies (WIRE_FORMAT.md §3.5) ─────────────────────
# Each encodes as the bare string itself, so a ``Literal[...]`` alias is both the
# author-facing type and the wire value — no runtime enum object needed.

Tone = Literal["Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info"]
Weight = Literal["Compact", "Standard", "Spacious"]
Emphasis = Literal["Quiet", "Normal", "Loud"]
Orientation = Literal["Vertical", "Horizontal"]
SpacerSize = Literal["Small", "Medium", "Large"]
BadgeVariant = Literal["Neutral", "Brand", "Success", "Warning", "Critical", "Info"]
HeadingVariant = Literal["Standard", "Eyebrow", "Caption", "Lead"]
ButtonVariant = Literal["Primary", "Secondary", "Tertiary", "Destructive"]
ChartKind = Literal["Line", "Bar", "Area", "Pie", "Scatter", "Heatmap"]
StyleRole = Literal["None", "Eyebrow", "Data", "Lede", "Caption"]
FontVoice = Literal["Default", "Display", "Structural"]
LiveRegion = Literal["polite", "assertive", "off"]

# ── Unobservable-slot sentinels (WIRE_FORMAT.md §4 / §5) ────────────────────

CLOSURE = "<closure>"
"""A function-typed slot the encoder cannot observe (e.g. ``onSelect``)."""

OPAQUE = "<opaque>"
"""A ``Binding.Static`` whose typed value the encoder cannot decompose."""


@runtime_checkable
class _WireConvertible(Protocol):
    """Anything that lowers to a canonical wire :data:`~fuaran_py.model.Value`."""

    def to_wire(self) -> Value: ...


def _lower(value: object) -> Value:
    """Lower an authoring value into the generic structural model.

    Scalars pass through (the ``int`` / ``float`` distinction is preserved exactly,
    as in :func:`fuaran_py.model.from_json`); typed dataclasses defer to their
    ``to_wire``; sequences become :class:`~fuaran_py.model.Arr`; a plain ``dict``
    becomes a tag-less :class:`~fuaran_py.model.Obj` (a ``JsonValue`` record).
    """
    # bool is a subclass of int — the isinstance tuple below tests it harmlessly,
    # and the canonical encoder discriminates bool before int on the way out.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (WireNode, Obj, Arr)):
        return value
    if isinstance(value, (list, tuple)):
        return Arr([_lower(item) for item in value])
    if isinstance(value, dict):
        return Obj(None, {str(k): _lower(v) for k, v in value.items()})
    if isinstance(value, _WireConvertible):
        return value.to_wire()
    raise TypeError(f"cannot lower authoring value of type {type(value)!r}")


def _obj(tag: str | None, fields: dict[str, object]) -> Obj:
    """Build a structural :class:`~fuaran_py.model.Obj`, dropping ``None`` fields.

    Mirrors wire rule 4 (``None`` / absent optionals are omitted entirely, never
    emitted as ``null``). Every retained value is lowered.
    """
    return Obj(tag, {k: _lower(v) for k, v in fields.items() if v is not None})


# ── TextSource (WIRE_FORMAT.md §3.3) ────────────────────────────────────────


@dataclass(frozen=True)
class LiteralText:
    """A literal ``TextSource`` — ``{"$type":"Literal","text":…}``."""

    text: str

    def to_wire(self) -> Value:
        return Obj("Literal", {"text": self.text})


TextSource = LiteralText
"""The authoring ``TextSource`` surface (the ``Literal`` case; `Bound`/`I18n` are
emitted via raw bindings as the surface grows)."""

TextInput = str | LiteralText
"""A ``TextSource``, or a bare ``str`` coerced to a :class:`LiteralText`."""


# ── Binding (WIRE_FORMAT.md §3.3) ───────────────────────────────────────────


@dataclass(frozen=True)
class Static:
    """``Binding.Static`` — a constant value (or the :data:`OPAQUE` sentinel)."""

    value: Value

    def to_wire(self) -> Value:
        return Obj("Static", {"value": _lower(self.value)})


@dataclass(frozen=True)
class State:
    """``Binding.State`` — a host state-key lookup with a default."""

    key: str
    default_value: Value

    def to_wire(self) -> Value:
        return Obj("State", {"defaultValue": _lower(self.default_value), "key": self.key})


@dataclass(frozen=True)
class Filter:
    """``Binding.Filter`` — a named filter source."""

    name: str

    def to_wire(self) -> Value:
        return Obj("Filter", {"name": self.name})


Binding = Static | State | Filter

NumberInput = float | int | Binding
"""A numeric ``Binding``, or a bare number coerced to :class:`Static`."""

StringInput = str | Binding
"""A string ``Binding``, or a bare ``str`` coerced to :class:`Static`."""


# ── Action (WIRE_FORMAT.md §3.3 / §4) ───────────────────────────────────────


@dataclass(frozen=True)
class Chain:
    """``Action.Chain`` — a sequence of actions (the no-op default is ``Chain([])``)."""

    actions: tuple[Action, ...] = ()

    def to_wire(self) -> Value:
        return Obj("Chain", {"ops": Arr([_lower(a) for a in self.actions])})


@dataclass(frozen=True)
class Dispatch:
    """``Action.Dispatch`` — the message is a closure, erased to :data:`CLOSURE`."""

    msg: object = None

    def to_wire(self) -> Value:
        return Obj("Dispatch", {"msg": CLOSURE})


@dataclass(frozen=True)
class Navigate:
    route: str

    def to_wire(self) -> Value:
        return Obj("Navigate", {"route": self.route})


@dataclass(frozen=True)
class SetState:
    key: str
    value: Value

    def to_wire(self) -> Value:
        return Obj("SetState", {"key": self.key, "value": _lower(self.value)})


@dataclass(frozen=True)
class Notify:
    channel: str
    payload: Value

    def to_wire(self) -> Value:
        return Obj("Notify", {"channel": self.channel, "payload": _lower(self.payload)})


@dataclass(frozen=True)
class WriteToClipboard:
    text: str

    def to_wire(self) -> Value:
        return Obj("WriteToClipboard", {"text": self.text})


Action = Chain | Dispatch | Navigate | SetState | Notify | WriteToClipboard


# ── CellFormat (WIRE_FORMAT.md §3.3) ────────────────────────────────────────


@dataclass(frozen=True)
class FormatNone:
    def to_wire(self) -> Value:
        return Obj("None", {})


@dataclass(frozen=True)
class Currency:
    code: str

    def to_wire(self) -> Value:
        return Obj("Currency", {"code": self.code})


@dataclass(frozen=True)
class NumberFormat:
    decimals: int | None = None

    def to_wire(self) -> Value:
        return _obj("Number", {"decimals": self.decimals})


@dataclass(frozen=True)
class PercentFormat:
    decimals: int | None = None

    def to_wire(self) -> Value:
        return _obj("Percent", {"decimals": self.decimals})


@dataclass(frozen=True)
class SignificantDigits:
    digits: int

    def to_wire(self) -> Value:
        return Obj("SignificantDigits", {"digits": self.digits})


@dataclass(frozen=True)
class DateFormat:
    format: str

    def to_wire(self) -> Value:
        return Obj("Date", {"format": self.format})


CellFormat = FormatNone | Currency | NumberFormat | PercentFormat | SignificantDigits | DateFormat


# ── Accessibility / SemanticStyle / StateBehaviour (WIRE_FORMAT.md §3.1) ────


@dataclass(frozen=True)
class Accessibility:
    """The ARIA trait. ``role`` / ``live_region`` are bare strings; ``label`` /
    ``hidden`` are bindings. Omitted entirely from a node when not set."""

    label: Binding | None = None
    labelled_by: str | None = None
    described_by: str | None = None
    role: str | None = None
    live_region: LiveRegion | None = None
    hidden: Binding | None = None

    def to_wire(self) -> Value:
        return _obj(
            None,
            {
                "describedBy": self.described_by,
                "hidden": self.hidden,
                "label": self.label,
                "labelledBy": self.labelled_by,
                "liveRegion": self.live_region,
                "role": self.role,
            },
        )


@dataclass(frozen=True)
class SemanticStyle:
    """``SemanticStyle`` — emitted only when not all-default (rule: §3.1)."""

    emphasis: Emphasis = "Normal"
    tone: Tone = "Default"
    weight: Weight = "Standard"
    role: StyleRole | None = None
    voice: FontVoice | None = None

    def is_default(self) -> bool:
        return (
            self.emphasis == "Normal"
            and self.tone == "Default"
            and self.weight == "Standard"
            and (self.role is None or self.role == "None")
            and (self.voice is None or self.voice == "Default")
        )

    def to_wire(self) -> Value:
        role = None if self.role == "None" else self.role
        voice = None if self.voice == "Default" else self.voice
        return _obj(
            None,
            {
                "emphasis": self.emphasis,
                "role": role,
                "tone": self.tone,
                "voice": voice,
                "weight": self.weight,
            },
        )


@dataclass(frozen=True)
class StateBehaviour:
    """Loading / empty / error placeholders. Omitted entirely when all unset."""

    on_loading: UiNode | None = None
    on_empty: UiNode | None = None
    on_error: bool = False  # the ErrorPayload->Node callback is a closure → sentinel

    def is_empty(self) -> bool:
        return self.on_loading is None and self.on_empty is None and not self.on_error

    def to_wire(self) -> Value:
        return _obj(
            None,
            {
                "onEmpty": self.on_empty,
                "onError": CLOSURE if self.on_error else None,
                "onLoading": self.on_loading,
            },
        )


# ── TabHeader (a tag-less record nested in TabsSpec) ─────────────────────────


@dataclass(frozen=True)
class TabHeader:
    label: TextSource
    icon: str | None = None
    disabled: Binding | None = None

    def to_wire(self) -> Value:
        return _obj(None, {"disabled": self.disabled, "icon": self.icon, "label": self.label})


# ── NodeKind union (WIRE_FORMAT.md §3.2) ────────────────────────────────────
#
# The wire is *flat*: a kind's spec fields are hoisted directly under ``$type``
# (no ``spec`` wrapper). Each per-kind dataclass therefore lowers to a single
# ``Obj(tag, …)`` whose ``tag`` is the kind discriminator.


@runtime_checkable
class Kind(Protocol):
    """A ``NodeKind`` — lowers to its flat ``{"$type":…, …fields}`` object."""

    def to_wire(self) -> Obj: ...


# Layout ----------------------------------------------------------------------


@dataclass(frozen=True)
class Dashboard:
    children: tuple[UiNode, ...] = ()

    def to_wire(self) -> Obj:
        return _obj("Dashboard", {"children": list(self.children)})


@dataclass(frozen=True)
class Stack:
    children: tuple[UiNode, ...] = ()
    orientation: Orientation = "Vertical"
    wrap: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Stack",
            {"children": list(self.children), "orientation": self.orientation, "wrap": self.wrap},
        )


@dataclass(frozen=True)
class GridLayout:
    children: tuple[UiNode, ...] = ()
    cols: int = 12
    template_columns: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "GridLayout",
            {"children": list(self.children), "cols": self.cols, "templateColumns": self.template_columns},
        )


@dataclass(frozen=True)
class SplitPanel:
    children: tuple[UiNode, ...] = ()
    weight: float = 0.5

    def to_wire(self) -> Obj:
        return _obj("SplitPanel", {"children": list(self.children), "weight": self.weight})


@dataclass(frozen=True)
class Tabs:
    children: tuple[UiNode, ...] = ()
    active_index: Binding = field(default_factory=lambda: Static(0))
    orientation: Orientation = "Horizontal"
    active_tag: Binding | None = None
    tab_headers: tuple[TabHeader, ...] | None = None
    tab_tags: tuple[str, ...] | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Tabs",
            {
                "activeIndex": self.active_index,
                "activeTag": self.active_tag,
                "children": list(self.children),
                "onSelect": CLOSURE,
                "orientation": self.orientation,
                "tabHeaders": list(self.tab_headers) if self.tab_headers is not None else None,
                "tabTags": list(self.tab_tags) if self.tab_tags is not None else None,
            },
        )


@dataclass(frozen=True)
class Card:
    children: tuple[UiNode, ...] = ()
    heading: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj("Card", {"children": list(self.children), "heading": self.heading})


@dataclass(frozen=True)
class Stepper:
    children: tuple[UiNode, ...] = ()
    active_step: Binding = field(default_factory=lambda: Static(0))

    def to_wire(self) -> Obj:
        return _obj(
            "Stepper",
            {"activeStep": self.active_step, "children": list(self.children), "onSelect": CLOSURE},
        )


@dataclass(frozen=True)
class SummaryList:
    children: tuple[UiNode, ...] = ()
    heading: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj("SummaryList", {"children": list(self.children), "heading": self.heading})


@dataclass(frozen=True)
class Disclosure:
    children: tuple[UiNode, ...] = ()
    heading: TextSource = field(default_factory=lambda: LiteralText(""))
    open: Binding = field(default_factory=lambda: Static(False))
    default_open: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Disclosure",
            {
                "children": list(self.children),
                "defaultOpen": self.default_open,
                "heading": self.heading,
                "open": self.open,
            },
        )


# Display ---------------------------------------------------------------------


@dataclass(frozen=True)
class Heading:
    text: TextSource
    level: int = 2
    variant: HeadingVariant = "Standard"

    def to_wire(self) -> Obj:
        return _obj("Heading", {"level": self.level, "text": self.text, "variant": self.variant})


@dataclass(frozen=True)
class Markdown:
    text: TextSource

    def to_wire(self) -> Obj:
        return _obj("Markdown", {"text": self.text})


@dataclass(frozen=True)
class Metric:
    label: TextSource
    source: Binding
    format: CellFormat = field(default_factory=FormatNone)
    tone: Tone = "Default"
    weight: Weight = "Standard"
    emphasis: Emphasis = "Normal"
    icon: str | None = None
    subtext: TextSource | None = None
    trend: Binding | None = None
    trend_format: CellFormat | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Metric",
            {
                "emphasis": self.emphasis,
                "format": self.format,
                "icon": self.icon,
                "label": self.label,
                "source": self.source,
                "subtext": self.subtext,
                "tone": self.tone,
                "trend": self.trend,
                "trendFormat": self.trend_format,
                "weight": self.weight,
            },
        )


@dataclass(frozen=True)
class Badge:
    label: TextSource
    variant: BadgeVariant = "Neutral"

    def to_wire(self) -> Obj:
        return _obj("Badge", {"label": self.label, "variant": self.variant})


@dataclass(frozen=True)
class Sparkline:
    source: Binding

    def to_wire(self) -> Obj:
        return _obj("Sparkline", {"source": self.source})


@dataclass(frozen=True)
class Spacer:
    size: SpacerSize = "Medium"

    def to_wire(self) -> Obj:
        return _obj("Spacer", {"size": self.size})


@dataclass(frozen=True)
class Callout:
    body: TextSource
    tone: Tone = "Info"
    dismissable: bool = False
    heading: TextSource | None = None
    icon: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Callout",
            {
                "body": self.body,
                "dismissable": self.dismissable,
                "heading": self.heading,
                "icon": self.icon,
                "tone": self.tone,
            },
        )


@dataclass(frozen=True)
class Progress:
    fraction: Binding
    indeterminate: bool = False
    tone: Tone = "Default"
    label: TextSource | None = None
    caveat: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Progress",
            {
                "caveat": self.caveat,
                "fraction": self.fraction,
                "indeterminate": self.indeterminate,
                "label": self.label,
                "tone": self.tone,
            },
        )


@dataclass(frozen=True)
class Skeleton:
    rows: int

    def to_wire(self) -> Obj:
        return _obj("Skeleton", {"rows": self.rows})


@dataclass(frozen=True)
class LabelValueRow:
    label: TextSource
    source: Binding
    format: CellFormat = field(default_factory=FormatNone)
    emphasis: bool = False
    help: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "LabelValueRow",
            {
                "emphasis": self.emphasis,
                "format": self.format,
                "help": self.help,
                "label": self.label,
                "source": self.source,
            },
        )


@dataclass(frozen=True)
class Link:
    href: Binding
    label: TextSource
    download: bool = False
    rel: str | None = None
    target: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Link",
            {
                "download": self.download,
                "href": self.href,
                "label": self.label,
                "rel": self.rel,
                "target": self.target,
            },
        )


# Input -----------------------------------------------------------------------


@dataclass(frozen=True)
class Button:
    label: TextSource
    on_click: Action = field(default_factory=Chain)
    variant: ButtonVariant = "Secondary"
    disabled: Binding | None = None
    icon: str | None = None

    def to_wire(self) -> Obj:
        # ButtonSpec.Tooltip is intentionally never emitted (WIRE_FORMAT.md §10.1).
        return _obj(
            "Button",
            {
                "disabled": self.disabled,
                "icon": self.icon,
                "label": self.label,
                "onClick": self.on_click,
                "variant": self.variant,
            },
        )


@dataclass(frozen=True)
class Select:
    label: TextSource
    source: Binding
    value: Binding
    placeholder: TextSource | None = None
    disabled: Binding | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Select",
            {
                "disabled": self.disabled,
                "label": self.label,
                "onChange": CLOSURE,
                "placeholder": self.placeholder,
                "source": self.source,
                "value": self.value,
            },
        )


@dataclass(frozen=True)
class FileUpload:
    label: TextSource
    accept: tuple[str, ...] = ()
    multiple: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "FileUpload",
            {"accept": list(self.accept), "label": self.label, "multiple": self.multiple, "onSelect": CLOSURE},
        )


# Visualisation ---------------------------------------------------------------


@dataclass(frozen=True)
class Chart:
    source: Binding
    x_field: str
    y_fields: tuple[str, ...]
    kind: ChartKind = "Line"
    stacked: bool = False
    title: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Chart",
            {
                "kind": self.kind,
                "source": self.source,
                "stacked": self.stacked,
                "title": self.title,
                "xField": self.x_field,
                "yFields": list(self.y_fields),
            },
        )


@dataclass(frozen=True)
class Table:
    headers: tuple[TextSource, ...]
    rows: tuple[tuple[TextSource, ...], ...]

    def to_wire(self) -> Obj:
        return _obj(
            "Table",
            {"headers": list(self.headers), "rows": [list(r) for r in self.rows]},
        )


@dataclass(frozen=True)
class Map:
    source: Binding
    centre_latitude: float = 0.0
    centre_longitude: float = 0.0
    zoom: float = 4.0

    def to_wire(self) -> Obj:
        return _obj(
            "Map",
            {
                "centreLatitude": self.centre_latitude,
                "centreLongitude": self.centre_longitude,
                "source": self.source,
                "zoom": self.zoom,
            },
        )


# Structural ------------------------------------------------------------------


@dataclass(frozen=True)
class Custom:
    module_id: str
    component_id: str
    props: dict[str, Value] = field(default_factory=dict)
    exposed_node_ids: tuple[str, ...] | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Custom",
            {
                "componentId": self.component_id,
                "exposedNodeIds": list(self.exposed_node_ids) if self.exposed_node_ids is not None else None,
                "moduleId": self.module_id,
                "props": self.props,
            },
        )


@dataclass(frozen=True)
class ErrorBoundary:
    child: UiNode
    fallback: UiNode

    def to_wire(self) -> Obj:
        return _obj("ErrorBoundary", {"child": self.child, "fallback": self.fallback})


@dataclass(frozen=True)
class FragmentDecl:
    name: str
    body: UiNode

    def to_wire(self) -> Obj:
        return _obj("FragmentDecl", {"body": self.body, "name": self.name})


@dataclass(frozen=True)
class FragmentRef:
    name: str

    def to_wire(self) -> Obj:
        return _obj("FragmentRef", {"name": self.name})


# ── The node envelope ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class UiNode:
    """A typed UI node: ``id`` + a typed :class:`Kind`, plus optional traits.

    Lowers to the generic :class:`~fuaran_py.model.Node` via :meth:`to_wire`; the
    canonical encoder then serialises it byte-identically to the corpus. ``style``
    is omitted when all-default, ``state`` when empty, ``accessibility`` when unset
    (wire rule 4 / §3.1).
    """

    id: str
    kind: Kind
    accessibility: Accessibility | None = None
    style: SemanticStyle | None = None
    state: StateBehaviour | None = None

    def to_wire(self) -> WireNode:
        extras: dict[str, Value] = {}
        if self.state is not None and not self.state.is_empty():
            extras["state"] = self.state.to_wire()
        if self.style is not None and not self.style.is_default():
            extras["style"] = self.style.to_wire()
        if self.accessibility is not None:
            extras["accessibility"] = self.accessibility.to_wire()
        return WireNode(self.id, self.kind.to_wire(), extras)

    def replace(self, **changes: object) -> UiNode:
        """Return a copy with the named traits replaced (e.g. ``n.replace(style=…)``)."""
        return replace(self, **changes)  # type: ignore[arg-type]
