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

from collections.abc import Iterable
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
BadgeVariant = Literal["Neutral", "Brand", "Success", "Warning", "Critical", "Info"]
HeadingVariant = Literal["Standard", "Eyebrow", "Caption", "Lead"]
ButtonVariant = Literal["Primary", "Secondary", "Tertiary", "Destructive"]
ChartKind = Literal["Line", "Bar", "Area", "Pie", "Scatter", "Heatmap"]
StyleRole = Literal["None", "Eyebrow", "Data", "Lede", "Caption"]
FontVoice = Literal["Default", "Display", "Structural"]
LiveRegion = Literal["polite", "assertive", "off"]
ImageVariant = Literal["Default", "Avatar", "Rounded"]
# fuaran#1077 — the three `Image` presentation slots (WIRE_FORMAT §3.6.2). Closed
# TOKEN vocabularies, never CSS values: `ImageAspect` names one of four ratios and
# carries no number, pair or stylesheet spelling. `fit` says what happens to
# pixels that do not match the box; `aspectRatio` reserves the box BEFORE the
# image arrives; a host derives neither from the other.
ImageFit = Literal["Natural", "Cover", "Contain"]
ImageAspect = Literal["Natural", "Square", "FourThree", "ThreeTwo", "SixteenNine"]
# `Eager` is the default deliberately, and it is not the "unoptimised" value:
# deferring an above-the-fold image DELAYS the largest contentful paint, and only
# the author knows where the image sits — so the format declines to guess.
ImageLoading = Literal["Eager", "Lazy"]
ScrollOrientation = Literal["Vertical", "Horizontal", "Both"]
DateVariant = Literal["Date", "Time", "DateTime"]
MathDisplay = Literal["Inline", "Block"]
IconSize = Literal["Small", "Medium", "Large"]  # Phase 821 — the Icon display kind
# fuaran#867 — which direction of movement is an improvement. `Neutral` is
# RESERVED and deliberately absent: the slot is an enum precisely so that a later
# admission is a bare-string addition rather than a type replacement.
TrendPolarity = Literal["HigherIsBetter", "LowerIsBetter"]

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
    """A literal ``TextSource`` — 0.2.0: the bare JSON string IS the canonical form."""

    text: str

    def to_wire(self) -> Value:
        return self.text


# The ``TextSource`` / ``TextInput`` aliases are defined just after the binding
# cases below, because ``Bound`` (a ``TextSource``) wraps a ``Binding``.


# ── Binding (WIRE_FORMAT.md §3.3) ───────────────────────────────────────────


@dataclass(frozen=True)
class Static:
    """``Binding.Static`` — a constant value (or the :data:`OPAQUE` sentinel)."""

    value: Value

    def to_wire(self) -> Value:
        # Phase 677 — absence is structural: a binding carrying no value omits the
        # key rather than emitting JSON null, for which the wire model has no case.
        if self.value is None:
            return Obj("Static", {})
        return Obj("Static", {"value": _lower(self.value)})


@dataclass(frozen=True)
class State:
    """``Binding.State`` — a host state-key lookup with a default."""

    key: str
    default_value: Value

    def to_wire(self) -> Value:
        # Phase 677 — same rule as `Static`: absence omits, never null.
        if self.default_value is None:
            return Obj("State", {"key": self.key})
        return Obj("State", {"defaultValue": _lower(self.default_value), "key": self.key})


@dataclass(frozen=True)
class Filter:
    """``Binding.Filter`` — a named filter source."""

    name: str

    def to_wire(self) -> Value:
        return Obj("Filter", {"name": self.name})


@dataclass(frozen=True)
class Selection:
    """``Binding.Selection`` — the last-selected row on ``node_id``.

    The row-typed ``accessor`` is a closure and rides off the wire (0.2.0);
    ``field`` (Phase 632) is its wire-expressible twin, projecting the named
    row property declaratively. ``default_value`` (0.2.9) yields until the
    user first selects a row; ``None`` means absent (omitted, never null).
    """

    node_id: str
    default_value: Value = None
    field: str | None = None

    def to_wire(self) -> Value:
        return _obj("Selection", {"defaultValue": self.default_value, "field": self.field, "nodeId": self.node_id})


@dataclass(frozen=True)
class Now:
    """``Binding.Now`` — the host-furnished current instant (ISO-8601 UTC).

    Tag-only on the wire (``{"$type":"Now"}``): the clock lives in the HOST,
    resolved once per render pass, never on the wire — which is what keeps a
    tree a pure value and lets a replayed op-stream reproduce its original
    render. The typed ``project`` accessor is a closure, off the wire.
    """

    def to_wire(self) -> Value:
        return Obj("Now", {})


# ── Locale-aware Format DU + LocaleSource (WIRE_FORMAT.md §3.3, Phase 102) ───
# Distinct from CellFormat: ``Currency`` carries ``isoCode`` (not ``code``),
# ``Date`` carries ``dateStyle`` (a bare enum, not a format string).

DateStyle = Literal["Short", "Medium", "Long", "Full"]
RelativeTimeUnit = Literal["Second", "Minute", "Hour", "Day", "Week", "Month", "Year"]
# Phase 819 — the numeric source counts this unit (Seconds = 1, Minutes = 60,
# Hours = 3600); presentation is `Compact` "1h 20m", `Clock` "1:20:00",
# `Long` "1 hour 20 minutes".
DurationUnit = Literal["Seconds", "Minutes", "Hours"]
DurationStyle = Literal["Compact", "Clock", "Long"]


@dataclass(frozen=True)
class FmtNumber:
    decimals: int | None = None

    def to_wire(self) -> Value:
        return _obj("Number", {"decimals": self.decimals})


@dataclass(frozen=True)
class FmtCurrency:
    iso_code: str

    def to_wire(self) -> Value:
        return Obj("Currency", {"isoCode": self.iso_code})


@dataclass(frozen=True)
class FmtPercent:
    decimals: int | None = None

    def to_wire(self) -> Value:
        return _obj("Percent", {"decimals": self.decimals})


@dataclass(frozen=True)
class FmtDate:
    date_style: DateStyle

    def to_wire(self) -> Value:
        return Obj("Date", {"dateStyle": self.date_style})


@dataclass(frozen=True)
class FmtRelativeTime:
    unit: RelativeTimeUnit

    def to_wire(self) -> Value:
        return Obj("RelativeTime", {"unit": self.unit})


@dataclass(frozen=True)
class FmtDuration:
    """``Format.Duration`` (Phase 819) — locale-independent duration formatting."""

    unit: DurationUnit
    style: DurationStyle

    def to_wire(self) -> Value:
        return Obj("Duration", {"style": self.style, "unit": self.unit})


Format = FmtNumber | FmtCurrency | FmtPercent | FmtDate | FmtRelativeTime | FmtDuration


@dataclass(frozen=True)
class Ambient:
    def to_wire(self) -> Value:
        return Obj("Ambient", {})


@dataclass(frozen=True)
class Explicit:
    tag: str

    def to_wire(self) -> Value:
        return Obj("Explicit", {"tag": self.tag})


LocaleSource = Ambient | Explicit


# ── LocalFlushTrigger (WIRE_FORMAT.md §3.3) ─────────────────────────────────


@dataclass(frozen=True)
class OnBlur:
    def to_wire(self) -> Value:
        return Obj("OnBlur", {})


@dataclass(frozen=True)
class OnDebounce:
    milliseconds: int

    def to_wire(self) -> Value:
        return Obj("OnDebounce", {"milliseconds": self.milliseconds})


LocalFlushTrigger = OnBlur | OnDebounce


# ── The remaining binding cases (Format / Local) ────────────────────────────


@dataclass(frozen=True)
class FormatBinding:
    """``Binding.Format`` — a locale-aware formatted value over a numeric source."""

    source: Binding
    format: Format
    locale: LocaleSource

    def to_wire(self) -> Value:
        return Obj(
            "Format", {"format": _lower(self.format), "locale": _lower(self.locale), "source": _lower(self.source)}
        )


@dataclass(frozen=True)
class Local:
    """``Binding.Local`` — a component-scoped buffer; ``format``/``onCommit``/``parse`` are closures."""

    initial_from: Binding
    flush_on: LocalFlushTrigger

    def to_wire(self) -> Value:
        return Obj(
            "Local",
            {
                "flushOn": _lower(self.flush_on),
                "format": CLOSURE,
                "initialFrom": _lower(self.initial_from),
                "onCommit": CLOSURE,
                "parse": CLOSURE,
            },
        )


Binding = Static | State | Filter | Selection | Now | FormatBinding | Local

NumberInput = float | int | Binding
"""A numeric ``Binding``, or a bare number coerced to :class:`Static`."""

