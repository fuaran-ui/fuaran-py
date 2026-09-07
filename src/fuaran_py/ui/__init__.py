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

from collections.abc import Sequence
from dataclasses import replace as _replace

from ..canonical import encode_value
from ..model import Obj
from ..schema import types as t
from ..schema.types import (
    Accessibility,
    Action,
    Binding,
    CellFormat,
    CompareRule,
    FieldRule,
    Kind,
    SemanticStyle,
    StateBehaviour,
    TextSource,
    UiNode,
)
from .capability import invoke as _invoke_record

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


#: What ``fuaran.switch(cases=…)`` accepts: a built case, or the ``(selector, child)``
#: pair whose selector is a literal ``match`` string or a ``when`` predicate binding.
SwitchCaseInput = t.SwitchCase | tuple[str, UiNode] | tuple[Binding, UiNode] | tuple[Obj, UiNode]


def _switch_case(entry: SwitchCaseInput) -> t.SwitchCase:
    """Coerce one ``fuaran.switch`` case entry to a :class:`~fuaran_py.schema.types.SwitchCase`.

    The selector's TYPE picks the wire member — a ``str`` is ``match``, a binding
    is ``when`` — and anything else is refused by name here. Refusing matters more
    than it looks: the pair used to be unpacked straight into ``match=``, so a
    predicate binding passed as the selector was written to the string slot and
    encoded there, producing a case no host can read and no error anyone can see.
    """
    if isinstance(entry, t.SwitchCase):
        return entry
    if not (isinstance(entry, tuple) and len(entry) == 2):
        raise TypeError(f"a switch case is a (selector, child) pair or a SwitchCase, and this is neither: {entry!r}")
    selector, child = entry
    if isinstance(selector, str):
        return t.SwitchCase(child=child, match=selector)
    if isinstance(
        selector, (t.Static, t.State, t.Filter, t.Selection, t.Now, t.FormatBinding, t.Local, t.Query, t.Invoke, Obj)
    ):
        return t.SwitchCase(child=child, when=selector)
    raise TypeError(
        "a switch case selector is either a literal 'match' string (compared against the "
        "switch's selector) or a Binding<bool> 'when' predicate (evaluated at render time), "
        f"and {type(selector).__name__} is neither"
    )


#: The characters the display-string parse keeps; everything else is decoration.
_METRIC_NUMERIC_CHARS = "0123456789.eE+-"

#: What ``metric(value=…)`` accepts, enumerated for the refusal message below —
#: the author cannot infer it, because the parse STRIPS before it reads.
_METRIC_VALUE_SHAPES = (
    "a number (42, 42.5, -7, 4.2e3); a Binding for a value the host resolves; "
    "or a display string whose decoration strips away to one of those "
    "('1,234' → 1234, '12.5%' → 12.5, '£42k' → 42, '$3.4M' → 3.4 — note a "
    "magnitude suffix is DROPPED, not applied)"
)


