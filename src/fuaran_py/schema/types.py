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

from ..canonical import encode_value
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
#: Which edge of the chart the series legend occupies — or ``"None"``, which
#: suppresses it entirely. A WIRE vocabulary: WHERE an author wants the legend is
#: their meaning; the geometry that puts it there stays the host's chart style,
#: which carries the default (``"Right"``). An explicit value beats it.
ChartLegendPosition = Literal["Top", "Right", "Bottom", "None"]
#: Data labels, with exactly two states. ``"Ends"`` names the SELECTIVE
#: placements that read — a bar's cap, the last point of a line or area edge —
#: and the set is closed there; an all-points case would retract the legibility
#: guarantee the vocabulary exists for. Absent means ``"Off"``.
ChartDataLabels = Literal["Off", "Ends"]
#: What a chart's x axis MEANS — DECLARED, never sniffed. Absent means
#: ``"Category"`` (one band per row, in row order); ``"Temporal"`` says the x
#: column carries canonical ISO-8601 dates and the axis is continuous. The
#: pre-emit validator grounds the declaration against the column type where the
#: schema is statically known (FUARAN097).
ChartXScale = Literal["Category", "Temporal"]
#: Sort direction on a :class:`DefaultSort`. Lower-case on the wire, unlike the
#: capitalised display vocabularies above — the wire's own spelling, not a style.
SortDirection = Literal["asc", "desc"]
#: Phase 812 — anti-scraper render strategy for a :class:`Link`. ``"email"``
#: marks a ``mailto:`` link whose address must not appear in plaintext in emitted
#: HTML; the renderers own the emission strategy.
LinkProtection = Literal["email"]
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
#: Text alignment for a `Drawing`'s `Label` shape — SVG's `text-anchor`, in the
#: wire's own capitalised spelling. Absent means the renderer's inherited default.
TextAnchor = Literal["Start", "Middle", "End"]
#: Which way a `Mount`'s guest channel carries messages. `OutOnly` is the guest
#: bubbling up with no host→guest leg; `TwoWay` opens both.
ChannelDirection = Literal["OutOnly", "TwoWay"]
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


TimeGrain = Literal["Second", "Minute", "Hour", "Day"]


@dataclass(frozen=True)
class Now:
    """``Binding.Now`` — the host-furnished current instant (ISO-8601 UTC).

    The INSTANT is never on the wire: the clock lives in the HOST, resolved once
    per render pass — which is what keeps a tree a pure value and lets a replayed
    op-stream reproduce its original render. The typed ``project`` accessor is a
    closure, off the wire.

    Phase 1533 — ``grain`` is the one thing the wire DOES carry, and only when it
    is not the ``Second`` default, so a grain-less ``Now`` is still the bare
    ``{"$type":"Now"}``. It declares the RESOLUTION the document wants: the host
    truncates its instant before the projection sees it, so a ``Day``-grain
    ``Now`` is the ``YYYY-MM-DD`` a day-difference verb accepts.
    """

    grain: TimeGrain | None = None

    def to_wire(self) -> Value:
        return _obj("Now", {"grain": self.grain})


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


@dataclass(frozen=True)
class FmtSince:
    """``Format.Since`` (Phase 1533) — the INSTANT-reading twin of ``RelativeTime``.

    ``RelativeTime``'s source is a signed COUNT of its unit, already computed by
    whoever produced it; this one's source is an instant in whole Unix-epoch
    seconds (``Date``'s convention) and the count is the delta the HOST takes
    against its own furnished instant.

    ``unit`` absent is NOT a default — it is the auto-selection request, resolved
    from the fixed threshold ladder in WIRE_FORMAT.md 4b.
    """

    unit: RelativeTimeUnit | None = None

    def to_wire(self) -> Value:
        return _obj("Since", {"unit": self.unit})


Format = FmtNumber | FmtCurrency | FmtPercent | FmtDate | FmtRelativeTime | FmtDuration | FmtSince


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
    """``Binding.Local`` — a component-scoped edit buffer, in either of its two
    spellings (WIRE_FORMAT.md §3.3.3).

    The buffer always carries ``flushOn`` and ``initialFrom``, and ``format`` /
    ``parse`` are host closures that cross the wire as the sentinel. What differs
    is the **commit destination**, and the two are mutually exclusive:

    * the **handler** spelling — ``onCommit``, another closure sentinel. The
      default, and what every pre-declarative call already means.
    * the **declarative** spelling — ``commit_to``, the State key the flush
      writes, optionally through a ``codec`` the buffer formats and re-parses
      with. This is the half that survives the wire: a decoding host can honour
      it, where the closure it can only see the sentinel of.

    A document carrying both is a decode refusal (there is no precedence rule —
    two hosts would write to different places from identical bytes), so it is
    refused here at construction instead of being built and discovered later.
    """

    initial_from: Binding
    flush_on: LocalFlushTrigger
    #: The State key the flush writes — the declarative spelling. ``None`` leaves
    #: the buffer on the handler spelling.
    commit_to: str | None = None
    #: The edit-buffer codec. Only :class:`NumberFormat` is admissible: the
    #: buffer must PARSE BACK what it renders, and a locale-rendered format
    #: (Currency / Date / RelativeTime / Duration) has no total inverse.
    codec: CellFormat | None = None
    #: Whether the host closure is the commit destination. ``None`` (the default)
    #: means "whichever ``commit_to`` implies", so neither spelling has to name
    #: the other; pass it explicitly only to state the pairing you want, which is
    #: what makes an impossible pairing refusable rather than silently resolved.
    on_commit: bool | None = None

    def __post_init__(self) -> None:
        if self.on_commit and self.commit_to is not None:
            raise ValueError(
                "Binding.Local takes exactly one commit destination, and 'on_commit' and "
                "'commit_to' were both given — either 'on_commit' (a host closure, which "
                "crosses the wire only as the closure sentinel) or 'commit_to' (the State "
                "key the flush writes)"
            )
        if self.on_commit is False and self.commit_to is None:
            raise ValueError(
                "Binding.Local with on_commit=False commits nowhere — give it 'commit_to', "
                "the State key the flush writes"
            )
        if self.codec is not None and not isinstance(self.codec, NumberFormat):
            raise ValueError(
                "Binding.Local 'codec' must be a format with a total, locale-independent "
                f"inverse — only Number has one, and {type(self.codec).__name__} does not; "
                "a buffer that renders one way and parses another cannot round-trip what "
                "the reader typed"
            )

    def _commits_by_closure(self) -> bool:
        return self.commit_to is None if self.on_commit is None else self.on_commit

    def to_wire(self) -> Value:
        return _obj(
            "Local",
            {
                "codec": self.codec,
                "commitTo": self.commit_to,
                "flushOn": self.flush_on,
                "format": CLOSURE,
                "initialFrom": self.initial_from,
                "onCommit": CLOSURE if self._commits_by_closure() else None,
                "parse": CLOSURE,
            },
        )