StringInput = str | Binding
"""A string ``Binding``, or a bare ``str`` coerced to :class:`Static`."""


# ── TextSource (now that ``Binding`` is defined) ────────────────────────────


@dataclass(frozen=True)
class Bound:
    """A ``TextSource.Bound`` — projects a ``Binding<string>`` as display text."""

    binding: Binding

    def to_wire(self) -> Value:
        return Obj("Bound", {"binding": _lower(self.binding)})


TextSource = LiteralText | Bound
"""The authoring ``TextSource`` surface (``Literal`` + ``Bound``)."""

TextInput = str | LiteralText | Bound
"""A ``TextSource``, or a bare ``str`` coerced to a :class:`LiteralText`."""


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
        # 0.2.0 — the `msg` closure sentinel is off the wire.
        return Obj("Dispatch", {})


@dataclass(frozen=True)
class Navigate:
    route: str

    def to_wire(self) -> Value:
        return Obj("Navigate", {"route": self.route})


@dataclass(frozen=True)
class SetState:
    """``Action.SetState`` — fuaran#818: ``value`` (a literal, written verbatim)
    XOR ``value_from`` (a Binding evaluated at dispatch time; ``valueFrom`` on
    the wire). A set ``value_from`` wins — the wire carries exactly one."""

    key: str
    value: Value = None
    value_from: Value | None = None

    def to_wire(self) -> Value:
        if self.value_from is not None:
            return Obj("SetState", {"key": self.key, "valueFrom": _lower(self.value_from)})
        return Obj("SetState", {"key": self.key, "value": _lower(self.value)})


@dataclass(frozen=True)
class Notify:
    channel: str
    payload: Value

    def to_wire(self) -> Value:
        return Obj("Notify", {"channel": self.channel, "payload": _lower(self.payload)})


@dataclass(frozen=True)
class WriteToClipboard:
    """``Action.WriteToClipboard`` — the payload is a ``TextSource`` (fuaran#1126).

    A bare string is still accepted and still encodes to the same bytes, because
    ``TextSource.Literal``'s canonical form IS the bare JSON string: the widening
    is source-breaking for construction sites and WIRE-NEUTRAL. What is new is
    that a BOUND payload can reach the clipboard — a figure in the grid in front
    of the reader, a link the session holds — and not only a literal the author
    typed at authoring time.

    Resolution happens at DISPATCH time on a host that has one, so what is
    copied is what the reader was looking at; resolving at decode time would
    freeze the value at the moment the document arrived, which for the shapes
    this widening exists for is the wrong value.
    """

    text: TextSource

    def to_wire(self) -> Value:
        # `_obj`, not a bare `Obj`: the payload is a typed `TextSource` now, so it
        # has to be LOWERED. A `Literal` lowers to the bare JSON string, which is
        # what keeps this arm's bytes identical to the ones it emitted before the
        # widening.
        return _obj("WriteToClipboard", {"text": self.text})


@dataclass(frozen=True)
class Print:
    """``Action.Print`` — open the reader's own print dialogue (fuaran#1124).

    The format's first PAYLOAD-FREE action case, and the emptiness is the
    specification rather than an omission in it: printing has parameters — page
    size, margins, orientation, sheet range, copies, which printer — and every
    one of them belongs either to the host's page setup or to the dialogue the
    reader is looking at when the action fires.

    It names no target either: it prints the PAGE, never a subtree of it,
    because a subtree is something the host already holds and can select for
    itself. Which subtrees stay whole on paper is a separate and independent
    statement (``Box.keep_together`` / ``.break_before`` and the grid's pair),
    because a printed page must be correct with no action having fired at all.
    """

    def to_wire(self) -> Value:
        return Obj("Print", {})


FileReadEncoding = Literal["Text", "Base64", "DataUrl"]


@dataclass(frozen=True)
class ReadFileBody:
    """``Action.ReadFileBody`` — reads a selected file's body; ``onRead`` is a closure."""

    file_ref: str
    encoding: FileReadEncoding = "Text"

    def to_wire(self) -> Value:
        return Obj("ReadFileBody", {"encoding": self.encoding, "fileRef": self.file_ref, "onRead": CLOSURE})


Action = Chain | Dispatch | Navigate | SetState | Notify | WriteToClipboard | Print | ReadFileBody


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


@dataclass(frozen=True)
class DurationFormat:
    """``CellFormat.Duration`` (Phase 819) — trendable duration cells: the raw
    float counts ``unit``s, rendered per ``style``."""

    unit: DurationUnit
    style: DurationStyle

    def to_wire(self) -> Value:
        return Obj("Duration", {"style": self.style, "unit": self.unit})


@dataclass(frozen=True)
class RelativeTimeFormat:
    """``CellFormat.RelativeTime`` (Phase 819) — cell-vocabulary parity with
    ``Format.RelativeTime`` (the English form is the canonical cell rendering)."""

    unit: RelativeTimeUnit

    def to_wire(self) -> Value:
        return Obj("RelativeTime", {"unit": self.unit})