def _metric_value(value: str | t.NumberInput) -> Binding:
    """KPI value coercion: a number → ``Static``; a display string is leniently
    parsed (non-numeric characters stripped) into a ``Static`` — a convenience for
    prototypes like ``value="£42k"``; pass a number or a binding for precision.

    **A display string the strip leaves unparseable is REFUSED**, with the input
    and :data:`_METRIC_VALUE_SHAPES` in the message. It used to become ``0.0``:
    ``"n/a"`` strips to the empty string, ``"2026-09-06"`` strips to itself and
    still does not parse, and either way the tile rendered a confident zero the
    author never wrote. A KPI reading 0 is not a visibly-missing value — it is a
    number, and it is the one number a reader will act on.

    The refusal covers every unparseable strip, not only the digit-free case that
    was reported: they are one branch, and narrowing it would leave the docstring
    obliged to list a date as an accepted shape.
    """
    if isinstance(value, bool):  # guard: bool is an int subclass
        return t.Static(value)
    if isinstance(value, (int, float)):
        return t.Static(value)
    if isinstance(value, str):
        cleaned = "".join(c for c in value if c in _METRIC_NUMERIC_CHARS)
        try:
            parsed = float(cleaned)
        except ValueError:
            raise ValueError(
                f"metric value {value!r} is not a number: stripping decoration leaves "
                f"{cleaned!r}, which does not parse. Accepted: {_METRIC_VALUE_SHAPES}."
            ) from None
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
    def selection(node_id: str, *, field: str | None = None, default_value: t.Value = None) -> Binding:
        """``Binding.Selection`` — the last-selected row on ``node_id``; ``field``
        (Phase 632) projects a row property declaratively, ``default_value``
        (0.2.9) yields until the first selection."""
        return t.Selection(node_id, default_value, field)

    @staticmethod
    def now() -> Binding:
        """``Binding.Now`` — the host-furnished current instant (tag-only on the wire)."""
        return t.Now()

    @staticmethod
    def query(name: str, *depends_on: str) -> Binding:
        """``Binding.Query`` — a value the host resolves under a module-scoped
        ``name``, re-run when any of ``depends_on`` changes.

        The dependency names are VARIADIC because a query most often has none
        (``binding.query("orders")``) and the alternative — a keyword taking a
        sequence — makes the common call carry punctuation for a slot it does
        not use. Order is carried to the wire verbatim: the wire slot is a list,
        so two orders are two documents.
        """
        return t.Query(name, tuple(depends_on))

    @staticmethod
    def invoke(capability_id: str, **args: str) -> Binding:
        """``Binding.Invoke`` — a host-registered capability as a value SOURCE.

        The same record the ``action`` namespace's twin builds, because the wire
        shape is identical in the two positions; this is the spelling that reads
        right at a ``value=`` / ``source=`` slot. Argument values are strings on
        the wire (see :data:`~fuaran_py.ui.capability.InvokeArgValue`).
        """
        return _invoke_record(capability_id, **args)

    @staticmethod
    def i18n(key: str, **args: t.Value) -> t.TextSource:
        """``TextSource.I18n`` — a catalog key the reading host resolves in the
        reader's locale, with placeholder values.

        On the ``binding`` namespace rather than a namespace of its own because
        it is where an author looks for "a value the host resolves", which is
        what it is — but note the RESULT is a ``TextSource``, not a ``Binding``:
        it goes in a ``label`` / ``caption`` / ``tooltip`` slot, never in a
        ``value`` one.
        """
        return t.I18n(key, dict(args))

    @staticmethod
    def opaque() -> Binding:
        """A ``Static`` whose value the encoder cannot decompose (``"<opaque>"``)."""
        return t.Static(t.OPAQUE)

    @staticmethod
    def format(source: Binding, fmt: t.Format, locale: t.LocaleSource) -> Binding:
        """``Binding.Format`` — a locale-aware formatted value over a numeric source."""
        return t.FormatBinding(source, fmt, locale)

    @staticmethod
    def local(
        initial_from: Binding,
        flush_on: t.LocalFlushTrigger,
        *,
        commit_to: str | None = None,
        codec: CellFormat | None = None,
        on_commit: bool | None = None,
    ) -> Binding:
        """``Binding.Local`` — a component-scoped edit buffer (WIRE_FORMAT §3.3.3).

        Two commit spellings, exactly one per buffer. Named nothing: the handler
        spelling, ``onCommit``, which is what every call written before the
        declarative half existed means and still means. Named ``commit_to``: the
        State key the flush writes, optionally through a ``codec`` (only
        ``format.number(...)`` — the buffer has to parse back what it renders).
        Naming both is refused, because a document carrying both is a decode
        refusal rather than a precedence question.
        """
        return t.Local(initial_from, flush_on, commit_to=commit_to, codec=codec, on_commit=on_commit)


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
    def navigate(route: str, target: str = "Self") -> Action:
        """Navigate to ``route``. fuaran#1536 — ``route`` may be any
        ``TextSource`` (a bare string is the ``Literal`` shorthand and its
        canonical form), and ``target`` names the browsing context: ``"Self"``
        (the default, omitted on the wire) or ``"Blank"``, which every renderer
        opens with ``noopener,noreferrer``."""
        return t.Navigate(route, target)

    @staticmethod
    def set_state(key: str, value: t.Value) -> Action:
        return t.SetState(key, value)

    @staticmethod
    def set_state_from(key: str, value_from: t.Value) -> Action:
        """fuaran#818 — write a DERIVED value to the State channel: ``value_from``
        is a Binding (wire-shaped, e.g. a Selection field projection) evaluated
        at dispatch time inside the existing gate (value XOR valueFrom on the
        wire). Closes "set state from what the user clicked" without closures."""
        return t.SetState(key, value_from=value_from)

    @staticmethod
    def notify(channel: str, payload: t.Value) -> Action:
        return t.Notify(channel, payload)

    @staticmethod
    def print() -> Action:  # noqa: A003 — the wire case name
        """``Action.Print`` — open the reader's own print dialogue (fuaran#1124).

        It takes NOTHING, and the emptiness is the specification rather than an
        omission in it: every printing parameter belongs either to the host's page
        setup or to the dialogue the reader is looking at. Which subtrees stay
        whole on paper is a separate, independent statement — ``keep_together`` /
        ``break_before`` on a box and the grid's pair — because a printed page
        must be correct with no action having fired at all.
        """
        return t.Print()

    @staticmethod
    def write_to_clipboard(text: t.TextInput) -> Action:
        """``Action.WriteToClipboard`` — the payload is a ``TextSource`` (fuaran#1126).

        A bare string is still accepted and still encodes to the same bytes,
        because ``Literal``'s canonical form IS the bare JSON string. What is new
        is that a BOUND payload can reach the clipboard — a figure in the grid in
        front of the reader, a link the session holds — resolved at DISPATCH time,
        so what is copied is what the reader was looking at.
        """
        return t.WriteToClipboard(_text(text))

    @staticmethod
    def read_file_body(file_ref: str, encoding: t.FileReadEncoding = "Text") -> Action:
        """``Action.ReadFileBody`` — read a selected file's body; ``onRead`` is a closure."""
        return t.ReadFileBody(file_ref, encoding)

    @staticmethod
    def call(endpoint: str, *, into: t.CallResultTarget | None = None, on_result: bool = False) -> Action:
        """``Action.Call`` — ask a host endpoint.

        ``into`` is the declarative result target (:meth:`into_state` /
        :meth:`into_query`) and is the half a decoding host can honour;
        ``on_result`` declares that the EMITTING host holds a result closure,
        which crosses the wire only as the sentinel. Naming neither is the
        ordinary fire-and-forget submit, not an omission.
        """
        return t.Call(endpoint, into, on_result)

    @staticmethod
    def into_state(key: str) -> t.CallResultTarget:
        """A :meth:`call` result landing in the State channel under ``key``."""
        return t.IntoState(key)

    @staticmethod
    def into_query(name: str) -> t.CallResultTarget:
        """A :meth:`call` result landing in the query-results slot under ``name``."""
        return t.IntoQuery(name)

    @staticmethod
    def ai_tool(tool_name: str, args: t.Value) -> Action:
        """``Action.AiTool`` — invoke a named tool on the host's AI surface.

        ``args`` is written verbatim, as :meth:`notify`'s payload is. Not to be
        confused with :meth:`invoke`, which names a host-registered capability
        with typed string arguments validated against a declared signature.
        """
        return t.AiTool(tool_name, args)

    @staticmethod
    def invoke(capability_id: str, **args: str) -> Action:
        """``Action.Invoke`` — a host-registered capability as an EFFECT.

        The same record ``binding.invoke`` builds — the wire shape is identical
        in the two positions — spelled where an ``on_click=`` slot reads right.
        """
        return _invoke_record(capability_id, **args)


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

    @staticmethod
    def duration(unit: t.DurationUnit, style: t.DurationStyle) -> CellFormat:
        """Phase 819 — trendable duration cells (the raw float counts ``unit``s)."""
        return t.DurationFormat(unit, style)

    @staticmethod
    def relative_time(unit: t.RelativeTimeUnit) -> CellFormat:
        """Phase 819 — the English relative form ("3 minutes ago" / "in 2 hours")."""
        return t.RelativeTimeFormat(unit)