@dataclass(frozen=True)
class Query:
    """``Binding.Query`` — a value the HOST resolves under a module-scoped name.

    The wire carries the name and, optionally, the names this query re-runs on:
    ``dependsOn`` is a list of the State / filter keys whose change invalidates
    the result. It is omitted when empty, so a dependency-free query is the bare
    ``{"$type":"Query","name":…}`` the grid and ``Call`` fixtures carry.

    What the wire does NOT carry is the RESULT: the typed ``accessor`` that
    projects the host's raw payload into the slot's type is a closure and has
    been off the wire since 0.2.0. So a query declares WHAT to ask for and WHEN
    to ask again, and nothing about the shape of the answer — the reading host
    resolves the name against its own registered sources, which is the same
    default-deny seam the capability registry gives ``Invoke``.
    """

    name: str
    #: The names whose change re-runs this query. A ``tuple`` rather than a list
    #: because the record is frozen and this is part of its identity; the
    #: authored ORDER is carried to the wire verbatim (the wire is a list, not a
    #: set, and two orders are two documents).
    depends_on: tuple[str, ...] = ()

    def to_wire(self) -> Value:
        fields: dict[str, Value] = {}
        if self.depends_on:
            fields["dependsOn"] = Arr(list(self.depends_on))
        fields["name"] = self.name
        return Obj("Query", fields)


@dataclass(frozen=True)
class InvokeArg:
    """One argument of an :class:`Invoke` — an ``addr`` naming a declared hole and
    the ``value`` bound into it.

    Both members are STRINGS on the wire, and the typing here says so rather than
    widening to a JSON value: the reference IDL declares ``req "addr" TStr;
    req "value" TStr``, so a number written here is a document the reference host
    refuses to decode. A capability's own signature is where an argument's real
    type lives (``fuaran_py.ui.capability.HoleSpace``), and it validates the
    parsed value at invocation time, on the host that owns the body.
    """

    addr: str
    value: str

    def to_wire(self) -> Value:
        # A bare (tag-less) record, not a discriminated case: `InvokeArg` is one
        # of the format's plain object records, so it carries no `$type`.
        return Obj(None, {"addr": self.addr, "value": self.value})


@dataclass(frozen=True)
class Invoke:
    """``Binding.Invoke`` / ``Action.Invoke`` — a host-registered capability
    referenced BY ID, with typed arguments; never code.

    One record for both unions, because the wire shape is identical in the two
    positions: as a binding it is a value SOURCE (the host resolves the
    invocation and the slot reads its result), as an action it is an EFFECT (the
    host runs it and the tree reads nothing back). The reference tier spells them
    as two distinct DU cases carrying the same fields; here one class is a member
    of both aliases, which is the same statement with nothing to keep in step.

    ``args`` is REQUIRED on the wire and is written even when empty (``"args":[]``)
    — an argument-free capability is a real thing and its emptiness is a
    statement, where an omitted key would read as "unspecified".

    The id is a reference, so it is only ever as safe as the registry that
    resolves it: an unregistered id is refused by
    :class:`~fuaran_py.ui.capability.CapabilityRegistry`, and the SHAPE is refused
    by the dispatch gate before that (``Invoke`` is a gated effect shape).
    """

    capability_id: str
    args: tuple[InvokeArg, ...] = ()

    def to_wire(self) -> Value:
        return Obj("Invoke", {"args": Arr([a.to_wire() for a in self.args]), "capabilityId": self.capability_id})

    def to_json(self) -> str:
        """The canonical JSON of this invocation on its own — the host bridge's
        input (``parse_invocation`` reads it back). Kept from the 0.2.0 surface
        this record was moved out of; nothing else in this module carries one,
        because nothing else is handed across a process boundary alone."""
        return encode_value(self.to_wire())


Binding = Static | State | Filter | Selection | Now | FormatBinding | Local | Query | Invoke

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


@dataclass(frozen=True)
class I18n:
    """A ``TextSource.I18n`` — a catalog KEY the reading host resolves in the
    reader's own locale, with a name-keyed bag of placeholder values.

    The key is the whole of what travels: the translated strings live in the
    host's catalog, so a document is locale-free and one document serves every
    reader. ``args`` fills the placeholders the catalog entry declares — plain
    JSON values, written verbatim.

    ``args`` is REQUIRED and is emitted even when empty (``"args":{}``), which is
    what the reference IDL declares (``req "args" (TMap TJson)``) and what
    ``tooltip-metric-1`` carries. Omitting it at empty would make a key with no
    placeholders a different document from the one every other host writes.

    Named ``I18n`` for the TEXT case, because the flat Python namespace has one
    name per class and this is the case the corpus exercises. The ``Binding``
    union has an ``I18n`` case of its own on the wire — a DIFFERENT shape, whose
    ``args`` are ``Binding<JVal>`` sources rather than literal values, and
    optional rather than required. When it is modelled here it takes a distinct
    class name (``I18nBinding``), on the ``FormatBinding``-beside-``Fmt*``
    precedent: two wire tags that collide under one Python name are two records,
    not one.
    """

    key: str
    args: dict[str, Value] = field(default_factory=dict)

    def to_wire(self) -> Value:
        # `_obj` would drop an empty dict only if it were `None`; `{}` is a value
        # and rides, which is the point — see the docstring.
        return _obj("I18n", {"args": dict(self.args), "key": self.key})


TextSource = LiteralText | Bound | I18n
"""The authoring ``TextSource`` surface (``Literal`` + ``Bound`` + ``I18n``)."""

TextInput = str | LiteralText | Bound | I18n
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
    """``Action.Navigate`` — fuaran#1536: the route is a ``TextSource``, not a
    bare string, so a tree can name a destination it computes from what the
    reader is looking at ("open the selected order"). ``target`` names the
    browsing context and is omitted at ``"Self"``.

    A plain ``str`` is accepted for ``route`` and carried as the bare JSON
    string, which IS ``TextSource.Literal``'s canonical form — so a caller
    written before the widening keeps working and its bytes do not move.

    ``target`` is a closed two-member vocabulary (``"Self"`` | ``"Blank"``)
    where ``LinkSpec.target`` is a free string: ``_parent`` and ``_top`` are
    frame-busting gestures a hosted tree must not be able to ask for. A
    ``"Blank"`` target is opened with ``noopener,noreferrer`` by the renderer.
    """

    route: Value
    target: str = "Self"

    def to_wire(self) -> Value:
        fields: dict[str, Value] = {"route": self.route}
        if self.target != "Self":
            fields["target"] = self.target
        return Obj("Navigate", fields)


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


@dataclass(frozen=True)
class Confirm:
    """``Action.Confirm`` — ask the reader, then dispatch (fuaran#1537).

    The second RECURSIVE action case after ``Chain``, and the first that
    recurses into NAMED members rather than a list. ``on_cancel`` is omitted
    from the wire when absent, and an absent cancel branch means *nothing
    happens*: a host must not substitute one.

    Confirmation is bounded at depth one — a ``Confirm`` reachable from either
    continuation, through a ``Chain`` included, is refused at decode. And the
    continuation is not a route around the dispatch gate: the dialogue is gated,
    and on acceptance the branch re-enters the ordinary dispatch entry so it
    meets its own gate and its own egress check.

    A confirmation is never an authorisation. The answer comes from the client,
    and a hostile client answers yes without asking anyone.
    """

    prompt: TextSource
    on_confirm: Action
    on_cancel: Action | None = None

    def to_wire(self) -> Value:
        # `_obj`, not a bare `Obj`: the prompt is a typed `TextSource` and has to
        # be LOWERED, exactly as `WriteToClipboard`'s payload is. A `Literal`
        # lowers to the bare JSON string. Keys sort to onCancel < onConfirm <
        # prompt; `onCancel` rides only when present.
        fields: dict[str, object] = {}
        if self.on_cancel is not None:
            fields["onCancel"] = self.on_cancel.to_wire()
        fields["onConfirm"] = self.on_confirm.to_wire()
        fields["prompt"] = self.prompt
        return _obj("Confirm", fields)