CellFormat = (
    FormatNone
    | Currency
    | NumberFormat
    | PercentFormat
    | SignificantDigits
    | DateFormat
    | DurationFormat
    | RelativeTimeFormat
)


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
    #: fuaran#1472 — the DECLARED base direction of this node's own run, and the
    #: only member of this record that is not presentational. The others are
    #: statements a host may ignore and still render a document that says the
    #: same thing; this one is a CORRECTNESS statement, because a value declared
    #: `ltr` inside right-to-left prose is reordered by the Unicode
    #: bidirectional algorithm unless the run is isolated, and the reader then
    #: reads its digits back in the wrong order. `auto` is the identity.
    direction: TextDirection = "auto"

    def is_default(self) -> bool:
        return (
            self.emphasis == "Normal"
            and self.tone == "Default"
            and self.weight == "Standard"
            and (self.role is None or self.role == "None")
            and (self.voice is None or self.voice == "Default")
            and self.direction == "auto"
        )

    def to_wire(self) -> Value:
        # Phase 460 / Phase 147 — every field is omitted-when-default (WIRE_FORMAT §3.1 / §3.6).
        return _obj(
            None,
            {
                "direction": None if self.direction == "auto" else self.direction,
                "emphasis": None if self.emphasis == "Normal" else self.emphasis,
                "role": None if self.role == "None" else self.role,
                "tone": None if self.tone == "Default" else self.tone,
                "voice": None if self.voice == "Default" else self.voice,
                "weight": None if self.weight == "Standard" else self.weight,
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


# ── Typed Static payloads (WIRE_FORMAT.md §"Typed Static payloads", Phase 429) ─
#
# The language enumerates a handful of ``Binding.Static`` payload shapes — a
# Select/Filter/Choice options list, a Map marker list, a grid/chart row feed —
# that ride the wire as their *typed* form rather than the ``"<opaque>"``
# catch-all. Authoring a ``Static`` of one of these lowers structurally (via
# ``_lower``): a :class:`SelectOption` / :class:`MapMarker` carries a ``to_wire``
# so a bare ``Static([SelectOption(...)])`` serialises to the typed array the F#/TS
# tiers emit, and a row feed is a plain list of ``dict`` cells
# (``Static([{"month": "Jan", "revenue": 980}])``). A ``Static`` of a genuinely
# host-typed value (Mount inputs, ``PropValue.Native``) still lowers to the
# residual ``"<opaque>"`` seam — which fuaran#665 narrowed, for rows, to the CELL:
# a nested array/object cell is the sentinel, the row around it is not.


@dataclass(frozen=True)
class SelectOption:
    """A single option in a Select / Choice / Filter options list.

    ``label`` is a :class:`TextSource` (a bare ``str`` is coerced to
    :class:`LiteralText`); ``value`` is the option's string key.
    """

    label: TextSource
    value: str

    def __post_init__(self) -> None:
        if isinstance(self.label, str):
            object.__setattr__(self, "label", LiteralText(self.label))

    def to_wire(self) -> Value:
        return Obj(None, {"label": _lower(self.label), "value": self.value})


@dataclass(frozen=True)
class MapMarker:
    """A single marker in a Map ``source`` (a typed ``Static`` payload)."""

    label: TextSource
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if isinstance(self.label, str):
            object.__setattr__(self, "label", LiteralText(self.label))

    def to_wire(self) -> Value:
        return Obj(None, {"label": _lower(self.label), "latitude": self.latitude, "longitude": self.longitude})


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


# ── Box — the unified container primitive (Phase 390) ───────────────────────
#
# The four retired near-synonym containers (Stack / GridLayout / Dashboard /
# Card) collapse into one ``Box`` kind whose *layout mode* names how children
# arrange and whose *role* names what the container means (element + ARIA
# landmark + ``fuaran-*`` chrome). Mirrors the F# ``BoxSpec`` / ``BoxLayout`` /
# ``BoxRole``. The retired author-facing constructors (:func:`Stack` /
# :func:`GridLayout` / :func:`Dashboard` / :func:`Card`) survive as thin
# Box-emitting conveniences below.

BoxRole = Literal["Group", "Card", "Dashboard", "Separator"]
"""What a ``Box`` means — drives the emitted element, ARIA landmark, and chrome."""


@dataclass(frozen=True)
class FlexLayout:
    """Flex flow — the retired ``Stack``. ``$type`` = ``Flex``."""

    direction: Orientation = "Vertical"
    wrap: bool = False
    gap: int | None = None

    def to_wire(self) -> Value:
        # Ordinal key order (direction < gap < wrap); the canonical encoder
        # re-sorts, so ``_obj`` drops the ``None`` gap and the rest sort.
        return _obj("Flex", {"direction": self.direction, "gap": self.gap, "wrap": self.wrap})


@dataclass(frozen=True)
class GridTemplate:
    """Explicit grid — the retired ``GridLayout``. ``$type`` = ``Grid``."""

    cols: int = 12
    template_columns: str | None = None
    gap: int | None = None

    def to_wire(self) -> Value:
        # Ordinal key order (cols < gap < templateColumns); gap /
        # templateColumns omitted when ``None``.
        return _obj("Grid", {"cols": self.cols, "gap": self.gap, "templateColumns": self.template_columns})


@dataclass(frozen=True)
class MasonryLayout:
    """Column-fill masonry (WIRE_FORMAT §3.6.7). ``$type`` = ``Masonry``.

    ``Grid`` fills by ROW, ``Masonry`` fills by COLUMN, and no value of any
    ``Grid`` field changes that — which is why this is a fourth case rather than
    a fifth field on :class:`GridTemplate`. ``cols`` is required and POSITIVE;
    there is deliberately no ``templateColumns`` twin, because the multi-column
    model that realises the mode has no track list for one to name.
    """

    cols: int = 3
    gap: int | None = None

    def to_wire(self) -> Value:
        # Ordinal key order (cols < gap); gap omitted when ``None``.
        return _obj("Masonry", {"cols": self.cols, "gap": self.gap})


@dataclass(frozen=True)
class AutoLayout:
    """Responsive auto-tile — the retired ``Dashboard``. ``$type`` = ``Auto``."""

    def to_wire(self) -> Value:
        return Obj("Auto", {})


BoxLayout = FlexLayout | GridTemplate | MasonryLayout | AutoLayout
"""How a ``Box`` arranges its children (``Flex`` | ``Grid`` | ``Masonry`` | ``Auto``)."""


@dataclass(frozen=True)
class Box:
    """The unified container — lowers to ``{"$type":"Box",…}``.

    Ordinal key order children < heading < layout < role; ``heading`` emits
    only when set (the retired Card heading).
    """

    children: tuple[UiNode, ...] = ()
    layout: BoxLayout = field(default_factory=FlexLayout)
    role: BoxRole = "Group"
    heading: TextSource | None = None
    #: fuaran#1473 — this container and its whole subtree stay on ONE page when
    #: the rendering is PAGED. It declares the one fact a host cannot recover
    #: from a rendering: a formatter laying out pages sees boxes, and nothing in
    #: the rendering carries back that the three lines of a totals block are ONE
    #: THING that reads wrong when halved.
    keep_together: bool = False
    #: fuaran#1473 — this container starts at the top of a fresh page. There is
    #: deliberately no break-AFTER twin anywhere: a break after this container is
    #: a break before the next one.
    break_before: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Box",
            {
                "breakBefore": True if self.break_before else None,
                "children": list(self.children),
                "heading": self.heading,
                "keepTogether": True if self.keep_together else None,
                "layout": self.layout.to_wire(),
                "role": self.role,
            },
        )


def Dashboard(children: tuple[UiNode, ...] = (), heading: TextSource | None = None) -> Box:  # noqa: N802
    """Retired ``Dashboard`` — a ``Box`` with ``Auto`` layout + ``Dashboard`` role.

    ``heading`` is the same optional Box slot :func:`Card` fills; a titled dashboard
    is the ordinary shape and needed no new field, only a way to reach this one."""
    return Box(children=children, layout=AutoLayout(), role="Dashboard", heading=heading)


def Stack(  # noqa: N802
    children: tuple[UiNode, ...] = (), orientation: Orientation = "Vertical", wrap: bool = False
) -> Box:
    """Retired ``Stack`` — a ``Box`` with ``Flex`` layout + ``Group`` role."""
    return Box(children=children, layout=FlexLayout(direction=orientation, wrap=wrap), role="Group")


def GridLayout(  # noqa: N802
    children: tuple[UiNode, ...] = (), cols: int = 12, template_columns: str | None = None
) -> Box:
    """Retired ``GridLayout`` — a ``Box`` with ``Grid`` layout + ``Group`` role."""
    return Box(children=children, layout=GridTemplate(cols=cols, template_columns=template_columns), role="Group")


def MasonryLayoutBox(  # noqa: N802
    children: tuple[UiNode, ...] = (), cols: int = 3, gap: int | None = None
) -> Box:
    """A ``Box`` with ``Masonry`` layout + ``Group`` role (WIRE_FORMAT §3.6.7).

    The default column count is 3 rather than :func:`GridLayout`'s 12: a masonry
    column is a real column of content, not a track in a fine-grained span grid.
    """
    return Box(children=children, layout=MasonryLayout(cols=cols, gap=gap), role="Group")


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
                # 0.2.0 — omitted-when-Horizontal (the universal default).
                "orientation": None if self.orientation == "Horizontal" else self.orientation,
                "tabHeaders": list(self.tab_headers) if self.tab_headers is not None else None,
                "tabTags": list(self.tab_tags) if self.tab_tags is not None else None,
            },
        )


def Card(children: tuple[UiNode, ...] = (), heading: TextSource | None = None) -> Box:  # noqa: N802
    """Retired ``Card`` — a ``Box`` with ``Flex{Vertical,false}`` layout + ``Card`` role + heading."""
    return Box(children=children, layout=FlexLayout(direction="Vertical", wrap=False), role="Card", heading=heading)


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


@dataclass(frozen=True)
class Modal:
    children: tuple[UiNode, ...] = ()
    open: Binding = field(default_factory=lambda: Static(False))
    dismissable: bool = False
    on_dismiss: Action = field(default_factory=Chain)
    heading: TextSource | None = None
    #: fuaran#1119 — `Blocking` is the identity and omits at it, so every modal
    #: written before this member is byte-identical. The two differ in ONE claim
    #: a host makes: a blocking surface asserts the page behind it is inert, an
    #: anchored one does not, because the page behind it genuinely is not.
    modality: ModalityKind = "Blocking"
    #: fuaran#1119 — the node the anchored surface is positioned against. Where
    #: it sits, which way it flips at a viewport edge and how far off it stands
    #: are the RENDERER's, not the document's.
    anchor: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Modal",
            {
                "anchor": self.anchor,
                "children": list(self.children),
                "dismissable": self.dismissable,
                "heading": self.heading,
                "modality": None if self.modality == "Blocking" else self.modality,
                "onDismiss": self.on_dismiss,
                "open": self.open,
            },
        )


@dataclass(frozen=True)
class ScrollArea:
    children: tuple[UiNode, ...] = ()
    orientation: ScrollOrientation = "Vertical"
    max_height: int | None = None
    max_width: int | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "ScrollArea",
            {
                "children": list(self.children),
                "maxHeight": self.max_height,
                "maxWidth": self.max_width,
                "orientation": self.orientation,
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
    value: Binding
    format: CellFormat = field(default_factory=FormatNone)
    tone: Tone = "Default"
    weight: Weight = "Standard"
    emphasis: Emphasis = "Normal"
    icon: str | None = None
    subtext: TextSource | None = None
    trend: Binding | None = None
    trend_format: CellFormat | None = None
    trend_polarity: TrendPolarity = "HigherIsBetter"

    def to_wire(self) -> Obj:
        # Phase 460 — the stylistic fields are omitted-when-default (WIRE_FORMAT §3.6).
        return _obj(
            "Metric",
            {
                "emphasis": None if self.emphasis == "Normal" else self.emphasis,
                "format": None if isinstance(self.format, FormatNone) else self.format,
                "icon": self.icon,
                "label": self.label,
                "subtext": self.subtext,
                "tone": None if self.tone == "Default" else self.tone,
                "trend": self.trend,
                "trendFormat": self.trend_format,
                # fuaran#867 — omitted-when-`HigherIsBetter`, so every Metric
                # authored before the slot existed encodes byte-identically.
                "trendPolarity": None if self.trend_polarity == "HigherIsBetter" else self.trend_polarity,
                "value": self.value,
                "weight": None if self.weight == "Standard" else self.weight,
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
                # 0.2.0 — omitted-when-false.
                "dismissable": True if self.dismissable else None,
                "heading": self.heading,
                "icon": self.icon,
                "tone": None if self.tone == "Default" else self.tone,
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
                # 0.2.0 — omitted-when-false.
                "indeterminate": True if self.indeterminate else None,
                "label": self.label,
                "tone": None if self.tone == "Default" else self.tone,
            },
        )


@dataclass(frozen=True)
class Skeleton:
    rows: int

    def to_wire(self) -> Obj:
        return _obj("Skeleton", {"rows": self.rows})


@dataclass(frozen=True)
class Icon:
    """Phase 821 — the standalone icon-only display kind: a decorative or
    labelled glyph with no Button / Image envelope. ``icon`` names a glyph from
    the existing icon vocabulary (the ``data-icon`` hook); ``label`` absent is
    decorative (``aria-hidden``), present is meaningful (``role="img"``)."""

    icon: str
    size: IconSize = "Medium"
    tone: Tone = "Default"
    label: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Icon",
            {
                "icon": self.icon,
                "label": self.label,
                # `size` omitted-when-`Medium`, `tone` omitted-when-`Default`
                # (the Phase 460 discipline).
                "size": None if self.size == "Medium" else self.size,
                "tone": None if self.tone == "Default" else self.tone,
            },
        )