# ── Declared field constraints (the ``rule`` namespace, fuaran#864) ──────────


class rule:  # noqa: N801 — namespace object, mirrors the cross-tier `rule.*` slot
    """``FieldRule`` constructors — a form field's declared ACCEPTED SET.

    ``FormFieldKind`` names the control; this names what the control accepts. A
    rule that constrains nothing is a decode error rather than a no-op, so each
    constructor below fills at least one constraint slot, and ``message`` is only
    ever the prose shown when some other slot is unmet::

        t.FormField("work-email", t.LiteralText("Work email"), t.TextField(...),
                    required=True, rule=rule.format("email"))
    """

    @staticmethod
    def format(fmt: t.RuleFormat, message: t.TextInput | None = None) -> t.FieldRule:  # noqa: A003
        """One of the ``email`` / ``url`` / ``tel`` shorthands. Text controls only —
        the validator raises FUARAN100 where the control cannot honour it."""
        return t.FieldRule(format=fmt, message=_text(message) if message is not None else None)

    @staticmethod
    def pattern(source: str, message: t.TextInput | None = None) -> t.FieldRule:
        """ECMA-262 source with HTML ``pattern`` semantics — implicitly anchored
        to the whole value, so every host agrees without a second definition."""
        return t.FieldRule(pattern=source, message=_text(message) if message is not None else None)

    @staticmethod
    def length(
        minimum: int | None = None, maximum: int | None = None, message: t.TextInput | None = None
    ) -> t.FieldRule:
        """A character-length bound. An inverted pair (``minimum`` above
        ``maximum``) admits no value at all and is a decode error."""
        return t.FieldRule(
            min_length=minimum, max_length=maximum, message=_text(message) if message is not None else None
        )

    @staticmethod
    def compare(against: Binding, op: t.CompareOp, message: t.TextInput | None = None) -> t.FieldRule:
        """This field's value against another operand. Point ``against`` at a
        sibling field's id — ``binding.state("<field id>")`` — for the ordered-pair
        shapes; a literal operand duplicates a bound the control may already carry
        (FUARAN101)."""
        return t.FieldRule(compare=t.CompareRule(against, op), message=_text(message) if message is not None else None)


# ── Timed-text tracks (the ``track`` namespace, fuaran#1110) ─────────────────


class track:  # noqa: N801 — namespace object, mirrors the `rule.*` sub-record shape
    """``TrackEntry`` constructors, ONE PER ``TrackKind`` case.

    One constructor per case rather than a single one taking the kind as a
    string: the vocabulary is closed at four, so surfacing it as four names
    makes the closure visible where an author reads it and removes the spelling
    from what they have to get right. ``metadata`` is not among them, and its
    absence is the design — its cues are rendered by no user agent and read only
    by script, so a declarative document naming it would state an intent no
    conformant host could honour.

    All four take the same four values, and only ``default`` is optional::

        fuaran.video("walkthrough", src="/w.mp4", label="Studio walkthrough",
                     tracks=[track.captions(src="/w.en.vtt", src_lang="en",
                                            label="English captions", default=True)])

    Authored ORDER is carried to the wire verbatim and kept by the renderer — a
    reader picks a track from a menu the user agent builds in document order.
    """

    @staticmethod
    def _entry(kind: t.TrackKind, src: t.StringInput, src_lang: str, label: t.TextInput, default: bool) -> t.TrackEntry:
        return t.TrackEntry(kind, _text(label), _str_binding(src), src_lang, default)

    @staticmethod
    def subtitles(*, src: t.StringInput, src_lang: str, label: t.TextInput, default: bool = False) -> t.TrackEntry:
        """A translation of the dialogue, for a reader who can hear the audio."""
        return track._entry("Subtitles", src, src_lang, label, default)

    @staticmethod
    def captions(*, src: t.StringInput, src_lang: str, label: t.TextInput, default: bool = False) -> t.TrackEntry:
        """Dialogue plus the non-speech sound, for a reader who cannot hear it."""
        return track._entry("Captions", src, src_lang, label, default)

    @staticmethod
    def descriptions(*, src: t.StringInput, src_lang: str, label: t.TextInput, default: bool = False) -> t.TrackEntry:
        """A narration of what is shown, for a reader who cannot see it."""
        return track._entry("Descriptions", src, src_lang, label, default)

    @staticmethod
    def chapters(*, src: t.StringInput, src_lang: str, label: t.TextInput, default: bool = False) -> t.TrackEntry:
        """Named navigation points along the timeline."""
        return track._entry("Chapters", src, src_lang, label, default)


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
    modal = Accessibility(role="dialog")
    scroll_area = Accessibility(role="region")
    toast = Accessibility(role="status", live_region="polite")
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
    def with_tooltip(tooltip: t.TextInput, n: UiNode) -> UiNode:
        """Attach a supplementary hint to any node.

        A postfix modifier rather than a per-constructor keyword because the
        tooltip is a trait of the NODE, exactly as style and ARIA are: every kind
        can be pointed at, and threading one keyword through forty constructors
        would say otherwise. It is not a substitute for
        ``accessibility.label`` — a hint that is the only name a control has is a
        missing name.
        """
        return n.replace(tooltip=_text(tooltip))

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


#: The `Modal.on_dismiss` default — the no-op ``Action.Chain []`` every modal
#: authored before Phase 1576 emitted. A shared frozen instance so the parameter
#: default can be DISTINGUISHED from an explicit ``None``, which is how an author
#: declares no handler at all (the spelling the corpus's popovers carry).
_NO_OP_DISMISS = t.Chain()