@dataclass(frozen=True)
class Focus:
    """``Action.Focus`` — move keyboard focus to an addressed node (fuaran#1537).

    A bare string and never a ``TextSource``: it addresses a node in this
    document, which the author wrote, so there is nothing here for a binding to
    compute — the ``CommitLocal`` precedent.

    What this does not claim: nothing about scrolling (a host may scroll as a
    consequence of focusing, and this neither asks it to nor prevents it) and
    nothing about selection. A node id that addresses nothing warns and moves
    nothing.
    """

    node_id: str

    def to_wire(self) -> Value:
        return Obj("Focus", {"nodeId": self.node_id})


FileReadEncoding = Literal["Text", "Base64", "DataUrl"]


@dataclass(frozen=True)
class ReadFileBody:
    """``Action.ReadFileBody`` — reads a selected file's body; ``onRead`` is a closure."""

    file_ref: str
    encoding: FileReadEncoding = "Text"

    def to_wire(self) -> Value:
        return Obj("ReadFileBody", {"encoding": self.encoding, "fileRef": self.file_ref, "onRead": CLOSURE})


@dataclass(frozen=True)
class IntoState:
    """A :class:`Call` result landing in the reactive State channel under ``key``
    — read back by every ``Binding.State`` naming it. Wire tag: ``State``."""

    key: str

    def to_wire(self) -> Value:
        return Obj("State", {"key": self.key})


@dataclass(frozen=True)
class IntoQuery:
    """A :class:`Call` result landing in the query-results slot under ``name`` —
    read back by every ``Binding.Query`` naming it. Wire tag: ``Query``."""

    name: str

    def to_wire(self) -> Value:
        return Obj("Query", {"name": self.name})


CallResultTarget = IntoState | IntoQuery
"""Where a :class:`Call`'s result lands, declaratively.

The two case classes are named ``IntoState`` / ``IntoQuery`` where their WIRE
tags are ``State`` / ``Query``, because Python's namespace is flat and both of
those names are already taken by the ``Binding`` cases that READ these slots.
Keeping them distinct is not cosmetic: it is what stops an author writing
``into=Filter("x")`` — a binding, not a result target — and getting a document
no host can honour.
"""


@dataclass(frozen=True)
class Call:
    """``Action.Call`` — ask a host endpoint, then do something with the answer.

    Two independent, both-optional ways to take the result, and the wire allows
    any combination of them including neither:

    * ``into`` — the DECLARATIVE target (:data:`CallResultTarget`). This is the
      half that survives the wire: a decoding host can honour it, where a closure
      it can only see the sentinel of.
    * ``on_result`` — the HANDLER spelling. A host closure, so it crosses the
      wire as ``"<closure>"`` and carries nothing a decoder can act on; it is a
      declaration that this call HAS a handler in the emitting host.

    Neither is the third real shape (``composite-tabs-panels``'s form submits and
    reads nothing back), which is why ``on_result`` is a plain ``False`` default
    rather than the tri-state ``Binding.Local`` uses: there is no "whichever the
    other implies" rule to express, because a call with no result target and no
    handler is an ordinary fire-and-forget submit rather than an under-specified
    one.
    """

    endpoint: str
    into: CallResultTarget | None = None
    #: Whether the emitting host holds a result handler. Written as the closure
    #: sentinel when true, omitted when false — never as a value, because there
    #: is no value: a closure does not cross a wire.
    on_result: bool = False

    def to_wire(self) -> Value:
        return _obj(
            "Call",
            {
                "endpoint": self.endpoint,
                "into": self.into,
                "onResult": CLOSURE if self.on_result else None,
            },
        )


@dataclass(frozen=True)
class AiTool:
    """``Action.AiTool`` — invoke a named tool on the host's AI surface with a
    JSON argument bag.

    ``args`` is a JVal written verbatim, exactly as :class:`Notify`'s ``payload``
    and :class:`SetState`'s ``value`` are, and for the same reason: these three
    carry real data in both directions, so erasing them to a sentinel would be
    silent data loss. Both members are required on the wire.

    Distinct from :class:`Invoke`, which it is easy to conflate. ``Invoke`` names
    a capability the HOST registered, with typed string args validated against a
    declared signature; this names a tool on an AI surface, with a free-form
    payload. Both are gated effect shapes, and neither is a route around the
    gate.
    """

    tool_name: str
    args: Value

    def to_wire(self) -> Value:
        return Obj("AiTool", {"args": _lower(self.args), "toolName": self.tool_name})