@dataclass(frozen=True)
class LabelValueRow:
    label: TextSource
    value: Binding
    format: CellFormat = field(default_factory=FormatNone)
    emphasis: bool = False
    help: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "LabelValueRow",
            {
                # `emphasis` is the behavioural bool — 0.2.2: omitted-when-false;
                # `format` is omitted-when-default (Phase 460).
                "emphasis": True if self.emphasis else None,
                "format": None if isinstance(self.format, FormatNone) else self.format,
                "help": self.help,
                "label": self.label,
                "value": self.value,
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


@dataclass(frozen=True)
class SrcSetEntry:
    """fuaran#1080 — one responsive candidate: an alternate rendition of the SAME
    picture at a declared intrinsic pixel width, from which a client picks one.

    ``width`` is the ``w`` descriptor and MUST be a positive integer. Widths
    rather than device-pixel-ratio descriptors or per-entry media conditions:
    those are alternative candidate-selection algebras, and a list mixing them is
    one a browser refuses outright.
    """

    src: Binding
    width: int

    def to_wire(self) -> Obj:
        return _obj(None, {"src": self.src, "width": self.width})


@dataclass(frozen=True)
class Image:
    alt: TextSource
    src: Binding
    variant: ImageVariant = "Default"
    # fuaran#1077 — omitted at their identity defaults on both boundaries, so a
    # default-presentation image emits exactly the pre-phase three fields.
    fit: ImageFit = "Natural"
    aspect_ratio: ImageAspect = "Natural"
    loading: ImageLoading = "Eager"
    # fuaran#1078 — CONTENT, not an identity default: absent means absent. A full
    # `TextSource`, so a caption is i18n-capable on exactly the terms `alt` is.
    caption: TextSource | None = None
    # fuaran#1080 — the EMPTY tuple is the identity: an image with no alternate
    # renditions and one with an empty list are the same document, so both emit
    # no key. Authored ORDER is preserved verbatim on the wire; ascending-by-width
    # is the renderer's canonicalisation, not the codec's.
    src_set: tuple[SrcSetEntry, ...] = ()
    # fuaran#1079 — the only slot on the record that declares an INTERACTION
    # rather than a picture. It says the full-size asset is REACHABLE from the
    # rendered image, not that a lightbox appears.
    expandable: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Image",
            {
                "alt": self.alt,
                "aspectRatio": None if self.aspect_ratio == "Natural" else self.aspect_ratio,
                "caption": self.caption,
                "expandable": True if self.expandable else None,
                "fit": None if self.fit == "Natural" else self.fit,
                "loading": None if self.loading == "Eager" else self.loading,
                "src": self.src,
                "srcSet": list(self.src_set) if self.src_set else None,
                "variant": self.variant,
            },
        )


# ── Media (WIRE_FORMAT.md §3.6.6) ───────────────────────────────────────────
#
# ONE kind, two variants — never two kinds. Everything a video surface and an
# audio surface share is stated once on :class:`Media`; only the slots that
# genuinely differ live in the variant, and there are two of them, both on
# :class:`Video`.


@dataclass(frozen=True)
class Video:
    """The video variant. ``autoplay`` is a DECLARATION whose rendering is
    constrained: a host that honours it MUST emit it together with a muted
    attribute, which is why there is deliberately no separate ``muted`` slot for a
    second knob to disagree with."""

    autoplay: bool = False
    poster: Binding | None = None

    def to_wire(self) -> Obj:
        return _obj("Video", {"autoplay": True if self.autoplay else None, "poster": self.poster})


@dataclass(frozen=True)
class Audio:
    """The audio variant, whose payload is the discriminator alone.

    It declares NO autoplay slot, and that is stronger than a default of
    ``False``: a slot that defaults to off is one a document can switch on, and
    there is no document this format wants to be able to state in which a page
    begins making sound unbidden.
    """

    def to_wire(self) -> Obj:
        return _obj("Audio", {})


MediaKind = Video | Audio

#: fuaran#1110 — the closed track vocabulary (WIRE_FORMAT §3.6.6). ``Metadata``
#: is deliberately absent: its cues are rendered by no user agent and read only
#: by script, so a declarative document naming it would state an intent no
#: conformant host could honour without leaving the vocabulary.
TrackKind = Literal["Subtitles", "Captions", "Descriptions", "Chapters"]

#: fuaran#1111 — the sandbox relaxations an `Embed` may request. The EMPTY list
#: is total denial and is the identity, so the shortest embed document is the
#: fully-sandboxed one and every relaxation is something a caller names.
EmbedPermission = Literal["AllowScripts", "AllowSameOrigin", "AllowForms", "AllowFullscreen"]

#: fuaran#1116 — the recording device a `FileUpload` asks the platform to open.
#: There is no display-capture case and there will not be one by widening this:
#: a screen capture reaches every window the reader has open rather than one
#: device behind the picker, so it is a different class of thing.
CaptureSource = Literal["Camera", "Microphone"]

#: fuaran#1119 — the modal's modality. The two differ in ONE claim: a blocking
#: surface asserts the page behind it is inert; an anchored one does not.
ModalityKind = Literal["Blocking", "Popover"]

#: fuaran#1472 — the declared base direction, LOWER-CASE because that is the
#: spelling the isolation is ultimately expressed in. `auto` is the identity.
TextDirection = Literal["auto", "ltr", "rtl"]


@dataclass(frozen=True)
class Embed:
    """fuaran#1111 — a third-party document rendered inside a maximally-sandboxed
    browsing context.

    A NEW kind rather than a `Mount` variant: `Mount` composes a COOPERATING
    guest — a scope id, a declared message channel, a capability request list, a
    host-side loader — and a third-party page has none of those, so widening
    `Mount` to admit an uncooperative third party would weaken every guarantee it
    makes. It is equally not a `Media` variant: `Media` fetches an asset and
    DISPLAYS it, this fetches a document and lets it EXECUTE.
    """

    src: Binding
    #: The frame's accessible name, emitted as `title`. Mandatory, on
    #: `Media.label`'s argument one kind over: a frame is a focus container a
    #: reader tabs INTO, so it is never decorative, and an unnamed one is
    #: announced as "frame" and nothing more.
    title: TextSource
    #: The box the frame reserves. REUSES the image aspect vocabulary rather than
    #: a parallel enum with identical cases — a ratio is a ratio, and the wire
    #: carries bare strings, so the type name reaches no document.
    aspect_ratio: ImageAspect = "Natural"
    #: The relaxations, in the ORDER the document names them. A JSON array is
    #: ordered data and this record carries it verbatim; emitting the tokens in a
    #: canonical order is the RENDERER's obligation, not the codec's.
    permissions: tuple[EmbedPermission, ...] = ()

    def to_wire(self) -> Obj:
        return _obj(
            "Embed",
            {
                "aspectRatio": None if self.aspect_ratio == "Natural" else self.aspect_ratio,
                "permissions": list(self.permissions) if self.permissions else None,
                "src": self.src,
                "title": self.title,
            },
        )