def _node(id: str, kind: Kind, a11y: Accessibility | None = None) -> UiNode:  # noqa: A002
    return UiNode(id=id, kind=kind, accessibility=a11y)


class fuaran:  # noqa: N801 — namespace object, mirrors the cross-tier `fuaran.*` vocabulary
    """Element constructors. Each injects per-kind defaults + ARIA, exactly as the
    F#/TS smart constructors do; pass ``node.bare(...)`` to drop the ARIA trait."""

    # ── Layout ───────────────────────────────────────────────────────────────
    @staticmethod
    def dashboard(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        heading: t.TextInput | None = None,
    ) -> UiNode:
        kind = t.Dashboard(tuple(children or ()), _text(heading) if heading is not None else None)
        return _node(id, kind, accessibility.dashboard)

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
    def masonry_layout(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        cols: int = 3,
        gap: int | None = None,
    ) -> UiNode:
        """The masonry hang (WIRE_FORMAT §3.6.7) — children fill DOWN each column
        rather than across each row. The default is 3 columns rather than
        :meth:`grid_layout`'s 12: a masonry column is a real column of content,
        not a track in a fine-grained span grid."""
        return _node(id, t.MasonryLayoutBox(tuple(children or ()), cols, gap), accessibility.none)

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
        on_select: bool = True,
        on_select_tag: bool = False,
    ) -> UiNode:
        """A tab strip, over TWO independent selection channels (Phase 1576).

        ``on_select=False`` omits the index handler, which is what ARMS a
        renderer's write-back default: an ``active_index`` bound to
        ``State`` / ``Filter`` has the clicked index written to that slot.
        ``on_select_tag=True`` declares the tag channel's handler beside it.
        """
        idx = t.Static(active_index) if isinstance(active_index, int) else active_index
        kind = t.Tabs(
            tuple(children or ()),
            idx,
            orientation,
            active_tag,
            tuple(tab_headers) if tab_headers is not None else None,
            tuple(tab_tags) if tab_tags is not None else None,
            on_select,
            on_select_tag,
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
        on_select: bool = True,
    ) -> UiNode:
        step = t.Static(active_step) if isinstance(active_step, int) else active_step
        return _node(id, t.Stepper(tuple(children or ()), step, on_select), accessibility.none)

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
        on_toggle: bool = False,
    ) -> UiNode:
        op = t.Static(open) if isinstance(open, bool) else open
        return _node(
            id,
            t.Disclosure(tuple(children or ()), _text(heading), op, default_open, on_toggle),
            accessibility.disclosure,
        )

    @staticmethod
    def modal(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        open: Binding | bool = False,  # noqa: A002
        dismissable: bool = False,
        on_dismiss: Action | None = _NO_OP_DISMISS,
        heading: t.TextInput | None = None,
        modality: t.ModalityKind = "Blocking",
        anchor: str | None = None,
    ) -> UiNode:
        """A modal surface, in one of TWO modalities (fuaran#1119).

        `Blocking` is the default and the identity: it asserts the page behind it
        is INERT, and a host emits `aria-modal` to say so. `Popover` is the
        NON-BLOCKING anchored surface, and it carries the dialog role WITHOUT
        that claim — the page behind it is genuinely still available, and a host
        that claimed otherwise would tell assistive technology the rest of the
        page is unreachable when it is not.

        `on_dismiss=None` declares NO handler (Phase 1576), which is what arms a
        renderer's dismiss write-back default: a dismissable modal whose `open` is
        bound to `State` / `Filter` closes itself with no host code. Omitting the
        argument keeps the no-op `Chain` every pre-phase modal emitted.
        """
        op = t.Static(open) if isinstance(open, bool) else open
        kind = t.Modal(
            tuple(children or ()),
            op,
            dismissable,
            on_dismiss,
            _text(heading) if heading is not None else None,
            modality,
            anchor,
        )
        return _node(id, kind, accessibility.modal)

    @staticmethod
    def scroll_area(
        id: str,  # noqa: A002
        *,
        children: list[UiNode] | None = None,
        orientation: t.ScrollOrientation = "Vertical",
        max_height: int | None = None,
        max_width: int | None = None,
    ) -> UiNode:
        kind = t.ScrollArea(tuple(children or ()), orientation, max_height, max_width)
        return _node(id, kind, accessibility.scroll_area)

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
        trend_polarity: t.TrendPolarity = "HigherIsBetter",
    ) -> UiNode:
        # fuaran#867 — `trend_polarity` says which way the quantity IMPROVES;
        # `tone` says how the reading STANDS. Never derive one from the other:
        # a falling wait time on a tile the author deliberately toned `Warning`
        # is an improvement from a bad place, and one slot could not say both.
        kind = t.Metric(
            label=_text(label),
            value=_metric_value(value),
            format=format if format is not None else t.FormatNone(),
            tone=tone,
            weight=weight,
            emphasis=emphasis,
            icon=icon,
            subtext=_text(subtext) if subtext is not None else None,
            trend=_num_binding(trend) if trend is not None else None,
            trend_format=trend_format,
            trend_polarity=trend_polarity,
        )
        return _node(id, kind, accessibility.metric)

    @staticmethod
    def fact(
        id: str,  # noqa: A002
        *,
        label: t.TextInput,
        value: t.TextInput,
        tone: t.Tone = "Default",
        emphasis: bool = False,
        help: t.TextInput | None = None,  # noqa: A002
        icon: str | None = None,
    ) -> UiNode:
        """``Fact`` — the labelled TEXT statement beside :meth:`metric`'s number.

        ``value`` is a ``TextInput``, so a bare string is the literal and
        ``t.Bound(binding.now())`` / ``t.Bound(binding.selection(...))`` are the
        environment and master-detail readings the corpus carries. No ARIA is
        injected — the reference host builds it with ``Accessibility.none``, and
        a live region on a static statement would announce nothing.
        """
        kind = t.Fact(
            label=_text(label),
            value=_text(value),
            tone=tone,
            emphasis=emphasis,
            help=_text(help) if help is not None else None,
            icon=icon,
        )
        return _node(id, kind, accessibility.none)

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
            value=_num_binding(value),
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
        protection: t.LinkProtection | None = None,
    ) -> UiNode:
        """``protection`` declares how the emitting host must PROTECT the
        destination — today, that a ``mailto:`` address must not reach emitted
        HTML in plaintext (Phase 812)."""
        return _node(
            id,
            t.Link(_str_binding(href), _text(label), download, rel, target, protection),
            accessibility.none,
        )

    @staticmethod
    def image(
        id: str,  # noqa: A002
        *,
        src: t.StringInput,
        alt: t.TextInput,
        variant: t.ImageVariant = "Default",
        fit: t.ImageFit = "Natural",
        aspect_ratio: t.ImageAspect = "Natural",
        loading: t.ImageLoading = "Eager",
        caption: t.TextInput | None = None,
        src_set: Sequence[tuple[t.StringInput, int]] = (),
        expandable: bool = False,
    ) -> UiNode:
        """A picture. The six slots past ``variant`` are the fuaran#1077–1080
        additions, each at its identity default here so an authored image is
        byte-identical to what it was before they existed.

        ``src_set`` takes ``(src, width)`` pairs rather than constructed entries
        because the two members are both required and there is nothing else to
        say; the authored ORDER is carried to the wire verbatim, and the renderer
        sorts ascending by width at emission.
        """
        return _node(
            id,
            t.Image(
                _text(alt),
                _str_binding(src),
                variant,
                fit=fit,
                aspect_ratio=aspect_ratio,
                loading=loading,
                caption=None if caption is None else _text(caption),
                src_set=tuple(t.SrcSetEntry(_str_binding(s), w) for s, w in src_set),
                expandable=expandable,
            ),
            accessibility.none,
        )

    @staticmethod
    def embed(
        id: str,  # noqa: A002
        *,
        src: t.StringInput,
        title: t.TextInput,
        aspect_ratio: t.ImageAspect = "Natural",
        permissions: Sequence[t.EmbedPermission] = (),
    ) -> UiNode:
        """A third-party document in a maximally-sandboxed browsing context
        (fuaran#1111).

        `permissions` defaults to the EMPTY list, which is TOTAL DENIAL: the
        shortest call is the fully-sandboxed one and every relaxation is
        something a caller names. `title` is required — a frame is a focus
        container a reader tabs INTO, so it is never decorative.

        The source must be `https`: the embed egress class refuses every other
        scheme and every relative reference at RENDER time, because a same-origin
        frame is exactly where `AllowSameOrigin` plus `AllowScripts` lets the
        framed document remove its own sandbox.
        """
        return _node(
            id,
            t.Embed(_str_binding(src), _text(title), aspect_ratio, tuple(permissions)),
            accessibility.none,
        )

    @staticmethod
    def tree(
        id: str,  # noqa: A002
        *,
        items: Sequence[t.TreeItem] = (),
        expanded_state_key: str | None = None,
        selection_state_key: str | None = None,
        on_select: bool = False,
    ) -> UiNode:
        """A hierarchy of rows with TREE semantics (fuaran#1120).

        The property the kind exists for is ONE TAB STOP: exactly one visible row
        is in the sequential focus order and the arrow keys move within the
        widget. No `List` + `Disclosure` composition has it — a composition of
        independently focusable containers is N tab stops, and no arrangement of
        them produces one.

        There is no `expandable` and no `selectable` flag: a behaviour the reader
        drives is declared as a named State key the host both writes and reads.
        Naming no `expanded_state_key` renders the tree FULLY EXPANDED, which is
        an initial presentation without a reader-driven affordance — a legitimate
        shape, and the only reading under which such a tree shows its content.
        """
        return _node(
            id,
            t.Tree(tuple(items), expanded_state_key, selection_state_key, on_select),
            accessibility.none,
        )

    @staticmethod
    def video(
        id: str,  # noqa: A002
        *,
        src: t.StringInput,
        label: t.TextInput,
        controls: bool = True,
        loop: bool = False,
        autoplay: bool = False,
        poster: t.StringInput | None = None,
        tracks: Sequence[t.TrackEntry] = (),
        transcript: t.TextInput | None = None,
    ) -> UiNode:
        """A video transport (fuaran#1076).

        ``label`` is required and has no decorative case — a media element is a
        control a reader focuses, plays, pauses and seeks, and one with no
        accessible name is announced as "video" and nothing more. ``controls``
        defaults to TRUE because the accessible setting is what a document gets
        for free. Declaring ``autoplay`` means autoplay-with-muted at every
        conformant host; there is no separate muted knob to disagree with it.

        ``tracks`` takes constructed :class:`track` entries rather than tuples —
        five members is past what a positional tuple reads as, and the closed
        kind is better spelled by the constructor's name than by the caller.
        ``transcript`` renders as a disclosure BESIDE the transport, never as a
        child of it: a media element admits only source-ish children, so a
        transcript in there is fallback content a browser never shows.
        """
        return _node(
            id,
            t.Media(
                _str_binding(src),
                _text(label),
                kind=t.Video(autoplay=autoplay, poster=None if poster is None else _str_binding(poster)),
                controls=controls,
                loop=loop,
                tracks=tuple(tracks),
                transcript=None if transcript is None else _text(transcript),
            ),
            accessibility.none,
        )

    @staticmethod
    def audio(
        id: str,  # noqa: A002
        *,
        src: t.StringInput,
        label: t.TextInput,
        controls: bool = True,
        loop: bool = False,
        tracks: Sequence[t.TrackEntry] = (),
        transcript: t.TextInput | None = None,
    ) -> UiNode:
        """An audio transport (fuaran#1076).

        There is deliberately no ``autoplay`` parameter, and its absence is the
        design rather than an omission: the ``Audio`` variant declares no such
        slot in the type, on the wire, or in the emission.

        ``transcript`` (fuaran#1110) is on the SPEC rather than on the video
        case, and this is the arm that explains the placement: a recording with
        no visual channel has nowhere else to put its words, where a video can
        usually be served by captions riding the timeline it already has.
        """
        return _node(
            id,
            t.Media(
                _str_binding(src),
                _text(label),
                kind=t.Audio(),
                controls=controls,
                loop=loop,
                tracks=tuple(tracks),
                transcript=None if transcript is None else _text(transcript),
            ),
            accessibility.none,
        )

    @staticmethod
    def divider(id: str) -> UiNode:  # noqa: A002
        """A separator — a plain horizontal rule (Phase 459: the retired ``Divider``,
        now a ``Box`` with the ``Separator`` role). Renders
        ``<hr class="fuaran-layout-separator">``. For a labelled / vertical separator,
        author a :func:`box` with ``role="Separator"`` directly (mirrors ``Fuaran.divider``).
        """
        return _node(
            id,
            t.Box(children=(), layout=t.FlexLayout(direction="Horizontal", wrap=False), role="Separator"),
            accessibility.none,
        )

    @staticmethod
    def toast(
        id: str,  # noqa: A002
        *,
        message: t.TextInput,
        open: Binding | bool = False,  # noqa: A002
        tone: t.Tone = "Default",
        dismissable: bool = True,
    ) -> UiNode:
        op = t.Static(open) if isinstance(open, bool) else open
        return _node(id, t.Toast(_text(message), op, tone, dismissable), accessibility.toast)

    @staticmethod
    def code_block(
        id: str,  # noqa: A002
        *,
        code: str,
        language: str = "",
        copyable: bool = False,
        line_numbers: bool = False,
        highlight_lines: list[int] | None = None,
    ) -> UiNode:
        kind = t.CodeBlock(code, language, copyable, line_numbers, tuple(highlight_lines or ()))
        return _node(id, kind, accessibility.none)

    @staticmethod
    def math(id: str, source: str, *, display: t.MathDisplay = "Block") -> UiNode:  # noqa: A002
        return _node(id, t.Math(source, display), accessibility.none)

    @staticmethod
    def drawing(
        id: str,  # noqa: A002
        *,
        view_box: t.ViewBox,
        shapes: Sequence[t.Shape] = (),
        style: t.DrawStyle | None = None,
        title: t.TextInput | None = None,
        description: t.TextInput | None = None,
    ) -> UiNode:
        """``Drawing`` — placed geometry over a user-space ``view_box``.

        The shape vocabulary is closed and carries no raw SVG: it is the opposite
        of :meth:`custom`. Geometry is static (a chart lowering hands over
        concrete coordinates); colour and opacity stay bindable through
        :class:`~fuaran_py.schema.types.DrawStyle`.

        ``style`` is the ROOT style every shape inherits from and defaults to the
        empty one, which is what makes an empty drawing the two-key document
        rather than one carrying eleven nulls.
        """
        kind = t.Drawing(
            view_box=view_box,
            shapes=tuple(shapes),
            style=style if style is not None else t.DrawStyle(),
            title=_text(title) if title is not None else None,
            description=_text(description) if description is not None else None,
        )
        return _node(id, kind, accessibility.none)

    @staticmethod
    def sparkline(id: str, *, source: Binding) -> UiNode:  # noqa: A002
        return _node(id, t.Sparkline(source), accessibility.none)

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

    @staticmethod
    def icon(
        id: str,  # noqa: A002
        name: str,
        *,
        size: t.IconSize = "Medium",
        tone: t.Tone = "Default",
        label: str | None = None,
    ) -> UiNode:
        """Phase 821 — the standalone icon-only display kind. No ``label`` is
        decorative (``aria-hidden``); a ``label`` makes it meaningful
        (``role="img"`` + ``aria-label``)."""
        return _node(id, t.Icon(name, size, tone, label), accessibility.none)

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
        multiple: bool = False,
        values: Binding | None = None,
        on_change: bool = True,
        on_change_multi: bool = False,
    ) -> UiNode:
        """The select control. ``on_change=False`` OMITS the handler key, which is what
        arms a renderer's write-back default against ``value`` / ``values`` — see
        :class:`~fuaran_py.schema.types.Select`. ``on_change_multi`` is the MULTI
        channel's own handler (Phase 1576) and arms independently of it."""
        kind = t.Select(
            _text(label),
            source,
            value,
            _text(placeholder) if placeholder is not None else None,
            disabled,
            multiple,
            values,
            on_change,
            on_change_multi,
        )
        return _node(id, kind, accessibility.select)

    @staticmethod
    def file_upload(
        id: str,  # noqa: A002
        *,
        label: t.TextInput,
        accept: list[str] | None = None,
        multiple: bool = False,
        disabled: Binding | None = None,
        drop_target: bool = False,
        accept_paste: bool = False,
        capture: t.CaptureSource | None = None,
        destination: str | None = None,
        on_select: bool = True,
    ) -> UiNode:
        """The upload control, and its FOUR optional declarations.

        The first three are INGRESS ROUTES and each is ADDITIONAL: the picker and
        its label are emitted whatever is declared, because there is no keyboard
        equivalent of a drag and a control that replaced the picker would remove
        the only route some readers have. `capture` is the one that PRODUCES a
        file rather than moving one that already exists; it and `accept` are ONE
        statement, and nothing synthesises either from the other.

        `destination` is the fourth and the only one about what happens AFTER the
        selection: a NAME the host has registered with its own upload sink, never
        an address. Absent is the pre-1117 control — the selection reaches the
        handler and nothing leaves the client.
        """
        kind = t.FileUpload(
            _text(label),
            tuple(accept or ()),
            multiple,
            disabled,
            drop_target,
            accept_paste,
            capture,
            destination,
            on_select,
        )
        return _node(id, kind, accessibility.file_upload)

    @staticmethod
    def form(
        id: str,  # noqa: A002
        *,
        fields: list[t.FormField],
        on_submit: Action | None = None,
        submit_label: t.TextInput = "Submit",
        disabled: Binding | None = None,
    ) -> UiNode:
        kind = t.Form(tuple(fields), on_submit if on_submit is not None else t.Chain(), _text(submit_label), disabled)
        return _node(id, kind, accessibility.form)

    @staticmethod
    def filters(id: str, *, items: list[t.FilterSpec]) -> UiNode:  # noqa: A002
        return _node(id, t.Filters(tuple(items)), accessibility.none)

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
        subtitle: t.TextInput | None = None,
        x_title: t.TextInput | None = None,
        y_title: t.TextInput | None = None,
        value_format: t.Format | None = None,
        legend_position: t.ChartLegendPosition | None = None,
        data_labels: t.ChartDataLabels | None = None,
        x_scale: t.ChartXScale | None = None,
        annotations: Sequence[t.ChartAnnotation] | None = None,
    ) -> UiNode:
        """The eight slots past ``stacked`` are each ABSENT by default, so a chart
        authored before they existed encodes byte-for-byte as it did.

        ``annotations`` takes the three-member ``§4l`` union — ``t.ReferenceLine``
        / ``t.EventMarker`` / ``t.RangeBand`` — in document order; the pre-emit
        validator grounds each address against the chart's own rows.
        """
        spec = t.Chart(
            source,
            x_field,
            tuple(y_fields),
            kind,
            stacked,
            _text(title) if title is not None else None,
            value_format=value_format,
            x_title=_text(x_title) if x_title is not None else None,
            y_title=_text(y_title) if y_title is not None else None,
            subtitle=_text(subtitle) if subtitle is not None else None,
            legend_position=legend_position,
            data_labels=data_labels,
            x_scale=x_scale,
            annotations=tuple(annotations) if annotations is not None else None,
        )
        return _node(id, spec, accessibility.chart)

    @staticmethod
    def table(
        id: str,  # noqa: A002
        *,
        headers: list[t.TextInput],
        rows: list[list[t.TextInput]],
        sortable: bool | None = None,
        default_sort: t.DefaultSort | None = None,
    ) -> UiNode:
        """``sortable`` is TRI-STATE: absent is the pre-801 wire byte-for-byte,
        and an explicit ``False`` is an author declining the affordance rather
        than one who was never asked. ``default_sort`` names the header ORDINAL
        the table arrives sorted by, and is meaningful on its own."""
        spec = t.Table(
            tuple(_text(h) for h in headers),
            tuple(tuple(_text(c) for c in row) for row in rows),
            sortable=sortable,
            default_sort=default_sort,
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

    @staticmethod
    def grid(
        id: str,  # noqa: A002
        *,
        source: Binding,
        columns: list[t.Column] | None = None,
        editable: bool = False,
        row_key_field: str | None = None,
        sort_state_key: str | None = None,
        default_sort: t.DefaultSort | None = None,
        page_size: int | None = None,
        page_state_key: str | None = None,
        edit_state_key: str | None = None,
        reorderable: bool = False,
        transfer_out_key: str | None = None,
        transfer_in_key: str | None = None,
        exportable: bool = False,
        keep_rows_together: bool = False,
        repeat_header: bool = False,
    ) -> UiNode:
        """``row_key_field`` names the row property that identifies a row — the
        declarative sibling of the erased ``rowKey`` closure; pass it whenever the
        columns are ``field``-projected, so a decoded grid can key its rows.

        Every slot past it is a DECLARATIVE grid behaviour: one that survives the
        wire because it names a host State key rather than a closure. The
        constructor now reaches the whole record, so the ergonomic surface and the
        typed one admit the same documents.
        """
        return _node(
            id,
            t.DataGrid(
                source,
                tuple(columns or ()),
                editable,
                row_key_field,
                transfer_out_key=transfer_out_key,
                transfer_in_key=transfer_in_key,
                exportable=exportable,
                keep_rows_together=keep_rows_together,
                repeat_header=repeat_header,
                sort_state_key=sort_state_key,
                default_sort=default_sort,
                page_size=page_size,
                page_state_key=page_state_key,
                edit_state_key=edit_state_key,
                reorderable=reorderable,
            ),
            accessibility.grid,
        )

    # ── Structural ─────────────────────────────────────────────────────────────
    @staticmethod
    def custom(
        id: str,  # noqa: A002
        *,
        module_id: str,
        component_id: str,
        props: dict[str, t.Value] | None = None,
        content_hash: t.ContentHash | None = None,
        exposed_node_ids: list[str] | None = None,
    ) -> UiNode:
        kind = t.Custom(
            module_id=module_id,
            component_id=component_id,
            props=props if props is not None else {},
            content_hash=content_hash,
            exposed_node_ids=tuple(exposed_node_ids) if exposed_node_ids is not None else None,
        )
        return _node(id, kind, accessibility.none)

    @staticmethod
    def error_boundary(id: str, *, child: UiNode, fallback: UiNode) -> UiNode:  # noqa: A002
        return _node(id, t.ErrorBoundary(child, fallback), accessibility.none)

    @staticmethod
    def switch(  # noqa: A003
        id: str,  # noqa: A002
        *,
        cases: list[SwitchCaseInput],
        default: UiNode,
        state_key: str | None = None,
        on: Binding | None = None,
    ) -> UiNode:
        """Binding-selected conditional child (Phase 392 / 768). ``default``
        renders when no case is taken. Give exactly one selector: the compact
        ``state_key``, or ``on`` (any ``Binding`` — e.g. ``binding.selection`` to
        follow the clicked row).

        A case is a ``(selector, child)`` pair or a :class:`~fuaran_py.schema.types.SwitchCase`
        built by hand. In the pair, a ``str`` selector is the ``match`` value
        compared against the switch's selector; a ``Binding`` selector is the
        ``when`` predicate (fuaran#1535), evaluated at render time and consulting
        no selector at all — which is why ``switch-predicate-only`` carries an
        empty ``state_key``."""
        switch_cases = tuple(_switch_case(entry) for entry in cases)
        return _node(id, t.Switch(state_key, switch_cases, default, on), accessibility.none)

    @staticmethod
    def fragment_decl(
        id: str,  # noqa: A002
        *,
        name: str,
        body: UiNode,
        holes: list[t.HoleDecl] | None = None,
        effect: t.EffectClass | None = None,
    ) -> UiNode:
        kind = t.FragmentDecl(name, body, tuple(holes or ()), effect)
        return _node(id, kind, accessibility.none)

    @staticmethod
    def fragment_ref(
        id: str,  # noqa: A002
        *,
        name: str,
        args: dict[str, t.FragmentArg] | None = None,
    ) -> UiNode:
        return _node(id, t.FragmentRef(name, args), accessibility.none)

    @staticmethod
    def mount(
        id: str,  # noqa: A002
        *,
        scope_id: str,
        channel: t.GuestChannel | None = None,
        capabilities: Sequence[str] = (),
        inputs: dict[str, t.FragmentArg] | None = None,
        on_bubble: bool = True,
    ) -> UiNode:
        """``Mount`` — attach a guest tree behind an isolation boundary (§4o).

        ``capabilities`` is the whole of what the guest may do, so the default is
        the EMPTY list rather than an absent key: a mount that grants nothing is a
        document, and default-deny is the posture the boundary exists for. The
        channel likewise defaults to ``OutOnly`` — the guest can bubble, the host
        cannot reach in.

        ``inputs`` shares :data:`~fuaran_py.schema.types.FragmentArg` with
        :meth:`fragment_ref`: scalars carry configuration, and ``t.SlotArg(tree)``
        hands the guest a whole node tree as its initial state.
        """
        kind = t.Mount(
            scope_id=scope_id,
            channel=channel if channel is not None else t.GuestChannel(),
            capabilities=tuple(capabilities),
            inputs=inputs,
            on_bubble=on_bubble,
        )
        return _node(id, kind, accessibility.none)

    # ── Display (defined last: ``list`` shadows the ``list`` builtin used as a
    #     type annotation by the constructors above, so it sits at the class foot
    #     where no later annotation resolves the name) ──────────────────────────
    @staticmethod
    def list(  # noqa: A003 — wire kind name (cross-tier parity with F#/TS ``list``)
        id: str,  # noqa: A002
        *,
        items: list[t.TextInput] | None = None,
        ordered: bool = False,
    ) -> UiNode:
        return _node(id, t.List(tuple(_text(i) for i in (items or ())), ordered), accessibility.none)


# ── Encoding ─────────────────────────────────────────────────────────────────


def encode(n: UiNode) -> str:
    """Serialise a typed :class:`UiNode` to canonical wire JSON.

    Lowers the typed tree to the generic structural model and runs the proven
    canonical encoder — byte-identical to :func:`fuaran_py.encode_node` over the
    same tree, and to the wire-format corpus for trees that match it.
    """
    return encode_value(n.to_wire())


# ── Compute-layer authoring (Phase 285) — the polars-like surface ────────────
#
# Append-only: the ergonomic transform-pipeline DSL (``frame`` / ``col`` / ``lit`` /
# ``when``) that emits canonical ``Transform`` JSON, plus the Python capability
# host-registration seam. The wire codec lives in :mod:`fuaran_py.dataframe`.
from . import capability as capability  # noqa: E402, PLC0414 — append-only re-export at module foot

# fuaran#1160 — the terse, notebook-grade layer OVER this surface (title-first,
# records-in, ids derived). It composes the constructors defined above, so it is
# imported here at the module foot rather than at the head.
# fuaran#1170 — controls whose State slot feeds a Compute parameter. It composes both
# this module's constructors and the compute layer's declarations, so it too is imported
# at the module foot.
from . import controls as controls  # noqa: E402, PLC0414 — append-only re-export at module foot
from . import quick as quick  # noqa: E402, PLC0414 — append-only re-export at module foot
from .capability import invoke  # noqa: E402 — the Invoke wire ctor (Binding.Invoke / Action.Invoke)
from .compute import (  # noqa: E402 — append-only: kept below the existing surface (integration-lane convention)
    AggExpr,
    Expr,
    Frame,
    OptionSource,
    ParamDecl,
    TransformBinding,
    UnboundParamError,
    col,
    frame,
    lit,
    param,
    source_ref,
    transform,
    when,
)
from .controls import Control, control  # noqa: E402 — fuaran#1170, the control namespace

__all__ = [
    "fuaran",
    "binding",
    "action",
    "format",
    # fuaran#864 — the declared field-constraint vocabulary.
    "rule",
    "FieldRule",
    "CompareRule",
    # fuaran#1110 — the timed-text track constructors, one per closed kind.
    "track",
    "node",
    "accessibility",
    "encode",
    "UiNode",
    "Accessibility",
    "SemanticStyle",
    "StateBehaviour",
    # Compute-layer authoring (Phase 285)
    "frame",
    "col",
    "lit",
    "when",
    "transform",
    "source_ref",
    "Frame",
    "Expr",
    "AggExpr",
    "TransformBinding",
    "capability",
    "invoke",
    # The terse notebook layer (fuaran#1160)
    "quick",
    # Parameter-bound controls (fuaran#1170)
    "controls",
    "control",
    "Control",
    "param",
    "ParamDecl",
    "OptionSource",
    "UnboundParamError",
]