Action = (
    Chain
    | Dispatch
    | Navigate
    | SetState
    | Notify
    | WriteToClipboard
    | Print
    | Confirm
    | Focus
    | ReadFileBody
    | Call
    | AiTool
    | Invoke
)


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
    #: Phase 1576 — whether a HOST HANDLER is present on the INDEX channel. The
    #: reference host types this as an option whose `None` arms the write-back
    #: default (an `activeIndex` bound to `State` / `Filter` has the clicked index
    #: written to that slot); `True` is the default here so every tree authored
    #: before this flag encodes byte-identically.
    on_select: bool = True
    #: Phase 1576 — the TAG channel's handler, the sibling of `on_select` over
    #: `active_tag` / `tab_tags`. A separate slot rather than a mode of the first:
    #: the two channels arm independently, and a tab strip may dispatch the tag
    #: while writing back the index (`controls-closure` carries both).
    on_select_tag: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Tabs",
            {
                # Phase 1585 — omitted at the identity `Static(0)`. Every host's
                # decoder restores it on absence, so a tab strip opening on its
                # first tab pays no key for it. The test is on the CASE and its
                # PAYLOAD, unlike `Chart.stacked`'s: the identity is one
                # inhabitant of a union with an unbounded payload domain, so a
                # `Static` carrying any other index still rides, and so does
                # every `State` / `Filter` / `Selection` / `Query` binding.
                # `type(v) is int` rather than `== 0`: Python makes `False == 0`
                # and `0.0 == 0` both true, and a mistyped payload must reach the
                # wire to be refused there rather than being silently dropped
                # here as if it were the identity.
                "activeIndex": (
                    None
                    if isinstance(self.active_index, Static)
                    and type(self.active_index.value) is int
                    and self.active_index.value == 0
                    else self.active_index
                ),
                "activeTag": self.active_tag,
                "children": list(self.children),
                "onSelect": CLOSURE if self.on_select else None,
                "onSelectTag": CLOSURE if self.on_select_tag else None,
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
    #: Phase 1576 — `Tabs.on_select`'s twin: absent arms the write-back default on
    #: `activeStep`, `True` (the default, so pre-phase trees are byte-identical)
    #: declares the host closure.
    on_select: bool = True

    def to_wire(self) -> Obj:
        return _obj(
            "Stepper",
            {
                "activeStep": self.active_step,
                "children": list(self.children),
                "onSelect": CLOSURE if self.on_select else None,
            },
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
    #: Phase 1576 — the host toggle handler. `False` is the default because this
    #: record never emitted the key at all before the phase, so absence (the
    #: write-back default the reference host describes) is what every existing
    #: tree already means; `True` declares the closure `controls-closure` carries.
    on_toggle: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Disclosure",
            {
                "children": list(self.children),
                "defaultOpen": self.default_open,
                "heading": self.heading,
                "onToggle": CLOSURE if self.on_toggle else None,
                "open": self.open,
            },
        )


@dataclass(frozen=True)
class Modal:
    children: tuple[UiNode, ...] = ()
    open: Binding = field(default_factory=lambda: Static(False))
    dismissable: bool = False
    #: Phase 1576 — OPTIONAL, and `None` is a distinct fact rather than a missing
    #: argument: the reference host arms the dismiss write-back default when the
    #: wire omits the key, so a decoded dismissable modal closes itself with no
    #: host code. The default stays the no-op `Chain` every pre-phase tree already
    #: emitted (`modal-1` carries it), so passing `None` is how an author declares
    #: no handler.
    on_dismiss: Action | None = field(default_factory=Chain)
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
class Fact:
    """``Fact`` — the labelled TEXT statement, :class:`Metric`'s complementary kind.

    Where a ``Metric`` carries a NUMBER through a :class:`CellFormat`, a ``Fact``
    carries a ``TextSource``: "Patient — Alice Smith", "Today — <the host's
    instant>". That is why ``value`` is a ``TextSource`` rather than a ``Binding``
    — the corpus binds it through ``Bound`` (a ``Now`` for an environment
    reading, a ``Selection`` for a master-detail pane), and a literal is the bare
    string.

    ``emphasis`` is the behavioural BOOL rather than the three-valued display
    :data:`Emphasis` the rest of the surface uses — the wire spells it that way,
    and it is omitted at ``False``; ``tone`` is omitted at ``"Default"``. Both
    omissions are the §3.6 rule, so a Fact that declares neither is the minimal
    two-key document the corpus carries.
    """

    label: TextSource
    value: TextSource
    tone: Tone = "Default"
    emphasis: bool = False
    help: TextSource | None = None
    icon: str | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Fact",
            {
                "emphasis": True if self.emphasis else None,
                "help": self.help,
                "icon": self.icon,
                "label": self.label,
                "tone": None if self.tone == "Default" else self.tone,
                "value": self.value,
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
    #: Phase 812 — how the emitting host must PROTECT this destination, not how
    #: it must draw it. The one admitted value marks a ``mailto:`` address that
    #: must not reach emitted HTML in plaintext; a renderer that has no strategy
    #: still has the declaration, which is why it rides the wire rather than
    #: living in a host's own configuration.
    protection: LinkProtection | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Link",
            {
                "download": self.download,
                "href": self.href,
                "label": self.label,
                "protection": self.protection,
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


# ── Drawing — placed geometry (WIRE_FORMAT §4b) ─────────────────────────────
#
# A ``Drawing`` is a RESOLVED geometric artefact: a chart lowering produces
# concrete coordinates, so every coordinate slot is a plain ``float`` and only
# :class:`DrawStyle` carries bindings. That split is the reason the kind is worth
# having at all — the picture survives the wire, and its colour stays reactive.
#
# The sub-records sit immediately above the kind that uses them, following the
# §4l annotation family's placement above :class:`Chart` rather than being
# scattered among the shared records at the module head.


@dataclass(frozen=True)
class ViewBox:
    """The 2-D user-space coordinate box — SVG's ``viewBox``, field by field.

    All four slots are REQUIRED and each admits the §7 non-finite sentinels
    (``float("nan")`` / ``float("inf")``), which the canonical encoder writes as
    the quoted ``"NaN"`` / ``"Infinity"`` / ``"-Infinity"`` tokens. Nothing here
    refuses one: a degenerate box is a document a conformant host must be able to
    carry and refuse for itself, and the ``drawing-nonfinite-sentinels`` fixture
    exists to pin exactly that.
    """

    min_x: float
    min_y: float
    width: float
    height: float

    def to_wire(self) -> Value:
        return Obj(None, {"height": self.height, "minX": self.min_x, "minY": self.min_y, "width": self.width})


@dataclass(frozen=True)
class DrawPoint:
    """A point in a drawing's user-space coordinates."""

    x: float
    y: float

    def to_wire(self) -> Value:
        return Obj(None, {"x": self.x, "y": self.y})


@dataclass(frozen=True)
class DrawStyle:
    """The fill / stroke (+ text) style every :class:`Shape` carries.

    EVERY slot is optional and omitted when absent (rule 4), so a shape emits only
    what differs from the renderer's inherited default — which is what keeps
    ``drawing-empty``'s ``"style":{}`` the empty object rather than eleven nulls.

    The text cluster (``font_family`` / ``font_size`` / ``text_anchor`` /
    ``emphasis`` / ``rotation``) is honoured off :class:`Label` alone; ``tip``
    applies to every shape and is the hover-readable text the renderer emits as an
    SVG ``<title>`` child. ``rotation`` at ``0.0`` is a DOCUMENT rather than an
    absence — the fixture carries an explicit zero — so only ``None`` omits it.
    """

    fill: Binding | None = None
    stroke: Binding | None = None
    stroke_width: Binding | None = None
    opacity: Binding | None = None
    text_anchor: TextAnchor | None = None
    font_size: float | None = None
    emphasis: Emphasis | None = None
    font_family: str | None = None
    mark_id: str | None = None
    rotation: float | None = None
    tip: TextSource | None = None

    def to_wire(self) -> Value:
        return _obj(
            None,
            {
                "emphasis": self.emphasis,
                "fill": self.fill,
                "fontFamily": self.font_family,
                "fontSize": self.font_size,
                "markId": self.mark_id,
                "opacity": self.opacity,
                "rotation": self.rotation,
                "stroke": self.stroke,
                "strokeWidth": self.stroke_width,
                "textAnchor": self.text_anchor,
                "tip": self.tip,
            },
        )


@dataclass(frozen=True)
class MoveTo:
    to: DrawPoint

    def to_wire(self) -> Value:
        return Obj("MoveTo", {"to": _lower(self.to)})


@dataclass(frozen=True)
class LineTo:
    to: DrawPoint

    def to_wire(self) -> Value:
        return Obj("LineTo", {"to": _lower(self.to)})


@dataclass(frozen=True)
class CubicTo:
    control1: DrawPoint
    control2: DrawPoint
    to: DrawPoint

    def to_wire(self) -> Value:
        return Obj(
            "CubicTo",
            {"control1": _lower(self.control1), "control2": _lower(self.control2), "to": _lower(self.to)},
        )


@dataclass(frozen=True)
class QuadraticTo:
    control: DrawPoint
    to: DrawPoint

    def to_wire(self) -> Value:
        return Obj("QuadraticTo", {"control": _lower(self.control), "to": _lower(self.to)})


@dataclass(frozen=True)
class Close:
    """The tag-only subpath close — no fields at all."""

    def to_wire(self) -> Value:
        return Obj("Close", {})


CurveCommand = MoveTo | LineTo | CubicTo | QuadraticTo | Close
"""The typed path vocabulary for :class:`Curve` — closed, and with no ``d`` string.

A path string would smuggle a whole second grammar past every validator and every
tree op; these five commands are what a conformant host has to understand.
"""


@dataclass(frozen=True)
class Group:
    children: tuple[Shape, ...] = ()
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj("Group", {"children": Arr([_lower(c) for c in self.children]), "style": _lower(self.style)})


@dataclass(frozen=True)
class Rectangle:
    x: float
    y: float
    width: float
    height: float
    corner_radius: float | None = None
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return _obj(
            "Rectangle",
            {
                "cornerRadius": self.corner_radius,
                "height": self.height,
                "style": self.style,
                "width": self.width,
                "x": self.x,
                "y": self.y,
            },
        )


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj(
            "Line",
            {
                "style": _lower(self.style),
                "x1": self.x1,
                "x2": self.x2,
                "y1": self.y1,
                "y2": self.y2,
            },
        )


@dataclass(frozen=True)
class Polyline:
    points: tuple[DrawPoint, ...] = ()
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj("Polyline", {"points": Arr([_lower(p) for p in self.points]), "style": _lower(self.style)})


@dataclass(frozen=True)
class Polygon:
    points: tuple[DrawPoint, ...] = ()
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj("Polygon", {"points": Arr([_lower(p) for p in self.points]), "style": _lower(self.style)})


@dataclass(frozen=True)
class Curve:
    commands: tuple[CurveCommand, ...] = ()
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj("Curve", {"commands": Arr([_lower(c) for c in self.commands]), "style": _lower(self.style)})


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj("Circle", {"cx": self.cx, "cy": self.cy, "r": self.r, "style": _lower(self.style)})


@dataclass(frozen=True)
class Ellipse:
    cx: float
    cy: float
    rx: float
    ry: float
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj(
            "Ellipse",
            {"cx": self.cx, "cy": self.cy, "rx": self.rx, "ry": self.ry, "style": _lower(self.style)},
        )


@dataclass(frozen=True)
class Label:
    x: float
    y: float
    text: TextSource
    style: DrawStyle = field(default_factory=lambda: DrawStyle())

    def to_wire(self) -> Value:
        return Obj(
            "Label",
            {"style": _lower(self.style), "text": _lower(self.text), "x": self.x, "y": self.y},
        )


Shape = Group | Rectangle | Line | Polyline | Polygon | Curve | Circle | Ellipse | Label
"""The closed primitive set. An unknown discriminator is default-denied on decode."""


@dataclass(frozen=True)
class Drawing:
    """``Drawing`` — the placed-geometry kind (WIRE_FORMAT §4b).

    ``shapes`` / ``style`` / ``view_box`` are required and always emitted — an
    empty shape list and an all-default style are the ``drawing-empty`` document,
    not an absence — while ``title`` and ``description`` are the accessible name
    and long description, omitted when unnamed.
    """

    view_box: ViewBox
    shapes: tuple[Shape, ...] = ()
    style: DrawStyle = field(default_factory=lambda: DrawStyle())
    title: TextSource | None = None
    description: TextSource | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Drawing",
            {
                "description": self.description,
                "shapes": Arr([_lower(s) for s in self.shapes]),
                "style": self.style,
                "title": self.title,
                "viewBox": self.view_box,
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
    # Multi-select (Phase 291): ``multiple`` is emitted only when ``True`` (a
    # single-select stays byte-identical to the pre-multi corpus); ``values``
    # is the ``Binding<string list>`` of selected option values, emitted only
    # when present.
    multiple: bool = False
    values: Binding | None = None
    #: fuaran#1170 — whether a HOST HANDLER is present. The wire's `onChange` is a
    #: closure and so rides as the sentinel; what matters is whether the key is there
    #: at all, because a renderer arms its write-back default only for a control that
    #: declares no handler. `True` is the default so every tree authored before this
    #: flag encodes byte-identically; a control meant to WRITE its own slot passes
    #: `on_change=False`. Same shape and same reason as `ToggleField.on_toggle`.
    on_change: bool = True
    #: Phase 1576 — the MULTI channel's handler, over ``values``. A separate wire
    #: key (`onChangeMulti`) and so a separate slot: a multi-select may dispatch
    #: the whole selection while the single-value channel does something else, and
    #: `controls-closure` carries both. `False` by default — the record never
    #: emitted the key before this phase.
    on_change_multi: bool = False

    def to_wire(self) -> Obj:
        return _obj(
            "Select",
            {
                "disabled": self.disabled,
                "label": self.label,
                "multiple": self.multiple if self.multiple else None,
                "onChange": CLOSURE if self.on_change else None,
                "onChangeMulti": CLOSURE if self.on_change_multi else None,
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
    #: Phase 1576 — the selection handler. `True` by default so every pre-phase
    #: upload is byte-identical; `False` reaches the handler-less spelling.
    on_select: bool = True
    #: fuaran#1548 — the largest single file this control accepts, in bytes. PER
    #: FILE, not per selection: it bounds each file the reader picks, which is
    #: what makes it the quantity the `file-read` route can be measured against
    #: and what makes it meaningful on a single-file upload. Absent declares no
    #: ceiling — the pre-1548 control, bounded only by whatever the host already
    #: enforces. Positive-only, and a signed 32-bit integer like every typed
    #: integer slot on this wire (WIRE_FORMAT §7.1).
    max_bytes: int | None = None
    #: fuaran#1548 — how many files this control accepts in one selection.
    #: Absent declares no ceiling. Meaningful only alongside `multiple`: a
    #: single-file upload admits one file by construction, so a ceiling there is
    #: INERT rather than wrong, and is documented rather than refused.
    #: Positive-only.
    max_files: int | None = None

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
                # fuaran#1548 — the two declared ceilings, ordinary optionals,
                # so an upload declaring neither is byte-identical to what it
                # always was.
                "maxBytes": self.max_bytes,
                "maxFiles": self.max_files,
                "multiple": self.multiple,
                "onSelect": CLOSURE if self.on_select else None,
            },
        )


# Visualisation ---------------------------------------------------------------
#
# ── Chart annotations (§4l) ──────────────────────────────────────────────────
#
# An annotation carries an ADDRESS and a LABEL, and nothing else: WHERE in the
# data a threshold or an episode sits is the author's meaning, while the stroke
# weights, opacities and label offsets that draw it are the host's, in
# :mod:`fuaran_py.charts`. That split is why the union is a WIRE vocabulary and
# why it is CLOSED at three members — a reference line on the value axis, an
# event marker at one x address, a range band between two.
#
# The addresses are DECLARED rather than sniffed, which is what lets the pre-emit
# validator ground them (FUARAN137-141) instead of the lowering guessing: a
# category key that names no band, a date on a band axis, a pair that runs
# backwards are each refused by name.


@dataclass(frozen=True)
class AnnotationCategory:
    """An x address on a BAND axis — one category key, which must name exactly
    one row of the chart's own source."""

    key: str

    def to_wire(self) -> Value:
        return _obj("Category", {"key": self.key})


@dataclass(frozen=True)
class AnnotationDate:
    """An x address on a TEMPORAL axis — a canonical ISO-8601 ``YYYY-MM-DD``."""

    iso: str

    def to_wire(self) -> Value:
        return _obj("Date", {"iso": self.iso})


AnnotationX = AnnotationCategory | AnnotationDate
"""One x-axis address, in the axis's own form. The validator refuses the
mismatch rather than coercing it: a date read as a category grounds against no
band, and a category read as a date lands on the epoch."""


@dataclass(frozen=True)
class ValueRange:
    """A range band's ends on the VALUE axis, in the axis's own units."""

    # ``from`` is a Python keyword, so the member is ``from_`` and the WIRE key
    # is spelled out in `to_wire` — the same accommodation `Column.field_name`
    # makes for `field`.
    from_: float
    to: float

    def to_wire(self) -> Value:
        return _obj("ValueRange", {"from": self.from_, "to": self.to})


@dataclass(frozen=True)
class XRange:
    """A range band's ends on the X axis — two addresses of the SAME form."""

    from_: AnnotationX
    to: AnnotationX

    def to_wire(self) -> Value:
        return _obj("XRange", {"from": self.from_, "to": self.to})


AnnotationRange = ValueRange | XRange


@dataclass(frozen=True)
class ReferenceLine:
    """A horizontal line at one place on the VALUE axis — a target, a floor, a
    budget. Neutralised on a Pie, which has no value axis."""

    value: float
    label: TextSource | None = None

    def to_wire(self) -> Value:
        return _obj("ReferenceLine", {"label": self.label, "value": self.value})


@dataclass(frozen=True)
class EventMarker:
    """A vertical rule at one x address — a launch, a repricing, an incident."""

    at: AnnotationX
    label: TextSource | None = None

    def to_wire(self) -> Value:
        return _obj("EventMarker", {"at": self.at, "label": self.label})


@dataclass(frozen=True)
class RangeBand:
    """A shaded interval — a tolerance band on the value axis, a freeze window on
    the x axis."""

    range: AnnotationRange
    label: TextSource | None = None

    def to_wire(self) -> Value:
        return _obj("RangeBand", {"label": self.label, "range": self.range})


ChartAnnotation = ReferenceLine | EventMarker | RangeBand
"""The closed three-member annotation union (§4l)."""


@dataclass(frozen=True)
class Chart:
    source: Binding
    x_field: str
    y_fields: tuple[str, ...]
    kind: ChartKind = "Line"
    stacked: bool = False
    title: TextSource | None = None
    #: The VALUE axis's number format, reusing the shared `Format` vocabulary
    #: (Phase 876). A semantic declaration, not an appearance: "these are pounds"
    #: is the author's, the tick-label typography is the host's.
    value_format: Format | None = None
    #: The two axis titles and the chart's second line (Phase 878/880). Each is a
    #: `TextSource`, so a bare `str` lowers to the canonical bare string.
    x_title: TextSource | None = None
    y_title: TextSource | None = None
    subtitle: TextSource | None = None
    #: Phase 880 — an explicit legend edge beats the chart style's default.
    legend_position: ChartLegendPosition | None = None
    #: Phase 879 — data labels, absent meaning `"Off"`.
    data_labels: ChartDataLabels | None = None
    #: Phase 882 — what the x column MEANS, absent meaning `"Category"`.
    x_scale: ChartXScale | None = None
    #: Phase 1490/1491/1492 (§4l) — the data-addressed annotations, in DOCUMENT
    #: order, which is what the per-case ordinal in a mark id indexes. Absent and
    #: empty are the same picture, but they are not the same wire: absent omits
    #: the key, so a chart authored before the slot existed still encodes
    #: byte-for-byte as it did.
    annotations: tuple[ChartAnnotation, ...] | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "Chart",
            {
                "annotations": list(self.annotations) if self.annotations is not None else None,
                "dataLabels": self.data_labels,
                "kind": self.kind,
                "legendPosition": self.legend_position,
                "source": self.source,
                # Phase 1585 — omitted-when-false. Every host's decoder restores
                # `False` on absence, so a grouped chart pays no key for it.
                "stacked": True if self.stacked else None,
                "subtitle": self.subtitle,
                "title": self.title,
                "valueFormat": self.value_format,
                "xField": self.x_field,
                "xScale": self.x_scale,
                "xTitle": self.x_title,
                "yFields": list(self.y_fields),
                "yTitle": self.y_title,
            },
        )


@dataclass(frozen=True)
class DefaultSort:
    """Phase 801 — the column a sortable table or grid is sorted by BEFORE the
    reader touches anything, and in which direction.

    ``column`` is a zero-based ORDINAL into the headers / columns as authored,
    not a field name: the static-rows table has no field names at all, and one
    shape for both halves is what keeps the two from drifting.
    """

    column: int
    direction: SortDirection

    def to_wire(self) -> Value:
        return _obj(None, {"column": self.column, "direction": self.direction})


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
    #: Phase 801 — the reader may re-sort the table by clicking a header. TRI-STATE
    #: rather than a plain bool: absent is the pre-801 wire byte-for-byte, and an
    #: explicit ``False`` is an author saying so, which a host may read differently
    #: from an author who has not been asked.
    sortable: bool | None = None
    #: The sort in force before any click. Meaningful with ``sortable`` absent —
    #: an author can order a table the reader cannot re-order.
    default_sort: DefaultSort | None = None

    def to_wire(self) -> Obj:
        return _obj(
            "DataGrid",
            {
                "columns": [],
                "source": Static(Arr([])),
                # `_obj`, not a bare dict: `_lower` keeps every key a dict carries,
                # so the two optional slots would encode as JSON null rather than
                # being omitted.
                "staticRows": _obj(
                    None,
                    {
                        "defaultSort": self.default_sort,
                        "headers": list(self.headers),
                        "rows": [list(r) for r in self.rows],
                        "sortable": self.sortable,
                    },
                ),
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
#
# **Both the handler and the value are OPTIONAL on every control** (Phase 1576,
# generalising fuaran#1170's `Select` / `NumberField` pair). The schema's required
# list for each case is `$type` plus that case's own structural members —
# `options`, `rows`, `variant`, `max` — and never the handler or the value, and
# the two absences say different things a host acts on:
#
# * **No handler** is what ARMS the renderer's write-back default. A closure
#   cannot cross the wire, so a control declaring one describes changes that go
#   somewhere the document cannot reach; a control declaring none writes its own
#   slot. `on_change` / `on_toggle` therefore say whether a HOST HANDLER is
#   present, and default to `True` on every record that emitted one
#   unconditionally before this phase — so every tree authored against the older
#   surface encodes byte-identically, and reaching the canonical minimal control
#   is an explicit `False`.
# * **No value** is the auto-bound control: the decoder synthesises `Filter(name)`
#   on a chip and `State(field id, <typed placeholder>)` on a form field, so
#   `{"$type":"Text"}` IS a bound control rather than an empty one. A declared
#   `State` / `Local` binding is honoured exactly as before — the default is
#   absence, and absence is only what an author who says nothing gets.


@dataclass(frozen=True)
class TextField:
    value: Binding | None = None
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj("Text", {"onChange": CLOSURE if self.on_change else None, "value": self.value})


@dataclass(frozen=True)
class NumberField:
    value: Binding | None = None
    #: fuaran#1170 — see `Select.on_change`: the absent key is what arms a renderer's
    #: write-back default, so a control that writes its own slot passes `False`.
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj("Number", {"onChange": CLOSURE if self.on_change else None, "value": self.value})


@dataclass(frozen=True)
class CheckboxField:
    value: Binding | None = None
    on_toggle: bool = True

    def to_wire(self) -> Value:
        return _obj("Checkbox", {"onToggle": CLOSURE if self.on_toggle else None, "value": self.value})


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
    value: Binding | None = None
    #: REQUIRED by the schema (`TextArea` is the one control whose required list
    #: names a member beyond `$type` that is not a discriminating structure), but
    #: it carries a default so `value` can precede it and every positional
    #: `TextAreaField(value, rows)` call keeps working. An omitted `rows` is
    #: refused by name at construction rather than encoded as a control the
    #: schema rejects.
    rows: int | None = None
    on_change: bool = True

    def __post_init__(self) -> None:
        if self.rows is None:
            raise ValueError("FormFieldKind.TextArea requires 'rows' — the schema's required list names it")

    def to_wire(self) -> Value:
        return _obj(
            "TextArea", {"onChange": CLOSURE if self.on_change else None, "rows": self.rows, "value": self.value}
        )


@dataclass(frozen=True)
class RangedNumber:
    value: Binding | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj(
            "RangedNumber",
            {
                "max": self.max,
                "min": self.min,
                "onChange": CLOSURE if self.on_change else None,
                "step": self.step,
                "value": self.value,
            },
        )


@dataclass(frozen=True)
class RangeField:
    """``FormFieldKind.Range`` — the PAIR-valued numeric range control.

    The sibling of :class:`RangedNumber`, and the difference is the value rather
    than the bounds: a `RangedNumber` resolves to ONE number inside `min`/`max`,
    where this resolves to the ordered `(min, max)` pair the reader has narrowed
    to. A literal pair rides the wire as the BARE ``{"max":…,"min":…}`` object
    (no ``Static`` envelope — the ``DateRange`` posture), and the auto-bound
    spelling omits `value` entirely.
    """

    value: Binding | tuple[float, float] | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    on_change: bool = True

    def to_wire(self) -> Value:
        pair = self.value
        lowered: object = Obj(None, {"max": pair[1], "min": pair[0]}) if isinstance(pair, tuple) else pair
        return _obj(
            "Range",
            {
                "max": self.max,
                "min": self.min,
                "onChange": CLOSURE if self.on_change else None,
                "step": self.step,
                "value": lowered,
            },
        )


@dataclass(frozen=True)
class DateField:
    value: Binding | None = None
    variant: DateVariant = "Date"
    min: str | None = None
    max: str | None = None
    step: float | None = None
    #: fuaran#1170 — see `Select.on_change`: the absent key is what arms a renderer's
    #: write-back default, so a control that writes its own slot passes `False`.
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj(
            "Date",
            {
                "max": self.max,
                "min": self.min,
                "onChange": CLOSURE if self.on_change else None,
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

    value: Binding | tuple[str, str] | None = None
    variant: DateVariant = "Date"
    min: str | None = None
    max: str | None = None
    step: float | None = None
    on_change: bool = True

    def to_wire(self) -> Value:
        pair = self.value
        lowered: object = Obj(None, {"from": pair[0], "to": pair[1]}) if isinstance(pair, tuple) else pair
        return _obj(
            "DateRange",
            {
                "max": self.max,
                "min": self.min,
                "onChange": CLOSURE if self.on_change else None,
                "step": self.step,
                "value": lowered,
                "variant": self.variant,
            },
        )


@dataclass(frozen=True)
class ChoiceField:
    options: Binding
    value: Binding | None = None
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj(
            "Choice",
            {"onChange": CLOSURE if self.on_change else None, "options": self.options, "value": self.value},
        )


@dataclass(frozen=True)
class SegmentedChoice:
    options: Binding
    value: Binding | None = None
    orientation: Orientation = "Horizontal"
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj(
            "SegmentedChoice",
            {
                "onChange": CLOSURE if self.on_change else None,
                "options": self.options,
                "orientation": self.orientation,
                "value": self.value,
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
    value: Binding | None = None
    allow_free_text: bool = False
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj(
            "Combobox",
            {
                "allowFreeText": True if self.allow_free_text else None,
                "onChange": CLOSURE if self.on_change else None,
                "options": self.options,
                "value": self.value,
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

    value: Binding | None = None
    allow_free_text: bool = True
    #: An ABSENT source and an EMPTY one are different facts: absent means the
    #: control has no candidate set at all, resolved-empty means it has one that
    #: is currently empty — which is also every asynchronous source's first frame.
    suggestions: Binding | None = None
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj(
            "Tokens",
            {
                "allowFreeText": None if self.allow_free_text else False,
                "onChange": CLOSURE if self.on_change else None,
                "suggestions": self.suggestions,
                "value": self.value,
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
    value: Binding | None = None
    allow_half: bool = False
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj(
            "Rating",
            {
                "allowHalf": True if self.allow_half else None,
                "max": self.max,
                "onChange": CLOSURE if self.on_change else None,
                "value": self.value,
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

    value: Binding | None = None
    on_change: bool = True

    def to_wire(self) -> Value:
        return _obj("Color", {"onChange": CLOSURE if self.on_change else None, "value": self.value})


FormFieldKind = (
    TextField
    | NumberField
    | CheckboxField
    | ToggleField
    | TextAreaField
    | RangedNumber
    | RangeField
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
RangeFilter = RangeField

FilterKind = FormFieldKind


def _chip_parameter(kind: FilterKind) -> str | None:
    """The parameter a filter chip's control actually writes, or ``None``.

    ``State(key)`` names its slot and ``Filter(name)`` is the binding a decoder
    would have synthesised from the chip's own name, so both name a parameter in
    the same sense. Every other binding — ``Static`` above all — names none, and
    a chip carrying one is left entirely alone.
    """
    value = getattr(kind, "value", None)
    if isinstance(value, State):
        return value.key
    if isinstance(value, Filter):
        return value.name
    return None


@dataclass(frozen=True)
class FilterSpec:
    """One filter chip: a parameter name, a label, and the control that writes it.

    **``name`` must agree with the parameter the control binds.** A reference
    decoder auto-binds ``Filter(<name>)`` only where the control carries no
    ``value``, so on a chip whose control declares its own binding the name
    reaches nothing but the element id — and a name set apart from the bound
    parameter used to render, filter nothing, and say nothing. A disagreement is
    refused here, at the point the author wrote it, with both names in the
    message: which of the two is the mistake is the author's call.

    ``name=None`` DERIVES it from the binding. That is this surface's spelling of
    "omitted": ``name`` is the first positional field and reordering the record to
    give it a default would silently reinterpret every existing positional call as
    passing a label.
    """

    name: str | None
    label: TextSource
    kind: FilterKind

    def __post_init__(self) -> None:
        bound = _chip_parameter(self.kind)
        if self.name is None:
            if bound is None:
                raise ValueError(
                    "filter chip has no name and nothing to derive one from: its control binds no "
                    "parameter (a State or Filter binding names one; Static does not). Name the "
                    "chip explicitly, or bind its control to the parameter it filters."
                )
            object.__setattr__(self, "name", bound)
        elif bound is not None and bound != self.name:
            raise ValueError(
                f"filter chip is named {self.name!r} but its control binds the parameter "
                f"{bound!r}. A chip's name reaches nothing but the element id once the control "
                "declares its own binding, so the two must agree — rename the chip, rebind the "
                "control, or pass name=None to derive the name from the binding."
            )

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
    #: Phase 801 / Phase 806 — this column's OPT-OUT from the grid's declarative
    #: sort / edit affordance. Both are TRI-STATE for the same reason: absent
    #: defers to the grid, and an explicit ``False`` on one column of a sortable
    #: grid is the whole point of the slot (``grid-bound-sort``'s free-text note
    #: column, ``grid-declared-edit``'s read-only one).
    sortable: bool | None = None
    editable: bool | None = None

    def to_wire(self) -> Value:
        # Phase 460 — `format` / `width` omitted-when-default (`CellFormat.None`
        # / `ColumnWidth.Auto`); `_obj` drops the `None`-valued entries.
        return _obj(
            None,
            {
                "editable": self.editable,
                "field": self.field_name,
                "format": None if isinstance(self.format, FormatNone) else self.format,
                "kind": self.kind,
                "label": self.label,
                "sortable": self.sortable,
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
    #: Phase 801 — the DECLARATIVE sort. `sort_state_key` names the host State key
    #: the grid's live sort is read from and written back to, which is what makes
    #: the affordance survive the wire: a closure-sorted grid sorts nowhere a
    #: decoded document can see. `default_sort` is the order in force before the
    #: reader touches a header, and is meaningful without the key.
    sort_state_key: str | None = None
    default_sort: DefaultSort | None = None
    #: Phase 803 — declarative paging, the same shape: a page SIZE the author
    #: fixes, and the State key the current page number lives in.
    page_size: int | None = None
    page_state_key: str | None = None
    #: Phase 806 — the State key the grid's pending row edits accumulate in. It is
    #: what `editable` needs to mean anything after a round trip, and per-column
    #: `Column.editable` narrows it.
    edit_state_key: str | None = None
    #: fuaran#1123 — the reader may re-order rows by dragging them. Omitted at its
    #: `False` identity, like the behaviour flags above it.
    reorderable: bool = False

    def to_wire(self) -> Obj:
        # 0.2.0 — `editable` omitted-when-false; fuaran#1125 / #1473 and
        # `reorderable` join it on exactly those terms. Everything else declared
        # here rides only when the author named it, so a grid authored before any
        # of these slots existed encodes byte-for-byte as it did.
        return _obj(
            "DataGrid",
            {
                "columns": list(self.columns),
                "defaultSort": self.default_sort,
                "editStateKey": self.edit_state_key,
                "editable": True if self.editable else None,
                "exportable": True if self.exportable else None,
                "keepRowsTogether": True if self.keep_rows_together else None,
                "pageSize": self.page_size,
                "pageStateKey": self.page_state_key,
                "reorderable": True if self.reorderable else None,
                "repeatHeader": True if self.repeat_header else None,
                "rowKey": None if self.row_key_field is not None else CLOSURE,
                "rowKeyField": self.row_key_field,
                "sortStateKey": self.sort_state_key,
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
    """One case in a :class:`Switch`. EXACTLY ONE of ``match`` and ``when`` is set.

    ``match`` (Phase 392) renders ``child`` when the selector's string form equals
    it; ``when`` (fuaran#1535) renders ``child`` when the predicate resolves
    ``True``, consulting no selector at all. Both together and neither at all are
    decode errors (FUARAN142 pre-emit) — the Phase 818 ``value`` / ``valueFrom``
    shape, where the "exactly one" rule is policy rather than something the
    dataclass can express.
    """

    child: UiNode
    match: str | None = None
    #: A ``Binding<bool>`` — typed (the ``binding`` namespace) or structural, since
    #: several wire binding cases still have no typed constructor here.
    when: Binding | Value | None = None

    def __post_init__(self) -> None:
        # The XOR is enforced HERE rather than left to the decoder, because a
        # case built with neither used to reach the encoder as a bare
        # ``{"child":…}`` — valid JSON, refused by every conformant decoder
        # including this host's, and the author found out at the far end.
        if self.match is not None and self.when is not None:
            raise ValueError(
                "Switch case carries both 'match' and 'when' — exactly one is allowed: "
                "'match' (a literal string compared against the switch's selector) or "
                "'when' (a Binding<bool> predicate evaluated at render time)"
            )
        if self.match is None and self.when is None:
            raise ValueError(
                "Switch case carries neither 'match' nor 'when' — give it a literal string "
                "under 'match' (compared against the switch's selector), or a Binding<bool> "
                "under 'when' (a predicate evaluated at render time, needing no selector)"
            )

    def to_wire(self) -> Obj:
        # ``_obj`` drops ``None`` fields (wire rule 4) and lowers each value, so
        # the XOR needs no branching here: exactly one of the two is set, and the
        # other is omitted by the same mechanism every optional slot uses.
        return _obj(None, {"child": self.child, "match": self.match, "when": self.when})


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


@dataclass(frozen=True)
class GuestChannel:
    """A :class:`Mount`'s message channel — a direction, and optionally the guest's
    message shape, which is what the capability gate validates against."""

    direction: ChannelDirection = "OutOnly"
    message_shape: str | None = None

    def to_wire(self) -> Value:
        return _obj(None, {"direction": self.direction, "messageShape": self.message_shape})


@dataclass(frozen=True)
class Mount:
    """``Mount`` — the isolation / embedding boundary (WIRE_FORMAT §4o).

    A guest tree attaches under ``scope_id``, talks over ``channel``, and may do
    only what ``capabilities`` names — so ``capabilities`` is REQUIRED and its
    empty list is the meaningful default-deny document rather than an omission.

    ``inputs`` is the host→guest configuration map, sharing :data:`FragmentArg`
    with :class:`FragmentRef` (reuse, not a second vocabulary): the scalar cases
    carry config, and ``SlotArg`` carries a whole node tree as the guest's initial
    state. ``on_bubble`` follows the surface's handler-flag convention — ``True``
    (the default) emits the closure sentinel the corpus carries, ``False`` omits
    the key for a guest whose bubbles the host does not take.
    """

    scope_id: str
    channel: GuestChannel = field(default_factory=lambda: GuestChannel())
    capabilities: tuple[str, ...] = ()
    inputs: dict[str, FragmentArg] | None = None
    on_bubble: bool = True

    def to_wire(self) -> Obj:
        inputs = None
        if self.inputs is not None:
            inputs = Obj(None, {k: _lower(v) for k, v in self.inputs.items()})
        return _obj(
            "Mount",
            {
                "capabilities": Arr(list(self.capabilities)),
                "channel": self.channel,
                "inputs": inputs,
                "onBubble": CLOSURE if self.on_bubble else None,
                "scopeId": self.scope_id,
            },
        )


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
    #: Conditional presence (fuaran#1535) — a Binding[bool] whose resolved
    #: False removes this node from the rendered output ENTIRELY: no element,
    #: no placeholder, no aria-hidden, nothing in the layout and nothing in
    #: the accessibility tree. It is NOT accessibility.hidden, which is
    #: aria-hidden over a node that IS rendered. An absent, unresolved or
    #: errored predicate renders the node.
    visible: Value | None = None
    #: A short supplementary hint about this node, on the NODE rather than on any
    #: one kind — a tooltip is a trait of the thing pointed at, and every kind can
    #: be pointed at. A `TextSource`, so it can be bound or localised, and never a
    #: replacement for `accessibility.label`: a hint that is the only name a
    #: control has is a missing name, not a tooltip.
    tooltip: TextSource | None = None

    def to_wire(self) -> WireNode:
        extras: dict[str, Value] = {}
        if self.state is not None and not self.state.is_empty():
            extras["state"] = self.state.to_wire()
        if self.style is not None and not self.style.is_default():
            extras["style"] = self.style.to_wire()
        if self.accessibility is not None:
            extras["accessibility"] = self.accessibility.to_wire()
        if self.visible is not None:
            extras["visible"] = self.visible
        if self.tooltip is not None:
            extras["tooltip"] = _lower(self.tooltip)
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