@dataclass(frozen=True)
class TreeItem:
    """fuaran#1120 — one row of a `Tree`, and the format's first SELF-REFERENTIAL
    shape: `children` is a list of the same record.

    `children` omits at the EMPTY LIST and `icon` when absent, so a leaf carries
    two keys and nothing else — which is most of a real hierarchy, and a host
    emitting `"children":[]` on a leaf produces different bytes for most of a
    file listing.

    `id` is required because it is what the two State slots NAME. `label` is a
    `TextSource` because it is content — authored, translated, bindable. Row ids
    must be unique within one tree, but that is an EMIT-side obligation rather
    than a decode refusal: duplicate detection is a whole-tree property, and a
    repeat makes both State slots ambiguous.
    """

    id: str
    label: TextSource
    children: tuple[TreeItem, ...] = ()
    icon: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            None,
            {
                "children": list(self.children) if self.children else None,
                "icon": self.icon,
                "id": self.id,
                "label": self.label,
            },
        )


@dataclass(frozen=True)
class Tree:
    """fuaran#1120 — a hierarchy of ROWS and, optionally, the names of the two
    State slots through which a reader opens rows and selects one.

    This kind carries NO `expandable` and NO `selectable` boolean, and none is
    coming: a behaviour the reader drives is declared as a named State key the
    host both writes and reads, and a flag with no key behind it is a decorative
    control writing state nothing reads.

    A tree naming no `expanded_state_key` renders FULLY EXPANDED — the same
    reading that lets a grid honour a declared initial order while offering no
    interactive sorting, and the only reading under which such a tree shows its
    content at all. A tree naming no `selection_state_key` does not select, and
    emits no `aria-selected`.
    """

    items: tuple[TreeItem, ...] = ()
    #: Names a State slot holding a JSON ARRAY OF ROW IDS. An array rather than a
    #: map of booleans, because the question a host asks is set membership, and a
    #: set has one spelling where a map has two for "closed".
    expanded_state_key: str | None = None
    #: Names a State slot holding a bare ROW-ID STRING.
    selection_state_key: str | None = None
    #: Emitted only when present (rule 4); the value is the closure sentinel.
    on_select: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Tree",
            {
                "expandedStateKey": self.expanded_state_key,
                "items": list(self.items),
                "onSelect": CLOSURE if self.on_select else None,
                "selectionStateKey": self.selection_state_key,
            },
        )


@dataclass(frozen=True)
class TrackEntry:
    """fuaran#1110 — one timed-text track, and the strictest record on the wire:
    four of its five members are required.

    ``src_lang`` is required on EVERY kind, where HTML makes ``srclang``
    mandatory only on a subtitles track — the extra strictness costs an author
    one value and buys a menu a user agent can order, a speech engine can
    pronounce and a reader can tell apart. ``label`` is required because it is
    the entry the track menu shows and the only thing telling one track from
    another there. ``default`` is the one omitted-at-``False`` slot; at most one
    track per KIND may carry it at render time, and a later election is emitted
    without the attribute rather than dropped.
    """

    kind: TrackKind
    label: TextSource
    src: Binding
    src_lang: str
    default: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            None,
            {
                "default": True if self.default else None,
                "kind": self.kind,
                "label": self.label,
                "src": self.src,
                "srcLang": self.src_lang,
            },
        )


@dataclass(frozen=True)
class Media:
    """A playback surface. ``label`` is REQUIRED — the one place the media
    contract differs from ``Image``'s: an image can honestly be decorative and say
    so with an empty ``alt``, but a media element is a TRANSPORT and is never
    decorative, and there is no value to default to that would not be a fabricated
    name for someone else's recording.

    ``controls`` is omitted at TRUE (the second such slot after
    ``Toast.dismissable``): a media element without a transport cannot be paused,
    seeked or muted by a keyboard user at all, so the accessible setting is what a
    document gets for free and taking it away is the deviation that costs a key.
    """

    src: Binding
    label: TextSource
    kind: MediaKind = field(default_factory=Video)
    controls: bool = True
    loop: bool = False
    # fuaran#1110 — the EMPTY tuple is the identity, exactly as `Image.src_set`'s
    # is: a transport with no tracks and one with an empty list are the same
    # document, so both emit no key. Authored ORDER is carried verbatim, and here
    # the RENDERER keeps it too — a reader picks from a menu built in document
    # order, so sorting it would be rewriting someone else's menu.
    tracks: tuple[TrackEntry, ...] = ()
    # fuaran#1110 — an ordinary optional rather than an identity default: absent
    # means the document offers no transcript, which is a different statement
    # from offering an empty one. It lives on the SPEC and not on `Video`
    # because it is the affordance an AUDIO surface needs most.
    transcript: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Media",
            {
                "controls": None if self.controls else False,
                "kind": self.kind,
                "label": self.label,
                "loop": True if self.loop else None,
                "src": self.src,
                "tracks": list(self.tracks) if self.tracks else None,
                "transcript": self.transcript,
            },
        )


@dataclass(frozen=True)
class List:
    items: tuple[TextSource, ...] = ()
    ordered: bool = False

    def to_wire(self) -> Obj:
        return _obj("List", {"items": list(self.items), "ordered": self.ordered})


@dataclass(frozen=True)
class Toast:
    message: TextSource
    open: Binding = field(default_factory=lambda: Static(False))
    tone: Tone = "Default"
    dismissable: bool = True

    def to_wire(self) -> Obj:
        return _obj(
            "Toast",
            {
                # 0.2.0 — the one omit-when-TRUE (a toast defaults dismissable).
                "dismissable": None if self.dismissable else False,
                "message": self.message,
                "open": self.open,
                "tone": None if self.tone == "Default" else self.tone,
            },
        )


@dataclass(frozen=True)
class CodeBlock:
    code: str
    language: str
    copyable: bool = False
    line_numbers: bool = False
    highlight_lines: tuple[int, ...] = ()

    def to_wire(self) -> Obj:
        return _obj(
            "CodeBlock",
            {
                "code": self.code,
                "copyable": self.copyable,
                "highlightLines": list(self.highlight_lines),
                "language": self.language,
                "lineNumbers": self.line_numbers,
            },
        )


@dataclass(frozen=True)
class Math:
    source: str
    display: MathDisplay = "Block"

    def to_wire(self) -> Obj:
        return _obj("Math", {"display": self.display, "source": self.source})


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
    # Multi-select (Phase 291): ``multiple`` is emitted only when ``True`` (a
    # single-select stays byte-identical to the pre-multi corpus); ``values``
    # is the ``Binding<string list>`` of selected option values, emitted only
    # when present. The multi onChange is a closure → no separate wire key.
    multiple: bool = False
    values: Binding | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Select",
            {
                "disabled": self.disabled,
                "label": self.label,
                "multiple": self.multiple if self.multiple else None,
                "onChange": CLOSURE,
                "placeholder": self.placeholder,
                "source": self.source,
                "value": self.value,
                "values": self.values,
            },
        )


@dataclass(frozen=True)
class FileUpload:
    label: TextSource
    accept: tuple[str, ...] = ()
    multiple: bool = False
    disabled: Binding | None = None
    #: fuaran#1115 — the control renders a DROP ZONE. This names an INGRESS
    #: ROUTE, not a gesture: the drag-over, the drop and the visible drop state
    #: are the renderer's affordance, and a dropped file resolves through the
    #: same selection path a picked one does. The zone is ADDITIONAL — the picker
    #: and its label are always emitted, because there is no keyboard equivalent
    #: of a drag.
    drop_target: bool = False
    #: fuaran#1115 — a paste carrying files, on the focused control, resolves
    #: through the same selection path. It admits no clipboard-READING
    #: capability: what a host attaches is a `paste` listener, which fires only
    #: on the reader's own paste and carries only what the reader pasted.
    accept_paste: bool = False
    #: fuaran#1116 — WHICH of the reader's own recording devices the platform
    #: opens in place of the file browser. OPTIONAL rather than
    #: omit-at-default: "say nothing" is a state of its own, because an upload
    #: naming no device asks for the ordinary picker, which is not one of the two
    #: devices wearing a default.
    capture: CaptureSource | None = None
    #: fuaran#1117 — the host-registered destination selected files stream to. A
    #: NAME and never an ADDRESS: it is an id the host has registered with its
    #: own upload sink, and nothing on this member is ever fetched, joined to a
    #: base, or otherwise turned into a URL. A wire document comes from an
    #: arbitrary emitter, and a URL here would let that emitter choose where a
    #: reader's file goes.
    destination: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "FileUpload",
            {
                "accept": list(self.accept),
                "acceptPaste": True if self.accept_paste else None,
                "capture": self.capture,
                "destination": self.destination,
                "disabled": self.disabled,
                "dropTarget": True if self.drop_target else None,
                "label": self.label,
                "multiple": self.multiple,
                "onSelect": CLOSURE,
            },
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
    """Author-facing carrier for a static read-only table.

    Phase 393 — no longer a `VisKind` case of its own: `to_wire` lowers it into the
    `staticRows` mode of `DataGrid` (one tabular kind). The empty `Static` source
    re-encodes to ``{"$type":"Static","value":[]}`` under the fuaran#665 typed
    row-source encoding — byte-identical to the F#/TS static grid.
    """

    headers: tuple[TextSource, ...]
    rows: tuple[tuple[TextSource, ...], ...]

    def to_wire(self) -> Obj:
        return _obj(
            "DataGrid",
            {
                "columns": [],
                "source": Static(Arr([])),
                "staticRows": {
                    "headers": list(self.headers),
                    "rows": [list(r) for r in self.rows],
                },
            },
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


# Input — composite (Form / Filters) ------------------------------------------
#
# ``onChange`` / ``onToggle`` handlers are closures → the ``CLOSURE`` sentinel.


@dataclass(frozen=True)
class TextField:
    value: Binding

    def to_wire(self) -> Value:
        return Obj("Text", {"onChange": CLOSURE, "value": _lower(self.value)})


@dataclass(frozen=True)
class NumberField:
    value: Binding

    def to_wire(self) -> Value:
        return Obj("Number", {"onChange": CLOSURE, "value": _lower(self.value)})


@dataclass(frozen=True)
class CheckboxField:
    value: Binding

    def to_wire(self) -> Value:
        return Obj("Checkbox", {"onToggle": CLOSURE, "value": _lower(self.value)})


@dataclass(frozen=True)
class ToggleField:
    """``FormFieldKind.Toggle`` (Phase 766) — the switch-styled boolean control:
    ``Checkbox``'s bool mechanics under a distinct tag-only discriminator.

    Mirrors the F# shape where BOTH slots are optional: an absent ``value``
    auto-binds to the field's state key at run time (the canonical minimal
    control is the bare ``{"$type":"Toggle"}``), and an absent handler arms the
    write-back default. ``on_toggle=True`` marks a host handler as present — a
    closure, emitted as the sentinel.
    """

    value: Binding | None = None
    on_toggle: bool = False

    def to_wire(self) -> Value:
        return _obj("Toggle", {"onToggle": CLOSURE if self.on_toggle else None, "value": self.value})


@dataclass(frozen=True)
class TextAreaField:
    value: Binding
    rows: int

    def to_wire(self) -> Value:
        return Obj("TextArea", {"onChange": CLOSURE, "rows": self.rows, "value": _lower(self.value)})


@dataclass(frozen=True)
class RangedNumber:
    value: Binding
    min: float | None = None
    max: float | None = None
    step: float | None = None

    def to_wire(self) -> Value:
        return _obj(
            "RangedNumber",
            {"max": self.max, "min": self.min, "onChange": CLOSURE, "step": self.step, "value": self.value},
        )


@dataclass(frozen=True)
class DateField:
    value: Binding
    variant: DateVariant = "Date"
    min: str | None = None
    max: str | None = None
    step: float | None = None

    def to_wire(self) -> Value:
        return _obj(
            "Date",
            {
                "max": self.max,
                "min": self.min,
                "onChange": CLOSURE,
                "step": self.step,
                "value": self.value,
                "variant": self.variant,
            },
        )


@dataclass(frozen=True)
class DateRangeField:
    """``FormFieldKind.DateRange`` (0.7.0) — the single-control date range.

    ``Range``'s pair mechanics with ``Date``'s value conventions: the bound value
    is the ordered ``(from, to)`` pair, each end an ISO-8601 string in the
    ``variant``'s shape. A literal pair rides the wire as the BARE
    ``{"from":…,"to":…}`` object (no ``Static`` envelope — the ``Range``
    posture) and must satisfy ``from <= to``; ``min`` / ``max`` (ISO strings) and
    ``step`` (seconds) bound BOTH ends.
    """

    value: Binding | tuple[str, str]
    variant: DateVariant = "Date"
    min: str | None = None
    max: str | None = None
    step: float | None = None

    def to_wire(self) -> Value:
        pair = self.value
        lowered: object = Obj(None, {"from": pair[0], "to": pair[1]}) if isinstance(pair, tuple) else _lower(pair)
        return _obj(
            "DateRange",
            {
                "max": self.max,
                "min": self.min,
                "onChange": CLOSURE,
                "step": self.step,
                "value": lowered,
                "variant": self.variant,
            },
        )


@dataclass(frozen=True)
class ChoiceField:
    options: Binding
    value: Binding

    def to_wire(self) -> Value:
        return Obj("Choice", {"onChange": CLOSURE, "options": _lower(self.options), "value": _lower(self.value)})


@dataclass(frozen=True)
class SegmentedChoice:
    options: Binding
    value: Binding
    orientation: Orientation = "Horizontal"

    def to_wire(self) -> Value:
        return Obj(
            "SegmentedChoice",
            {
                "onChange": CLOSURE,
                "options": _lower(self.options),
                "orientation": self.orientation,
                "value": _lower(self.value),
            },
        )


@dataclass(frozen=True)
class ComboboxField:
    """fuaran#1113 — the typeahead / autocomplete control.

    `Choice` is a bounded menu the reader scans; this is a searchable one it
    FILTERS, which is what makes a two-hundred-option source usable rather than
    merely valid. The option source is an ordinary binding, so a `Query`-bound
    one IS the asynchronous suggestion feed.

    `allow_free_text` omits at `False`, which makes the shortest document the
    CONSTRAINED one — the opposite polarity to `Tokens`, because that case's
    suggestion source is optional and this one's is required.
    """

    options: Binding
    value: Binding
    allow_free_text: bool = False

    def to_wire(self) -> Value:
        return _obj(
            "Combobox",
            {
                "allowFreeText": True if self.allow_free_text else None,
                "onChange": CLOSURE,
                "options": _lower(self.options),
                "value": _lower(self.value),
            },
        )


@dataclass(frozen=True)
class TokensField:
    """fuaran#1121 — SEVERAL values accumulated as removable chips, over a
    suggestion set that may be open, searchable, asynchronous, or absent entirely.

    THE TRIANGLE, which is the line an emitter has to hold: a CLOSED set small
    enough to scan is a `Select` with `multiple`; ONE value from a large or
    asynchronous set is a `Combobox`; SEVERAL values over a set that is open, or
    that the document does not enumerate at all, is this. A `Combobox` PER ITEM
    is not a smaller version of this control — it is N single-value fields with N
    ids, no gesture that removes the third entry, and a submission shaped like
    `tag1`, `tag2`, `tag3` rather than one list.

    The value list is ORDERED and the order is the READER'S: chips appear where
    they were added, and a host must not sort or de-duplicate it.

    `allow_free_text` omits at TRUE here — the OPPOSITE polarity to `Combobox`'s,
    and the thing about this case a host is most likely to get wrong. The default
    follows the required-ness of the SET, which is one rule rather than two
    habits: `Combobox.options` is required so "constrained" is its resting state,
    where `suggestions` is optional so "open" is this one's.
    """

    value: Binding
    allow_free_text: bool = True
    #: An ABSENT source and an EMPTY one are different facts: absent means the
    #: control has no candidate set at all, resolved-empty means it has one that
    #: is currently empty — which is also every asynchronous source's first frame.
    suggestions: Binding | None = None

    def to_wire(self) -> Value:
        return _obj(
            "Tokens",
            {
                "allowFreeText": None if self.allow_free_text else False,
                "onChange": CLOSURE,
                "suggestions": None if self.suggestions is None else _lower(self.suggestions),
                "value": _lower(self.value),
            },
        )


@dataclass(frozen=True)
class RatingField:
    """fuaran#1130 — a SUBJECTIVE SCORE on a small ordinal scale.

    The line an emitter has to hold is one sentence: a rating is a judgement a
    person GIVES, a `RangedNumber` is a measurement they REPORT. Both carry a
    floating-point value and a ceiling, which is exactly why the sentence is
    written down rather than left to be inferred from the shapes.

    `max` is the case's only REQUIRED member and must be at least 1: a scale with
    no positions has nothing to draw, nothing to announce and no keystroke that
    could change anything.

    The value is a FLOAT even where nothing can type a fraction, and that is
    normative: the commonest rating a reader sees is an AVERAGE arriving through
    a `Query` binding, and an integer slot could not carry it.

    `allow_half` governs ENTRY, never DISPLAY — it is the granularity of a
    keystroke and of a pointer commit, and a host must not quantise a resolved
    value to it.
    """

    max: int
    value: Binding
    allow_half: bool = False

    def to_wire(self) -> Value:
        return _obj(
            "Rating",
            {
                "allowHalf": True if self.allow_half else None,
                "max": self.max,
                "onChange": CLOSURE,
                "value": _lower(self.value),
            },
        )


@dataclass(frozen=True)
class ColorField:
    """fuaran#1130 — the platform's own colour picker.

    Note what it is NOT: a CONTROL, and not a `rule.format`. A format constrains
    the text a reader types into a text box, where this is a swatch that opens the
    operating system's colour picker, which no format on a `Text` field can
    produce.

    The value is `#rrggbb` and nothing else — six hexadecimal digits after a `#`,
    either case. That is the one form a native colour input can hold or return, so
    it is the wire form too rather than a wider colour syntax the control would
    silently narrow. CASE IS PRESERVED, never normalised: browsers normalise at
    the DOM, which is their business and not the wire's.
    """

    value: Binding

    def to_wire(self) -> Value:
        return _obj("Color", {"onChange": CLOSURE, "value": _lower(self.value)})


FormFieldKind = (
    TextField
    | NumberField
    | CheckboxField
    | ToggleField
    | TextAreaField
    | RangedNumber
    | DateField
    | DateRangeField
    | ChoiceField
    | SegmentedChoice
    | ComboboxField
    | TokensField
    | RatingField
    | ColorField
)


# ── FieldRule (fuaran#864, WIRE_FORMAT §3.6) ────────────────────────────────
# A field's declared CONSTRAINT — the accepted set, where `FormFieldKind` names
# the CONTROL. An optional record field rather than a discriminator case, so a
# form authored before it encodes byte-identically.

CompareOp = Literal["eq", "neq", "lt", "lte", "gt", "gte"]

#: The `format` shorthands a text control can honour.
RuleFormat = Literal["email", "url", "tel"]


@dataclass(frozen=True)
class CompareRule:
    """``FieldRule.compare`` — this field's value against another operand.

    Both slots are required on the wire. Point ``against`` at a sibling field's
    id via ``State`` (a form field's value lives in State under its own id) to
    express "end date on or after start date"; a ``Static`` operand is a literal
    bound and is what FUARAN101 measures against the control's own min/max.
    """

    against: Binding
    op: CompareOp

    def to_wire(self) -> Value:
        return _obj(None, {"against": self.against, "op": self.op})


@dataclass(frozen=True)
class FieldRule:
    """A ``FormField``'s declared constraint. Every slot is optional, but a rule
    with NO constraint slot at all is a decode error, not a no-op — ``message``
    alone does not rescue it, since a message is the prose shown when some
    *other* slot is unmet.

    ``pattern`` carries ECMA-262 source with HTML ``pattern`` semantics —
    implicitly anchored to the whole value — so a browser, a static projection
    and a native surface agree without a second definition.
    """

    compare: CompareRule | None = None
    format: RuleFormat | None = None
    max_length: int | None = None
    message: TextSource | None = None
    min_length: int | None = None
    pattern: str | None = None

    def to_wire(self) -> Value:
        return _obj(
            None,
            {
                "compare": self.compare,
                "format": self.format,
                "maxLength": self.max_length,
                "message": self.message,
                "minLength": self.min_length,
                "pattern": self.pattern,
            },
        )


@dataclass(frozen=True)
class FormField:
    id: str
    label: TextSource
    kind: FormFieldKind
    required: bool = False
    help: TextSource | None = None
    # fuaran#864 — omitted when absent, so every pre-864 field is byte-unchanged.
    rule: FieldRule | None = None

    def to_wire(self) -> Value:
        return _obj(
            None,
            {
                "help": self.help,
                "id": self.id,
                "kind": self.kind,
                "label": self.label,
                "required": self.required,
                "rule": self.rule,
            },
        )


@dataclass(frozen=True)
class Form:
    fields: tuple[FormField, ...] = ()
    on_submit: Action = field(default_factory=Chain)
    submit_label: TextSource = field(default_factory=lambda: LiteralText("Submit"))
    disabled: Binding | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Form",
            {
                "disabled": self.disabled,
                "fields": list(self.fields),
                "onSubmit": self.on_submit,
                "submitLabel": self.submit_label,
            },
        )


# 0.2.0 filters-unification — the `FilterKind` family is retired: a filter
# chip's control is an ordinary `FormFieldKind` (`Text` / `Choice` /
# `SegmentedChoice` on the wire). The old names survive as thin aliases of the
# form-control classes so existing authoring code keeps working while emitting
# the unified wire.
TextFilter = TextField
ChoiceFilter = ChoiceField
SegmentedFilter = SegmentedChoice

FilterKind = FormFieldKind


@dataclass(frozen=True)
class FilterSpec:
    name: str
    label: TextSource
    kind: FilterKind

    def to_wire(self) -> Value:
        return Obj(None, {"kind": _lower(self.kind), "label": _lower(self.label), "name": self.name})


@dataclass(frozen=True)
class Filters:
    items: tuple[FilterSpec, ...] = ()

    def to_wire(self) -> Obj:
        return _obj("Filters", {"items": list(self.items)})


# Visualisation — DataGrid (row-typed columns erase to closures) --------------


@dataclass(frozen=True)
class ColumnWidth:
    kind: str = "Auto"

    def to_wire(self) -> Value:
        return Obj(self.kind, {})


@dataclass(frozen=True)
class ColumnKind:
    kind: str = "Text"

    def to_wire(self) -> Value:
        return Obj(self.kind, {})


@dataclass(frozen=True)
class TonedPillColumnKind:
    """A value-conditional pill: the one cell kind holding no closure, which is exactly
    why it survives the wire (Phase 750).

    ``field`` names the row property that is both the pill's label and the map key;
    ``map`` carries value → tone; ``default`` tones a value the map does not mention and
    is omitted at ``Default`` (Phase 460). Every other cell kind's appearance rule is a
    ``(row) -> …`` closure that erases to ``"<closure>"``, so it can be authored here but
    never expressed on the wire — this one can.
    """

    field: str
    map: dict[str, Tone]
    default: Tone = "Default"

    def to_wire(self) -> Value:
        return _obj(
            "TonedPill",
            {
                "default": None if self.default == "Default" else self.default,
                "field": self.field,
                "map": dict(self.map),
            },
        )


AnyColumnKind = ColumnKind | TonedPillColumnKind


@dataclass(frozen=True)
class Column:
    """A DataGrid column.

    ``value`` (the closure projection) and ``field`` (the declarative one) are the
    wire's two sibling optional slots for "which cell does this column show"
    (``schema/decode.py::_decode_column``). A closure cannot survive the wire, so it
    erases to ``"<closure>"``; a ``field`` names a row property and survives intact,
    which is the only spelling a *decoded* grid can actually project from. Naming a
    ``field`` therefore emits ``field`` and omits the erased ``value`` — the two are
    alternatives, not a pair (corpus: ``nodes/grid-field-named.json`` vs
    ``nodes/grid-1.json``).
    """

    label: str
    format: CellFormat = field(default_factory=FormatNone)
    kind: AnyColumnKind = field(default_factory=ColumnKind)
    width: ColumnWidth = field(default_factory=ColumnWidth)
    field_name: str | None = None

    def to_wire(self) -> Value:
        # Phase 460 — `format` / `width` omitted-when-default (`CellFormat.None`
        # / `ColumnWidth.Auto`); `_obj` drops the `None`-valued entries.
        return _obj(
            None,
            {
                "field": self.field_name,
                "format": None if isinstance(self.format, FormatNone) else self.format,
                "kind": self.kind,
                "label": self.label,
                "value": None if self.field_name is not None else CLOSURE,
                "width": None if self.width.kind == "Auto" else self.width,
            },
        )


@dataclass(frozen=True)
class DataGrid:
    """A data grid.

    ``rowKey`` / ``rowKeyField`` mirror the column slots above: the closure spelling
    erases, the declarative one names a row property and survives the wire. Naming a
    ``row_key_field`` emits ``rowKeyField`` and omits the erased ``rowKey``.
    """

    source: Binding
    columns: tuple[Column, ...] = ()
    editable: bool = False
    row_key_field: str | None = None
    #: fuaran#1123 — the two sides of ONE shared State key, and between them they
    #: say exactly one thing: THESE GRIDS EXCHANGE ROWS. A grid declaring
    #: `transfer_out_key` K may RELEASE rows onto K; one declaring
    #: `transfer_in_key` K ACCEPTS rows arriving on it; one declaring both with
    #: one K does each. TWO members and not one symmetric key, because the
    #: one-way ends are ordinary — an archive column that accepts and never
    #: releases, a Done column that releases nothing back.
    transfer_out_key: str | None = None
    transfer_in_key: str | None = None
    #: fuaran#1125 — this grid's rows are the reader's to take, and the boolean is
    #: the WHOLE declaration: not the file format, not the file name, not the
    #: control, not the gesture, and not which rows. It is the grid-behaviour
    #: rule reached by a node that writes NOTHING — every other member of that
    #: family names a State key because the behaviour it declares writes
    #: something the grid reads back, and an export writes nothing.
    exportable: bool = False
    #: fuaran#1473 — no row of this grid is split across a page boundary. It
    #: applies to the grid's ROWS, not to the grid as a whole.
    keep_rows_together: bool = False
    #: fuaran#1473 — the column headers repeat at the top of every page the grid
    #: continues onto.
    repeat_header: bool = False

    def to_wire(self) -> Obj:
        # 0.2.0 — `editable` omitted-when-false; fuaran#1125 / #1473 join it on
        # exactly those terms, and the two transfer keys ride only when declared.
        return _obj(
            "DataGrid",
            {
                "columns": list(self.columns),
                "editable": True if self.editable else None,
                "exportable": True if self.exportable else None,
                "keepRowsTogether": True if self.keep_rows_together else None,
                "repeatHeader": True if self.repeat_header else None,
                "rowKey": None if self.row_key_field is not None else CLOSURE,
                "rowKeyField": self.row_key_field,
                "source": self.source,
                "transferInKey": self.transfer_in_key,
                "transferOutKey": self.transfer_out_key,
            },
        )


# Structural ------------------------------------------------------------------


@dataclass(frozen=True)
class ContentHash:
    algorithm: str
    hash: str
    strictness: str

    def to_wire(self) -> Value:
        return Obj(None, {"algorithm": self.algorithm, "hash": self.hash, "strictness": self.strictness})


@dataclass(frozen=True)
class Custom:
    module_id: str
    component_id: str
    props: dict[str, Value] = field(default_factory=dict)
    content_hash: ContentHash | None = None
    exposed_node_ids: tuple[str, ...] | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Custom",
            {
                "componentId": self.component_id,
                "contentHash": self.content_hash,
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
class SwitchCase:
    """One case in a :class:`Switch` (Phase 392): render ``child`` when the state
    value's string form equals ``match``. Wire: ``{"child":<Node>,"match":<str>}``."""

    match: str
    child: UiNode

    def to_wire(self) -> Obj:
        return _obj(None, {"child": self.child, "match": self.match})


@dataclass(frozen=True)
class Switch:
    """Binding-selected conditional child (Phase 392; the selector widened to any
    ``Binding`` by Phase 768) — render one of several child subtrees. The selector
    picks the case (first match on the value's string form wins); ``default``
    renders when none match (and is the SSR / first-paint surface).

    Exactly one selector spelling is given: ``state_key`` (the compact Phase 392
    form, canonical for a default-free ``State``) or ``on`` (any ``Binding`` —
    e.g. a :class:`Selection`, so the branch follows the clicked row with no
    writer). Mirroring the F#/TS encoders, an ``on`` that is a default-free
    ``State`` collapses to the ``stateKey`` wire spelling, so canonical bytes
    carry ``on`` only for a selector the compact form cannot spell. Wire:
    ``{"$type":"Switch","cases":[…],"default":<Node>,"stateKey":<str>|"on":<Binding>}``."""

    state_key: str | None
    cases: tuple[SwitchCase, ...]
    default: UiNode
    on: Binding | None = None
    #: fuaran#1122 — advance to the next case every this-many milliseconds. It
    #: declares the one fact a host cannot recover from the tree: every other
    #: half of a carousel is already composable (the stage is a `Box`, the panels
    #: the `cases`, the position the bound key, the arrows ordinary controls
    #: writing it), and nothing in any arrangement of those says a TIMER exists.
    #: A DURATION, never a flag — "advances" with no interval is not renderable,
    #: and two hosts inventing different periods is exactly the divergence the
    #: corpus exists to prevent.
    auto_advance_ms: int | None = None

    def __post_init__(self) -> None:
        if (self.state_key is None) == (self.on is None):
            raise ValueError("Switch takes exactly one selector: state_key or on")

    def to_wire(self) -> Obj:
        fields: dict[str, object] = {
            "cases": list(self.cases),
            "default": self.default,
        }
        if self.auto_advance_ms is not None:
            fields["autoAdvanceMs"] = self.auto_advance_ms
        selector = self.on
        if selector is None:
            fields["stateKey"] = self.state_key
        elif isinstance(selector, State) and selector.default_value is None:
            # Phase 768 collapse rule — State(key) keeps the compact spelling.
            fields["stateKey"] = selector.key
        else:
            fields["on"] = selector
        return _obj("Switch", fields)


# Fragment parameterisation (holes / effect / args) — WIRE_FORMAT.md §3.2 -----

HostEffect = Literal["Pure", "ReadsHost", "WritesHost"]
Determinism = Literal["Deterministic", "Clock", "Random", "Network"]


@dataclass(frozen=True)
class ScalarInt:
    value: int

    def to_wire(self) -> Value:
        return Obj("Int", {"value": self.value})


@dataclass(frozen=True)
class ScalarFloat:
    value: float

    def to_wire(self) -> Value:
        return Obj("Float", {"value": self.value})


@dataclass(frozen=True)
class ScalarBool:
    value: bool

    def to_wire(self) -> Value:
        return Obj("Bool", {"value": self.value})


@dataclass(frozen=True)
class ScalarStr:
    value: str

    def to_wire(self) -> Value:
        return Obj("Str", {"value": self.value})


Scalar = ScalarInt | ScalarFloat | ScalarBool | ScalarStr


@dataclass(frozen=True)
class IntRange:
    min: int
    max: int

    def to_wire(self) -> Value:
        return Obj("IntRange", {"max": self.max, "min": self.min})


@dataclass(frozen=True)
class FloatRange:
    min: float
    max: float

    def to_wire(self) -> Value:
        return Obj("FloatRange", {"max": self.max, "min": self.min})


@dataclass(frozen=True)
class StringLen:
    min_len: int
    max_len: int

    def to_wire(self) -> Value:
        return Obj("StringLen", {"maxLen": self.max_len, "minLen": self.min_len})


@dataclass(frozen=True)
class EnumSpace:
    choices: tuple[str, ...]

    def to_wire(self) -> Value:
        return _obj("Enum", {"choices": list(self.choices)})


@dataclass(frozen=True)
class AnyString:
    def to_wire(self) -> Value:
        return Obj("AnyString", {})


HoleValueSpace = IntRange | FloatRange | StringLen | EnumSpace | AnyString


@dataclass(frozen=True)
class ValueHole:
    name: str
    space: HoleValueSpace
    default: Scalar | None = None

    def to_wire(self) -> Value:
        return _obj("Value", {"default": self.default, "name": self.name, "space": self.space})


@dataclass(frozen=True)
class SlotHole:
    name: str
    kind_constraint: str | None = None

    def to_wire(self) -> Value:
        return _obj("Slot", {"kindConstraint": self.kind_constraint, "name": self.name})


@dataclass(frozen=True)
class RepeatHole:
    name: str
    count_space: HoleValueSpace

    def to_wire(self) -> Value:
        return Obj("Repeat", {"countSpace": _lower(self.count_space), "name": self.name})


HoleDecl = ValueHole | SlotHole | RepeatHole


@dataclass(frozen=True)
class EffectClass:
    host_effect: HostEffect
    determinism: Determinism

    def to_wire(self) -> Value:
        return Obj(None, {"determinism": self.determinism, "hostEffect": self.host_effect})


@dataclass(frozen=True)
class SlotArg:
    tree: UiNode

    def to_wire(self) -> Value:
        return Obj("SlotArg", {"tree": _lower(self.tree)})


FragmentArg = Scalar | SlotArg


@dataclass(frozen=True)
class FragmentDecl:
    name: str
    body: UiNode
    holes: tuple[HoleDecl, ...] = ()
    effect: EffectClass | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "FragmentDecl",
            {
                "body": self.body,
                "effect": self.effect,
                "holes": list(self.holes) if self.holes else None,
                "name": self.name,
            },
        )


@dataclass(frozen=True)
class FragmentRef:
    name: str
    args: dict[str, FragmentArg] | None = None

    def to_wire(self) -> Obj:
        args = None
        if self.args:
            args = Obj(None, {k: _lower(v) for k, v in self.args.items()})
        return _obj("FragmentRef", {"args": args, "name": self.name})


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

    def _repr_mimebundle_(
        self,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
    ) -> dict[str, str]:
        """The Jupyter rich-display bundle (fuaran#1161) — HTML, canonical wire, summary.

        An authored tree is the last expression of a notebook cell far more often
        than a decoded one is, so the display protocol is implemented on both and
        on the same terms: this one lowers to the structural model first, which is
        what :func:`~fuaran_py.schema.encode.encode_node` and the renderer already
        consume — one display path, not a second one that could drift from it.
        :mod:`fuaran_py.renderer.notebook` carries the contract.
        """
        from ..renderer.notebook import mimebundle

        return mimebundle(self.to_wire(), include, exclude)
